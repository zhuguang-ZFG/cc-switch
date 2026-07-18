mod support;

use cc_switch_lib::{
    kimi_config, update_settings, AppSettings, AppState, AppType, ConfigService, Database,
    MultiAppConfig, ProfileScope, ProfileService, Provider, ProviderService,
};
use serde_json::json;
use std::sync::Arc;

fn with_temp_kimi_dir<F: FnOnce(&std::path::Path)>(f: F) {
    let guard = support::test_mutex().lock().expect("test mutex poisoned");
    let home = support::ensure_test_home();
    support::reset_test_fs();

    let kimi_dir = home.join(".kimi-roundtrip");
    let _ = std::fs::remove_dir_all(&kimi_dir);
    std::fs::create_dir_all(&kimi_dir).expect("create temp Kimi Code dir");

    update_settings(AppSettings {
        kimi_config_dir: Some(kimi_dir.to_string_lossy().into_owned()),
        ..AppSettings::default()
    })
    .expect("set kimi_config_dir override");

    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| f(&kimi_dir)));

    let _ = update_settings(AppSettings::default());
    let _ = std::fs::remove_dir_all(&kimi_dir);
    drop(guard);

    if let Err(err) = result {
        std::panic::resume_unwind(err);
    }
}

#[test]
fn set_provider_preserves_unknown_provider_fields() {
    with_temp_kimi_dir(|dir| {
        let config_path = dir.join("config.toml");
        std::fs::write(
            &config_path,
            r#"default_model = "myhost/gpt-4"

[providers.myhost]
type = "openai"
base_url = "https://api.example.com/v1"
api_key = "sk-old"
rate_limit_delay = 0.5
key_env = "MY_API_KEY"
foo_bar = "keep-me-around"

[models."myhost/gpt-4"]
provider = "myhost"
model = "gpt-4"
max_context_size = 8192
"#,
        )
        .expect("seed config.toml");

        kimi_config::set_provider(
            "myhost",
            json!({
                "name": "myhost",
                "type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-new",
                "models": [{
                    "id": "gpt-4",
                    "alias": "myhost/gpt-4",
                    "max_context_size": 16384
                }]
            }),
        )
        .expect("set provider");

        let written = std::fs::read_to_string(&config_path).expect("read written config");
        assert!(written.contains("rate_limit_delay = 0.5"));
        assert!(written.contains("key_env = \"MY_API_KEY\""));
        assert!(written.contains("foo_bar = \"keep-me-around\""));
        assert!(written.contains("api_key = \"sk-new\""));
        assert!(written.contains("max_context_size = 16384"));
        assert!(!written.contains("sk-old"));
    });
}

#[test]
fn get_providers_returns_native_kimi_shape() {
    with_temp_kimi_dir(|dir| {
        std::fs::write(
            dir.join("config.toml"),
            r#"[providers.myhost]
type = "openai"
base_url = "https://api.example.com/v1"
api_key = "sk-test"

[models."myhost/gpt-4"]
provider = "myhost"
model = "gpt-4"
max_context_size = 8192
"#,
        )
        .expect("seed config.toml");

        let providers = kimi_config::get_providers().expect("get providers");
        let entry = providers.get("myhost").expect("myhost missing");
        assert_eq!(entry["type"], json!("openai"));
        assert_eq!(entry["base_url"], json!("https://api.example.com/v1"));
        assert_eq!(entry["models"][0]["id"], json!("gpt-4"));
        assert_eq!(entry["models"][0]["alias"], json!("myhost/gpt-4"));
        assert_eq!(entry["models"][0]["max_context_size"], json!(8192));
    });
}

#[test]
fn config_restore_projects_current_kimi_provider_to_live() {
    with_temp_kimi_dir(|dir| {
        let mut config = MultiAppConfig::default();
        let manager = config
            .get_manager_mut(&AppType::KimiCode)
            .expect("Kimi Code manager");
        manager.providers.insert(
            "restored".into(),
            Provider::with_id(
                "restored".into(),
                "Restored Kimi".into(),
                json!({
                    "type": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-test",
                    "models": [{
                        "id": "gpt-4.1",
                        "alias": "restored/gpt-4.1"
                    }]
                }),
                None,
            ),
        );
        manager.current = "restored".into();

        ConfigService::sync_current_providers_to_live(&mut config)
            .expect("project restored Kimi provider");

        let text =
            std::fs::read_to_string(dir.join("config.toml")).expect("read restored Kimi config");
        assert!(text.contains("[providers.restored]"));
        assert!(text.contains("default_model = \"restored/gpt-4.1\""));
    });
}

