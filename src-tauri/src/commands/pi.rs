//! Pi agent commands — live import / introspection.

use crate::error::AppError;
use crate::pi_config;
use crate::AppState;
use std::sync::Arc;
use tauri::State;

/// Provider names present in the Pi live models.json.
#[tauri::command]
pub fn get_pi_live_provider_ids() -> Result<Vec<String>, String> {
    let providers = pi_config::get_providers().map_err(|e| e.to_string())?;
    Ok(providers
        .keys()
        .filter(|name| {
            *name != pi_config::PI_PROXY_PROVIDER && !name.starts_with("cc-switch-")
        })
        .cloned()
        .collect())
}

/// A single Pi provider fragment from live config.
#[tauri::command]
pub fn get_pi_live_provider(name: String) -> Result<Option<serde_json::Value>, String> {
    let providers = pi_config::get_providers().map_err(|e| e.to_string())?;
    Ok(providers.get(&name).cloned())
}

/// Current defaultProvider from settings.json.
#[tauri::command]
pub fn get_pi_default_provider() -> Result<Option<String>, String> {
    pi_config::get_default_provider().map_err(|e| e.to_string())
}

/// Current defaultModel from settings.json.
#[tauri::command]
pub fn get_pi_default_model() -> Result<Option<String>, String> {
    pi_config::get_default_model().map_err(|e| e.to_string())
}

/// Import providers from Pi live models.json into the database.
#[tauri::command]
pub fn import_pi_providers_from_live(
    state: State<'_, Arc<AppState>>,
) -> Result<usize, String> {
    import_pi_from_live(state.inner().as_ref()).map_err(|e: AppError| e.to_string())
}

fn import_pi_from_live(state: &crate::store::AppState) -> Result<usize, AppError> {
    let providers = pi_config::get_providers()?;
    if providers.is_empty() {
        return Ok(0);
    }

    let existing_ids = state.db.get_provider_ids("pi")?;
    let proxy_owns_live = pi_config::is_proxy_takeover_active().unwrap_or(false);
    let mut imported = 0usize;
    let mut updated = 0usize;

    for (name, config) in providers {
        if name == pi_config::PI_PROXY_PROVIDER || name.starts_with("cc-switch-") {
            continue;
        }
        let base_url = config
            .get("baseUrl")
            .or_else(|| config.get("base_url"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if base_url.contains("/pi/v1") {
            continue;
        }

        // fail-closed 校验：缺 baseUrl / 空 models / 非 openai-completions 协议的
        // 条目导入后永远无法投影（无协议桥），跳过并告警，不做僵尸行
        if base_url.trim().is_empty() {
            log::warn!("Skip importing Pi provider '{name}': missing baseUrl");
            continue;
        }
        let models_empty = config
            .get("models")
            .and_then(|v| v.as_array())
            .map(|a| a.is_empty())
            .unwrap_or(true);
        if models_empty {
            log::warn!("Skip importing Pi provider '{name}': empty models list");
            continue;
        }
        let api = config
            .get("api")
            .and_then(|v| v.as_str())
            .unwrap_or("openai-completions");
        if api != "openai-completions" {
            log::warn!(
                "Skip importing Pi provider '{name}': api '{api}' has no proxy bridge support yet"
            );
            continue;
        }

        let display_name = config
            .get("name")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or(&name)
            .to_string();

        if existing_ids.contains(&name) {
            if proxy_owns_live {
                continue;
            }
            if let Ok(Some(mut provider)) = state.db.get_provider_by_id(&name, "pi") {
                // 再导入整体替换会抹掉 DB 侧 defaultModel（live 条目不含该键，
                // 它在 settings.json 顶层）——live 缺失时保留 DB 现值
                let mut config = config;
                if config.get("defaultModel").is_none() {
                    if let Some(dm) = provider.settings_config.get("defaultModel").cloned() {
                        config["defaultModel"] = dm;
                    }
                }
                provider.settings_config = config;
                provider.name = display_name;
                if let Err(e) = state.db.save_provider("pi", &provider) {
                    log::warn!("Failed to update Pi provider '{name}': {e}");
                    continue;
                }
                updated += 1;
            }
            continue;
        }

        let mut provider =
            crate::provider::Provider::with_id(name.clone(), display_name, config, None);
        provider.meta = Some(crate::provider::ProviderMeta {
            live_config_managed: Some(true),
            ..Default::default()
        });
        if let Err(e) = state.db.save_provider("pi", &provider) {
            log::warn!("Failed to import Pi provider '{name}': {e}");
            continue;
        }
        imported += 1;
    }

    if !proxy_owns_live {
        if let Ok(Some(default_provider)) = pi_config::get_default_provider() {
            if default_provider != pi_config::PI_PROXY_PROVIDER
                && !default_provider.starts_with("cc-switch-")
            {
                if state
                    .db
                    .get_provider_by_id(&default_provider, "pi")
                    .ok()
                    .flatten()
                    .is_some()
                {
                    let _ = state.db.set_current_provider("pi", &default_provider);
                    let _ = crate::settings::set_current_provider(
                        &crate::app_config::AppType::Pi,
                        Some(&default_provider),
                    );
                }
            }
        }
    }

    Ok(imported + updated)
}