#[test]
fn profile_snapshots_and_restores_kimi_default_provider() {
    with_temp_kimi_dir(|_| {
        let db = Arc::new(Database::memory().expect("create profile database"));
        let state = AppState::new(db.clone());

        for id in ["project-a", "project-b"] {
            let settings = json!({
                "type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
                "models": [{
                    "id": "gpt-4.1",
                    "alias": format!("{id}/gpt-4.1")
                }]
            });
            db.save_provider(
                AppType::KimiCode.as_str(),
                &Provider::with_id(id.into(), id.into(), settings.clone(), None),
            )
            .expect("save Kimi provider");
            kimi_config::set_provider(id, settings).expect("write Kimi provider");
        }

        let first = db
            .get_provider_by_id("project-a", AppType::KimiCode.as_str())
            .expect("read first provider")
            .expect("first provider exists");
        kimi_config::apply_switch_defaults("project-a", &first.settings_config)
            .expect("select first provider");
        let profile = ProfileService::create(&state, "Project A", ProfileScope::KimiCode)
            .expect("create Kimi profile");

        let second = db
            .get_provider_by_id("project-b", AppType::KimiCode.as_str())
            .expect("read second provider")
            .expect("second provider exists");
        kimi_config::apply_switch_defaults("project-b", &second.settings_config)
            .expect("select second provider");

        let (warnings, _) = ProfileService::apply(&state, &profile.id, ProfileScope::KimiCode)
            .expect("apply Kimi profile");
        assert!(warnings.is_empty(), "unexpected warnings: {warnings:?}");
        assert_eq!(
            kimi_config::get_default_provider()
                .expect("read default provider")
                .as_deref(),
            Some("project-a")
        );
    });
}

#[test]
fn provider_service_switches_from_custom_back_to_managed_kimi() {
    with_temp_kimi_dir(|dir| {
        std::fs::write(
            dir.join("config.toml"),
            r#"default_model = "kimi-for-coding"

[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://api.kimi.com/coding/v1"

[providers."managed:kimi-code".oauth]
storage = "file"
key = "oauth/kimi-code"

[models."kimi-for-coding"]
provider = "managed:kimi-code"
model = "kimi-for-coding"
"#,
        )
        .expect("seed managed Kimi config");

        let db = Arc::new(Database::memory().expect("create provider database"));
        let state = AppState::new(db.clone());
        let managed = Provider::with_id(
            kimi_config::MANAGED_KIMI_PROVIDER.into(),
            "Kimi For Coding".into(),
            json!({
                "type": "kimi",
                "_cc_managed": true,
                "models": [{
                    "id": "kimi-for-coding",
                    "alias": "kimi-for-coding"
                }]
            }),
            None,
        );
        let custom = Provider::with_id(
            "custom".into(),
            "Custom Kimi".into(),
            json!({
                "type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
                "models": [{
                    "id": "gpt-4.1",
                    "alias": "custom/gpt-4.1"
                }]
            }),
            None,
        );
        db.save_provider(AppType::KimiCode.as_str(), &managed)
            .expect("save managed provider");
        db.save_provider(AppType::KimiCode.as_str(), &custom)
            .expect("save custom provider");

        ProviderService::switch(&state, AppType::KimiCode, "custom")
            .expect("switch to custom provider");
        assert_eq!(
            kimi_config::get_default_provider().unwrap().as_deref(),
            Some("custom")
        );
        assert_eq!(
            db.get_provider_by_id("custom", AppType::KimiCode.as_str())
                .unwrap()
                .unwrap()
                .meta
                .and_then(|meta| meta.live_config_managed),
            Some(true)
        );

        ProviderService::switch(
            &state,
            AppType::KimiCode,
            kimi_config::MANAGED_KIMI_PROVIDER,
        )
        .expect("switch back to managed provider");
        assert_eq!(
            kimi_config::get_default_provider().unwrap().as_deref(),
            Some(kimi_config::MANAGED_KIMI_PROVIDER)
        );

        let text =
            std::fs::read_to_string(dir.join("config.toml")).expect("read switched Kimi config");
        assert!(text.contains("[providers.\"managed:kimi-code\".oauth]"));
        assert!(text.contains("key = \"oauth/kimi-code\""));
    });
}
