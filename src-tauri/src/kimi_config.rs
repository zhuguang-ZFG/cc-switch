//! Kimi Code CLI configuration (`~/.kimi-code/config.toml`).
//!
//! Kimi Code stores long-lived preferences in TOML:
//! - top-level `default_model`
//! - `[providers.<name>]` with `type`, `base_url`, `api_key`, …
//! - `[models."<alias>"]` with `provider`, `model`, `max_context_size`, …
//!
//! CC Switch manages custom providers in additive mode: every DB provider is
//! projected into live `providers`/`models`, and switching only updates
//! `default_model`. Unrelated tables (`thinking`, `hooks`, `permission`, …)
//! are preserved via `toml_edit`.

use crate::config::{atomic_write, get_home_dir};
use crate::error::AppError;
use crate::settings::{effective_backup_retain_count, get_kimi_override_dir};
use chrono::Local;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::fs;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime};
use toml_edit::{Array, DocumentMut, InlineTable, Item, Table, Value as TomlEditValue};

pub const MANAGED_KIMI_PROVIDER: &str = "managed:kimi-code";
pub const KIMI_OAUTH_CREDENTIAL: &str = "kimi-code";
pub const KIMI_OAUTH_KEY: &str = "oauth/kimi-code";
pub const KIMI_API_BASE_URL: &str = "https://api.kimi.com/coding/v1";
pub const KIMI_OAUTH_CLIENT_ID: &str = "17e5f671-d194-4dfb-9706-5516cb48c098";
pub const DEFAULT_KIMI_OAUTH_HOST: &str = "https://auth.kimi.com";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KimiOAuthToken {
    pub access_token: String,
    pub refresh_token: String,
    pub expires_at: i64,
    #[serde(default)]
    pub scope: String,
    #[serde(default = "default_token_type")]
    pub token_type: String,
    #[serde(default)]
    pub expires_in: i64,
    /// W8: preserve unknown fields the CLI may add in future versions
    /// (e.g. new OAuth metadata) instead of silently dropping them on
    /// the read-modify-write round trip.
    #[serde(flatten)]
    pub extra: std::collections::BTreeMap<String, serde_json::Value>,
}

fn default_token_type() -> String {
    "Bearer".to_string()
}

pub fn get_kimi_oauth_host() -> String {
    std::env::var("KIMI_CODE_OAUTH_HOST")
        .or_else(|_| std::env::var("KIMI_OAUTH_HOST"))
        .unwrap_or_else(|_| DEFAULT_KIMI_OAUTH_HOST.to_string())
        .trim_end_matches('/')
        .to_string()
}

/// OS release string matching the CLI's `os.release()` (e.g. `10.0.26200` on
/// Windows, kernel version on Linux, product version on macOS). Resolved once
/// per process; falls back to the OS name when detection fails.
fn get_os_release() -> &'static str {
    static RELEASE: OnceLock<String> = OnceLock::new();
    RELEASE.get_or_init(|| {
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            if let Ok(output) = std::process::Command::new("cmd")
                .args(["/c", "ver"])
                .creation_flags(CREATE_NO_WINDOW)
                .output()
            {
                // "Microsoft Windows [Version 10.0.26200.1234]"
                let text = String::from_utf8_lossy(&output.stdout);
                if let Some(pos) = text.find("Version ") {
                    let version: String = text[pos + 8..]
                        .chars()
                        .take_while(|c| c.is_ascii_digit() || *c == '.')
                        .collect();
                    let version = version.trim_end_matches('.').to_string();
                    if !version.is_empty() {
                        return version;
                    }
                }
            }
        }
        #[cfg(target_os = "macos")]
        {
            if let Ok(output) = std::process::Command::new("sw_vers")
                .arg("-productVersion")
                .output()
            {
                let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if !version.is_empty() {
                    return version;
                }
            }
        }
        #[cfg(all(unix, not(target_os = "macos")))]
        {
            if let Ok(output) = std::process::Command::new("uname").arg("-r").output() {
                let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if !version.is_empty() {
                    return version;
                }
            }
        }
        std::env::consts::OS.to_string()
    })
}

/// Node-style arch label used by the CLI's device fingerprint.
fn node_style_arch() -> &'static str {
    match std::env::consts::ARCH {
        "x86_64" => "x64",
        "aarch64" => "arm64",
        "x86" => "ia32",
        other => other,
    }
}

pub fn get_kimi_device_headers() -> Result<reqwest::header::HeaderMap, String> {
    use reqwest::header::{HeaderMap, HeaderName, HeaderValue, USER_AGENT};

    let home = get_kimi_dir();
    std::fs::create_dir_all(&home).map_err(|error| error.to_string())?;
    let device_path = home.join("device_id");
    let device_id = std::fs::read_to_string(&device_path)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            let value = uuid::Uuid::new_v4().to_string();
            let _ = crate::config::atomic_write(&device_path, value.as_bytes());
            value
        });
    let version = env!("CARGO_PKG_VERSION");
    let device_name = std::env::var("COMPUTERNAME")
        .or_else(|_| std::env::var("HOSTNAME"))
        .unwrap_or_else(|_| "unknown".to_string());
    // Mirror the CLI's identity.ts fingerprint: os-version carries the real
    // release (e.g. 10.0.26200), device-model "<OS> <release> <arch>".
    let os_display = match std::env::consts::OS {
        "windows" => "Windows",
        "macos" => "Darwin",
        "linux" => "Linux",
        other => other,
    };
    let values = [
        ("x-msh-platform", "kimi_code_cli".to_string()),
        ("x-msh-version", version.to_string()),
        ("x-msh-device-name", device_name),
        (
            "x-msh-device-model",
            format!("{os_display} {} {}", get_os_release(), node_style_arch()),
        ),
        ("x-msh-os-version", get_os_release().to_string()),
        ("x-msh-device-id", device_id),
    ];
    let mut headers = HeaderMap::new();
    headers.insert(
        USER_AGENT,
        HeaderValue::from_str(&format!("cc-switch/{version}"))
            .map_err(|error| format!("Invalid Kimi User-Agent: {error}"))?,
    );
    for (name, value) in values {
        let value: String = value
            .chars()
            .filter(|character| character.is_ascii() && !character.is_ascii_control())
            .collect();
        headers.insert(
            HeaderName::from_bytes(name.as_bytes()).map_err(|error| error.to_string())?,
            HeaderValue::from_str(if value.is_empty() { "unknown" } else { &value })
                .map_err(|error| error.to_string())?,
        );
    }
    Ok(headers)
}

// ============================================================================
// Paths
// ============================================================================

/// Resolve Kimi Code home directory.
///
/// Order (aligned with Kimi Code docs):
/// 1. CC Switch settings override (`kimi_config_dir`)
/// 2. `KIMI_CODE_HOME` env (trimmed, non-empty; no `~` expansion)
/// 3. `~/.kimi-code`
pub fn get_kimi_dir() -> PathBuf {
    if let Some(override_dir) = get_kimi_override_dir() {
        return override_dir;
    }

    if let Some(raw) = std::env::var_os("KIMI_CODE_HOME") {
        let value = raw.to_string_lossy();
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }

    get_home_dir().join(".kimi-code")
}

/// Live config path: `<kimi_dir>/config.toml`.
pub fn get_kimi_config_path() -> PathBuf {
    get_kimi_dir().join("config.toml")
}

/// Return the exact live TOML text for lossless takeover backups.
pub fn read_config_text() -> Result<String, AppError> {
    let path = get_kimi_config_path();
    if !path.exists() {
        return Ok(String::new());
    }
    fs::read_to_string(&path).map_err(|e| AppError::io(&path, e))
}

/// Restore an exact TOML snapshot captured before proxy takeover.
pub fn write_config_text(text: &str) -> Result<(), AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;
    let path = get_kimi_config_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::io(parent, e))?;
    }
    atomic_write(&path, text.as_bytes())
}

/// Project Kimi Code onto the stable local Responses ingress while preserving
/// every unrelated TOML table and user-defined provider/model field.
pub fn apply_proxy_takeover(
    proxy_base_url: &str,
    api_key: &str,
) -> Result<KimiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;
    let mut doc = read_document()?;
    let providers = ensure_table_mut(&mut doc, "providers")?;
    let provider = ensure_nested_table_mut(providers, "cc-switch-proxy")?;
    provider.insert("type", Item::Value(TomlEditValue::from("openai_responses")));
    provider.insert("base_url", Item::Value(TomlEditValue::from(proxy_base_url)));
    provider.insert("api_key", Item::Value(TomlEditValue::from(api_key)));

    let models = ensure_table_mut(&mut doc, "models")?;
    let model = ensure_nested_table_mut(models, "cc-switch-proxy/default")?;
    model.insert(
        "provider",
        Item::Value(TomlEditValue::from("cc-switch-proxy")),
    );
    model.insert(
        "model",
        Item::Value(TomlEditValue::from("cc-switch-proxy-default")),
    );
    model.insert(
        "max_context_size",
        Item::Value(TomlEditValue::from(262144i64)),
    );
    doc["default_model"] = Item::Value(TomlEditValue::from("cc-switch-proxy/default"));
    write_document(&doc)
}

pub fn is_proxy_takeover_active() -> Result<bool, AppError> {
    let doc = read_document()?;
    let provider = doc
        .get("providers")
        .and_then(Item::as_table)
        .and_then(|providers| providers.get("cc-switch-proxy"))
        .and_then(Item::as_table);
    Ok(provider.is_some_and(|table| {
        // Align with Reasonix: require the managed placeholder key AND the
        // CC Switch ingress path. Bare localhost/127.0.0.1 without `/kimicode/`
        // must not count as takeover (user-owned local gateways).
        table_str(table, "api_key").as_deref() == Some("PROXY_MANAGED")
            && table_str(table, "base_url").is_some_and(|url| url.contains("/kimicode/"))
    }))
}

/// Remove only the CC Switch-owned projection if no backup is available.
pub fn clear_proxy_takeover() -> Result<KimiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;
    let mut doc = read_document()?;
    let mut changed = false;
    if let Some(providers) = doc.get_mut("providers").and_then(Item::as_table_mut) {
        changed |= providers.remove("cc-switch-proxy").is_some();
    }
    if let Some(models) = doc.get_mut("models").and_then(Item::as_table_mut) {
        changed |= models.remove("cc-switch-proxy/default").is_some();
    }
    if doc
        .get("default_model")
        .and_then(Item::as_str)
        .is_some_and(|value| value == "cc-switch-proxy/default")
    {
        if let Some(fallback) = doc
            .get("models")
            .and_then(Item::as_table)
            .and_then(|models| models.iter().next().map(|(alias, _)| alias.to_string()))
        {
            doc["default_model"] = Item::Value(TomlEditValue::from(fallback.as_str()));
        } else {
            doc.as_table_mut().remove("default_model");
        }
        changed = true;
    }
    if changed {
        write_document(&doc)
    } else {
        Ok(KimiWriteOutcome::default())
    }
}

pub fn get_kimi_credentials_path() -> PathBuf {
    get_kimi_dir()
        .join("credentials")
        .join(format!("{KIMI_OAUTH_CREDENTIAL}.json"))
}

fn write_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

// ============================================================================
// Write outcome / backup
// ============================================================================

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KimiWriteOutcome {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub backup_path: Option<String>,
}

fn backup_config_if_exists(path: &Path) -> Result<Option<String>, AppError> {
    if !path.exists() {
        return Ok(None);
    }
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    let stamp = Local::now().format("%Y%m%d_%H%M%S");
    let backup = parent.join(format!("config.toml.bak.{stamp}"));
    fs::copy(path, &backup).map_err(|e| AppError::io(path, e))?;

    // Prune old backups
    let retain = effective_backup_retain_count();
    if retain > 0 {
        if let Ok(entries) = fs::read_dir(parent) {
            let mut backups: Vec<PathBuf> = entries
                .filter_map(|e| e.ok())
                .map(|e| e.path())
                .filter(|p| {
                    p.file_name()
                        .and_then(|n| n.to_str())
                        .is_some_and(|n| n.starts_with("config.toml.bak."))
                })
                .collect();
            backups.sort();
            while backups.len() > retain {
                if let Some(old) = backups.first().cloned() {
                    let _ = fs::remove_file(&old);
                    backups.remove(0);
                } else {
                    break;
                }
            }
        }
    }

    Ok(Some(backup.to_string_lossy().to_string()))
}

// ============================================================================
// Document I/O
// ============================================================================

pub fn parse_document_text(text: &str) -> Result<DocumentMut, AppError> {
    if text.trim().is_empty() {
        return Ok(DocumentMut::new());
    }
    text.parse::<DocumentMut>().map_err(|e| {
        AppError::localized(
            "provider.kimicode.config.invalid_toml",
            format!("Kimi Code config.toml 格式错误: {e}"),
            format!("Invalid Kimi Code config.toml: {e}"),
        )
    })
}

pub fn read_document() -> Result<DocumentMut, AppError> {
    let path = get_kimi_config_path();
    if !path.exists() {
        return Ok(DocumentMut::new());
    }
    let text = fs::read_to_string(&path).map_err(|e| AppError::io(&path, e))?;
    parse_document_text(&text)
}

fn write_document(doc: &DocumentMut) -> Result<KimiWriteOutcome, AppError> {
    let path = get_kimi_config_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::io(parent, e))?;
    }
    let backup_path = backup_config_if_exists(&path)?;
    let text = doc.to_string();
    atomic_write(&path, text.as_bytes())?;
    Ok(KimiWriteOutcome { backup_path })
}

/// Run a read-modify-write cycle on the live TOML document under the write lock.
///
/// `mutate` returns whether it changed the document; the file (and its `.bak`
/// backup) is only written when it did. Prefer this over read_config_text →
/// mutate → write_config_text: the unlocked read there lets a concurrent
/// writer lose updates between the two calls.
pub fn update_document<F>(mutate: F) -> Result<KimiWriteOutcome, AppError>
where
    F: FnOnce(&mut DocumentMut) -> Result<bool, AppError>,
{
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;
    let mut doc = read_document()?;
    if !mutate(&mut doc)? {
        return Ok(KimiWriteOutcome::default());
    }
    write_document(&doc)
}

// ============================================================================
// Provider helpers
// ============================================================================

fn table_str(table: &Table, key: &str) -> Option<String> {
    table
        .get(key)
        .and_then(|item| item.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string)
}

fn ensure_table_mut<'a>(doc: &'a mut DocumentMut, key: &str) -> Result<&'a mut Table, AppError> {
    if doc.contains_key(key) && !doc[key].is_table() {
        // 形状异常（键存在但不是表）：报错中止，不能静默清空整个段
        return Err(AppError::Message(format!(
            "Kimi config.toml 的 [{key}] 段形状异常（非表），已中止写入以防清盘；请手动检查该文件"
        )));
    }
    if !doc.contains_key(key) {
        doc[key] = Item::Table(Table::new());
    }
    Ok(doc[key].as_table_mut().expect("just inserted table"))
}

fn ensure_nested_table_mut<'a>(parent: &'a mut Table, key: &str) -> Result<&'a mut Table, AppError> {
    if parent.contains_key(key) && !parent[key].is_table() {
        return Err(AppError::Message(format!(
            "Kimi config.toml 的条目 '{key}' 形状异常（非表），已中止写入以防清盘；请手动检查该文件"
        )));
    }
    if !parent.contains_key(key) {
        let mut t = Table::new();
        t.set_implicit(true);
        parent.insert(key, Item::Table(t));
    }
    Ok(parent
        .get_mut(key)
        .and_then(Item::as_table_mut)
        .expect("just inserted nested table"))
}

fn json_to_toml_value(value: &Value) -> Option<TomlEditValue> {
    match value {
        Value::Null => None,
        Value::Bool(value) => Some(TomlEditValue::from(*value)),
        Value::Number(value) => value
            .as_i64()
            .map(TomlEditValue::from)
            .or_else(|| {
                value
                    .as_u64()
                    .and_then(|v| i64::try_from(v).ok())
                    .map(TomlEditValue::from)
            })
            .or_else(|| value.as_f64().map(TomlEditValue::from)),
        Value::String(value) => Some(TomlEditValue::from(value.as_str())),
        Value::Array(values) => {
            let mut array = Array::new();
            for value in values {
                if let Some(value) = json_to_toml_value(value) {
                    array.push(value);
                }
            }
            Some(TomlEditValue::Array(array))
        }
        Value::Object(values) => {
            let mut table = InlineTable::new();
            for (key, value) in values {
                if let Some(value) = json_to_toml_value(value) {
                    table.insert(key, value);
                }
            }
            Some(TomlEditValue::InlineTable(table))
        }
    }
}

fn merge_json_object_into_table(
    table: &mut Table,
    object: &Map<String, Value>,
    skipped: &[&str],
) -> Result<(), AppError> {
    for (key, value) in object {
        if skipped.contains(&key.as_str()) {
            continue;
        }
        if value.is_null()
            || matches!(key.as_str(), "api_key" | "base_url")
                && value.as_str().is_some_and(|value| value.trim().is_empty())
        {
            table.remove(key);
            continue;
        }
        match value {
            Value::Object(object) => {
                let nested = ensure_nested_table_mut(table, key)?;
                merge_json_object_into_table(nested, object, &[])?;
            }
            _ => {
                if let Some(value) = json_to_toml_value(value) {
                    table.insert(key, Item::Value(value));
                }
            }
        }
    }
    Ok(())
}

fn provider_table_is_managed(name: &str, table: Option<&Table>) -> bool {
    // Match the CLI's notion of managed: the `managed:` name prefix or the
    // specific `oauth.key = "oauth/kimi-code"` credential reference. A custom
    // provider carrying some other user-configured `oauth` table is valid per
    // the CLI schema and must stay editable/removable.
    //
    // The `cc-switch-proxy` projection (api_key = PROXY_MANAGED) is also
    // protected: editing/removing it via UI breaks takeover-restore symmetry
    // (is_proxy_takeover_active flips false while the proxy still owns 15721).
    name.starts_with("managed:")
        || table.is_some_and(|table| {
            table
                .get("oauth")
                .and_then(Item::as_table)
                .and_then(|oauth| oauth.get("key"))
                .and_then(Item::as_str)
                == Some(KIMI_OAUTH_KEY)
        })
        || table.is_some_and(|table| {
            table.get("api_key").and_then(Item::as_str) == Some("PROXY_MANAGED")
        })
}

pub fn is_managed_provider(name: &str) -> Result<bool, AppError> {
    let doc = read_document()?;
    let table = doc
        .get("providers")
        .and_then(Item::as_table)
        .and_then(|providers| providers.get(name))
        .and_then(Item::as_table);
    // A reserved managed name is only considered managed once its live table
    // actually exists. set/remove still reject reserved names so only official
    // login provisioning can create them.
    Ok(table.is_some_and(|table| provider_table_is_managed(name, Some(table))))
}

fn managed_provider_error(name: &str) -> AppError {
    AppError::localized(
        "provider.kimicode.managed.read_only",
        format!("Kimi Code 托管供应商 '{name}' 只能通过官方登录管理"),
        format!("Kimi Code managed provider '{name}' can only be changed through official login"),
    )
}

/// Default model alias for a provider: first model alias or `name/default`.
pub fn first_model_alias(provider_name: &str, settings: &Value) -> Option<String> {
    let models = settings.get("models")?.as_array()?;
    let first = models.first()?;
    if let Some(alias) = first
        .get("alias")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        return Some(alias.to_string());
    }
    let id = first
        .get("id")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())?;
    Some(format!("{provider_name}/{id}"))
}

fn model_alias_for_entry(provider_name: &str, model: &Value) -> Option<String> {
    if let Some(alias) = model
        .get("alias")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        return Some(alias.to_string());
    }
    let id = model
        .get("id")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())?;
    Some(format!("{provider_name}/{id}"))
}

/// Read all custom providers from live config into UI/DB JSON shape.
pub fn get_providers() -> Result<Map<String, Value>, AppError> {
    let doc = read_document()?;
    let mut out = Map::new();

    let parsed: toml::Value = toml::from_str(&doc.to_string()).map_err(|e| {
        AppError::localized(
            "provider.kimicode.config.invalid_toml",
            format!("Kimi Code config.toml 格式错误: {e}"),
            format!("Invalid Kimi Code config.toml: {e}"),
        )
    })?;
    let root = serde_json::to_value(parsed)
        .map_err(|e| AppError::Message(format!("Failed to convert Kimi config: {e}")))?;
    let Some(providers) = root.get("providers").and_then(Value::as_object) else {
        return Ok(out);
    };

    // Collect models grouped by provider name
    let mut models_by_provider: Map<String, Value> = Map::new();
    if let Some(models) = root.get("models").and_then(Value::as_object) {
        for (alias, model) in models {
            let Some(mt) = model.as_object() else {
                continue;
            };
            let Some(provider) = mt.get("provider").and_then(Value::as_str) else {
                continue;
            };
            let mut entry = mt.clone();
            let model_id = mt
                .get("model")
                .and_then(Value::as_str)
                .unwrap_or(alias.as_str());
            entry.insert("id".into(), Value::String(model_id.to_string()));
            entry.insert("alias".into(), Value::String(alias.to_string()));
            let list = models_by_provider
                .entry(provider.to_string())
                .or_insert_with(|| Value::Array(vec![]));
            if let Some(arr) = list.as_array_mut() {
                arr.push(Value::Object(entry));
            }
        }
    }

    for (name, provider) in providers {
        let Some(mut obj) = provider.as_object().cloned() else {
            continue;
        };
        obj.insert("name".into(), Value::String(name.to_string()));
        if let Some(t) = obj.get("type").and_then(Value::as_str) {
            // Hermes form UI still uses api_mode; map for round-trip display.
            let api_mode = match t {
                "anthropic" => "anthropic_messages",
                "openai_responses" => "codex_responses",
                _ => "chat_completions",
            };
            obj.insert("api_mode".into(), Value::String(api_mode.into()));
        }
        let oauth_is_kimi_managed = obj
            .get("oauth")
            .and_then(Value::as_object)
            .and_then(|oauth| oauth.get("key"))
            .and_then(Value::as_str)
            == Some(KIMI_OAUTH_KEY);
        obj.insert(
            "_cc_managed".into(),
            Value::Bool(name.starts_with("managed:") || oauth_is_kimi_managed),
        );
        if let Some(models) = models_by_provider.get(name) {
            obj.insert("models".into(), models.clone());
        } else {
            obj.insert("models".into(), Value::Array(vec![]));
        }
        out.insert(name.to_string(), Value::Object(obj));
    }

    Ok(out)
}

/// Upsert a provider + its models into an in-memory TOML document.
///
/// Managed providers are rejected so official OAuth tables cannot be rewritten
/// from the simplified database projection.
fn upsert_provider_into_document(
    doc: &mut DocumentMut,
    name: &str,
    provider_config: &Value,
) -> Result<(), AppError> {
    let name = name.trim();
    if name.is_empty() {
        return Err(AppError::localized(
            "provider.kimicode.name.empty",
            "Kimi Code 供应商名称不能为空",
            "Kimi Code provider name cannot be empty",
        ));
    }

    let existing = doc
        .get("providers")
        .and_then(Item::as_table)
        .and_then(|providers| providers.get(name))
        .and_then(Item::as_table);
    if provider_table_is_managed(name, existing) {
        return Err(managed_provider_error(name));
    }
    let providers = ensure_table_mut(doc, "providers")?;
    let entry = ensure_nested_table_mut(providers, name)?;

    let object = provider_config.as_object().ok_or_else(|| {
        AppError::localized(
            "provider.kimicode.settings.not_object",
            "Kimi Code 供应商配置必须是对象",
            "Kimi Code provider settings must be an object",
        )
    })?;
    merge_json_object_into_table(
        entry,
        object,
        &["name", "models", "api_mode", "_cc_managed"],
    )?;

    // Legacy payloads may only carry api_mode. Native type always wins.
    if !object.contains_key("type") {
        if let Some(mode) = object.get("api_mode").and_then(Value::as_str) {
            let provider_type = match mode {
                "anthropic_messages" => "anthropic",
                "codex_responses" => "openai_responses",
                _ => "openai",
            };
            entry.insert("type", Item::Value(TomlEditValue::from(provider_type)));
        } else if !entry.contains_key("type") {
            entry.insert("type", Item::Value(TomlEditValue::from("openai")));
        }
    }

    if let Some(models) = provider_config.get("models").and_then(|v| v.as_array()) {
        let aliases: Vec<String> = models
            .iter()
            .filter_map(|model| model_alias_for_entry(name, model))
            .collect();
        if let Some(models_root) = doc.get_mut("models").and_then(Item::as_table_mut) {
            // W1: only sweep entries that look like pure cc-switch projections
            // (only the keys we project). Hand-authored models carrying extra
            // keys (capabilities, custom fields) are preserved — deleting them
            // on every provider save was destroying user data.
            const PROJECTION_KEYS: &[&str] =
                &["provider", "model", "display_name", "max_context_size"];
            let stale: Vec<String> = models_root
                .iter()
                .filter_map(|(alias, item)| {
                    let table = item.as_table()?;
                    let is_ours = table_str(table, "provider").as_deref() == Some(name);
                    let not_incoming = !aliases.iter().any(|incoming| incoming == alias);
                    let is_pure_projection =
                        table.iter().all(|(k, _)| PROJECTION_KEYS.contains(&k));
                    (is_ours && not_incoming && is_pure_projection).then(|| alias.to_string())
                })
                .collect();
            for alias in stale {
                models_root.remove(&alias);
            }
        }
        let models_root = ensure_table_mut(doc, "models")?;
        for model in models {
            let Some(alias) = model_alias_for_entry(name, model) else {
                continue;
            };
            let mt = ensure_nested_table_mut(models_root, &alias)?;
            if let Some(object) = model.as_object() {
                merge_json_object_into_table(
                    mt,
                    object,
                    &["id", "alias", "name", "context_length"],
                )?;
            }
            mt.insert("provider", Item::Value(TomlEditValue::from(name)));
            // Map the form's `name` to the CLI's `display_name` so custom
            // model display names survive the round trip (schema.ts maps
            // display_name → displayName).
            if let Some(display) = model
                .get("name")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
            {
                mt.insert("display_name", Item::Value(TomlEditValue::from(display)));
            }
            let model_id = model
                .get("id")
                .and_then(|v| v.as_str())
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .unwrap_or(alias.as_str());
            mt.insert("model", Item::Value(TomlEditValue::from(model_id)));
            // Kimi Code 0.27 schema requires models.*.max_context_size (min 1).
            // Prefer explicit values; fall back to an existing live value; then
            // a safe 256K default so CC Switch never writes a config the CLI
            // rejects with "must define a positive max_context_size".
            let ctx = model
                .get("max_context_size")
                .and_then(|v| v.as_i64())
                .or_else(|| {
                    model
                        .get("max_context_size")
                        .and_then(|v| v.as_u64())
                        .map(|u| u as i64)
                })
                .or_else(|| {
                    model
                        .get("context_length")
                        .and_then(|v| v.as_i64())
                        .or_else(|| {
                            model
                                .get("context_length")
                                .and_then(|v| v.as_u64())
                                .map(|u| u as i64)
                        })
                })
                .filter(|v| *v > 0)
                .or_else(|| {
                    table_str(mt, "max_context_size")
                        .and_then(|raw| raw.parse::<i64>().ok())
                        .filter(|v| *v > 0)
                })
                .unwrap_or(262_144);
            mt.insert("max_context_size", Item::Value(TomlEditValue::from(ctx)));
        }
    }

    Ok(())
}

/// Upsert a provider + its models into live config.toml.
pub fn set_provider(name: &str, provider_config: Value) -> Result<KimiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;

    let mut doc = read_document()?;
    upsert_provider_into_document(&mut doc, name, &provider_config)?;
    write_document(&doc)
}

/// Remove a provider and its models from an in-memory TOML document.
/// Returns whether anything changed.
fn remove_provider_from_document(doc: &mut DocumentMut, name: &str) -> Result<bool, AppError> {
    let name = name.trim();
    let existing = doc
        .get("providers")
        .and_then(Item::as_table)
        .and_then(|providers| providers.get(name))
        .and_then(Item::as_table);
    if provider_table_is_managed(name, existing) {
        return Err(managed_provider_error(name));
    }
    let mut changed = false;

    if let Some(providers) = doc.get_mut("providers").and_then(Item::as_table_mut) {
        if providers.remove(name).is_some() {
            changed = true;
        }
    }

    let mut removed_aliases = Vec::new();
    if let Some(models_tbl) = doc.get_mut("models").and_then(Item::as_table_mut) {
        let to_remove: Vec<String> = models_tbl
            .iter()
            .filter_map(|(alias, item)| {
                let t = item.as_table()?;
                let p = table_str(t, "provider")?;
                if p == name {
                    Some(alias.to_string())
                } else {
                    None
                }
            })
            .collect();
        for alias in to_remove {
            models_tbl.remove(&alias);
            removed_aliases.push(alias);
            changed = true;
        }
    }

    // Fix default_model if it pointed at a removed model.
    if let Some(default) = doc
        .get("default_model")
        .and_then(Item::as_str)
        .map(str::to_string)
    {
        if removed_aliases.iter().any(|a| a == &default) {
            // Prefer any remaining model alias
            let fallback = doc
                .get("models")
                .and_then(Item::as_table)
                .and_then(|t| t.iter().next().map(|(k, _)| k.to_string()));
            if let Some(fb) = fallback {
                doc["default_model"] = Item::Value(TomlEditValue::from(fb));
            } else {
                doc.as_table_mut().remove("default_model");
            }
            changed = true;
        }
    }

    Ok(changed)
}

/// Remove a provider from a full TOML snapshot text (proxy restore backup).
pub fn remove_provider_from_text(text: &str, name: &str) -> Result<String, AppError> {
    let mut doc = parse_document_text(text)?;
    let _ = remove_provider_from_document(&mut doc, name)?;
    Ok(doc.to_string())
}

/// Whether a `[providers.<name>]` table exists in the given config text.
/// Used to gate a provider-key rename against the takeover restore backup
/// (during takeover a provider added "to config" lives only in the backup).
pub fn provider_exists_in_text(text: &str, name: &str) -> Result<bool, AppError> {
    let doc = parse_document_text(text)?;
    Ok(doc
        .get("providers")
        .and_then(|item| item.as_table())
        .is_some_and(|providers| providers.contains_key(name)))
}

/// Remove a provider and all models that point at it.
pub fn remove_provider(name: &str) -> Result<KimiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;

    let mut doc = read_document()?;
    if !remove_provider_from_document(&mut doc, name)? {
        return Ok(KimiWriteOutcome::default());
    }
    write_document(&doc)
}

/// Project a provider switch into a TOML document.
///
/// - Custom providers are upserted from the DB projection.
/// - Managed OAuth providers only change `default_model` (table is login-owned).
/// - Unrelated tables/comments are preserved via toml_edit.
fn apply_switch_defaults_to_document(
    doc: &mut DocumentMut,
    provider_id: &str,
    settings_config: &Value,
) -> Result<(), AppError> {
    let name = provider_id.trim();
    let existing = doc
        .get("providers")
        .and_then(Item::as_table)
        .and_then(|providers| providers.get(name))
        .and_then(Item::as_table);
    let is_managed = provider_table_is_managed(name, existing);

    if !is_managed {
        upsert_provider_into_document(doc, name, settings_config)?;
    }

    if let Some(alias) = first_model_alias(name, settings_config) {
        doc["default_model"] = Item::Value(TomlEditValue::from(alias.as_str()));
    }
    Ok(())
}

/// Upsert a provider (+ models) into a TOML snapshot **without** changing
/// top-level `default_model`. Used by additive full-sync under proxy takeover
/// so bulk projection does not stomp the restore backup's routing default.
pub fn upsert_provider_into_text(
    text: &str,
    provider_id: &str,
    settings_config: &Value,
) -> Result<String, AppError> {
    let mut doc = parse_document_text(text)?;
    upsert_provider_into_document(&mut doc, provider_id.trim(), settings_config)?;
    Ok(doc.to_string())
}

/// Project switch defaults into a full TOML snapshot text.
///
/// Used by proxy hot-switch to update the restore backup's `default_model`
/// (and custom provider projection) while live stays on `cc-switch-proxy`.
pub fn apply_switch_defaults_to_text(
    text: &str,
    provider_id: &str,
    settings_config: &Value,
) -> Result<String, AppError> {
    let mut doc = parse_document_text(text)?;
    apply_switch_defaults_to_document(&mut doc, provider_id, settings_config)?;
    Ok(doc.to_string())
}

/// On switch: ensure provider is present and set `default_model` to first model alias.
pub fn apply_switch_defaults(
    provider_id: &str,
    settings_config: &Value,
) -> Result<KimiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;

    let mut doc = read_document()?;
    apply_switch_defaults_to_document(&mut doc, provider_id, settings_config)?;
    write_document(&doc)
}

/// Current default model alias from live config.
pub fn get_default_model() -> Result<Option<String>, AppError> {
    let doc = read_document()?;
    Ok(doc
        .get("default_model")
        .and_then(Item::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string))
}

pub fn get_default_provider() -> Result<Option<String>, AppError> {
    let doc = read_document()?;
    let Some(alias) = doc
        .get("default_model")
        .and_then(Item::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
    else {
        return Ok(None);
    };
    Ok(doc
        .get("models")
        .and_then(Item::as_table)
        .and_then(|models| models.get(alias))
        .and_then(Item::as_table)
        .and_then(|model| table_str(model, "provider")))
}

pub fn load_oauth_token() -> Result<Option<KimiOAuthToken>, AppError> {
    let path = get_kimi_credentials_path();
    if !path.exists() {
        return Ok(None);
    }
    let bytes = fs::read(&path).map_err(|e| AppError::io(&path, e))?;
    match serde_json::from_slice::<KimiOAuthToken>(&bytes) {
        Ok(token) if !token.access_token.is_empty() && !token.refresh_token.is_empty() => {
            Ok(Some(token))
        }
        _ => Ok(None),
    }
}

fn get_kimi_oauth_refresh_lock_path() -> PathBuf {
    get_kimi_dir().join(".oauth-refresh.lock")
}

/// Cross-process refresh lock so concurrent CC Switch / Kimi CLI instances do
/// not rotate the same refresh token twice. Uses an exclusive lock file with
/// stale recovery (mtime > OAUTH_LOCK_STALE_SECS).
/// Stale threshold for abandoned locks. Must exceed the OAuth HTTP timeout
/// (30s) plus margin so a live refresh cannot be stolen mid-flight.
const OAUTH_LOCK_STALE_SECS: u64 = 120;

struct OAuthRefreshFileLock {
    path: PathBuf,
    /// Kept open so the OS holds the inode while the lock is alive.
    #[allow(dead_code)]
    file: fs::File,
    heartbeat: Option<std::thread::JoinHandle<()>>,
    /// Dropping the sender disconnects the channel and wakes the heartbeat
    /// thread immediately, so Drop never blocks a tokio worker on a sleep.
    stop_tx: Option<std::sync::mpsc::Sender<()>>,
}

impl Drop for OAuthRefreshFileLock {
    fn drop(&mut self) {
        drop(self.stop_tx.take());
        if let Some(handle) = self.heartbeat.take() {
            let _ = handle.join();
        }
        let _ = fs::remove_file(&self.path);
    }
}

fn is_stale_lock_file(path: &Path) -> bool {
    let Ok(meta) = fs::metadata(path) else {
        return true;
    };
    let Ok(modified) = meta.modified() else {
        return true;
    };
    SystemTime::now()
        .duration_since(modified)
        .map(|age| age > Duration::from_secs(OAUTH_LOCK_STALE_SECS))
        .unwrap_or(true)
}

fn touch_lock_file(path: &Path) {
    // Bump mtime so other processes do not treat a long refresh as stale.
    let _ = OpenOptions::new().write(true).open(path).and_then(|f| {
        f.set_len(f.metadata()?.len())?;
        Ok(())
    });
    // Fallback for platforms where set_len does not update mtime reliably.
    if let Ok(content) = fs::read(path) {
        let _ = fs::write(path, content);
    }
}

fn try_acquire_oauth_refresh_file_lock() -> Result<OAuthRefreshFileLock, AppError> {
    let path = get_kimi_oauth_refresh_lock_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::io(parent, e))?;
    }
    // Must exceed the worst-case hold time of the retry loop in
    // ensure_fresh_oauth_token_with_expected (3 × 30s HTTP timeout + backoff),
    // otherwise a waiter errors out while the holder is still legitimately
    // retrying a 429/5xx storm — the waiter would then miss adopting the
    // holder's rotated token.
    let deadline = Instant::now() + Duration::from_secs(100);
    loop {
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(mut file) => {
                let _ = writeln!(file, "{}", std::process::id());
                let _ = file.flush();
                let (stop_tx, stop_rx) = std::sync::mpsc::channel::<()>();
                let heartbeat_path = path.clone();
                let heartbeat = std::thread::spawn(move || {
                    // recv_timeout returns Err(Disconnected) as soon as the
                    // lock drops its sender, so shutdown is immediate.
                    while let Err(std::sync::mpsc::RecvTimeoutError::Timeout) =
                        stop_rx.recv_timeout(Duration::from_secs(15))
                    {
                        touch_lock_file(&heartbeat_path);
                    }
                });
                return Ok(OAuthRefreshFileLock {
                    path,
                    file,
                    heartbeat: Some(heartbeat),
                    stop_tx: Some(stop_tx),
                });
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                if is_stale_lock_file(&path) {
                    let _ = fs::remove_file(&path);
                    continue;
                }
                if Instant::now() >= deadline {
                    return Err(AppError::Message(
                        "Timed out waiting for Kimi OAuth refresh lock; another process may be refreshing credentials"
                            .into(),
                    ));
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(error) => {
                return Err(AppError::io(&path, error));
            }
        }
    }
}

/// Restrict credentials file/dir to the current user on Windows (best-effort).
/// Uses `icacls` which is present on all supported Windows builds. Failure is
/// logged by the caller as non-fatal so login still succeeds offline.
#[cfg(windows)]
pub(crate) fn restrict_path_to_current_user(path: &Path) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    use std::process::Command;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let path_str = path.to_string_lossy();
    let user = std::env::var("USERNAME").map_err(|_| "USERNAME env is missing".to_string())?;
    // Reset inheritance and grant only the current user Full control.
    let status = Command::new("icacls")
        .args([
            path_str.as_ref(),
            "/inheritance:r",
            "/grant:r",
            &format!("{user}:F"),
        ])
        .creation_flags(CREATE_NO_WINDOW)
        .status()
        .map_err(|e| format!("icacls failed to start: {e}"))?;
    if !status.success() {
        return Err(format!("icacls exited with {status}"));
    }
    Ok(())
}

pub fn save_oauth_token(token: &KimiOAuthToken) -> Result<(), AppError> {
    let path = get_kimi_credentials_path();
    if let Some(parent) = path.parent() {
        // Restricting ACLs shells out to icacls on Windows (tens–hundreds of
        // ms). Only pay that on first creation: during a refresh race with
        // the real Kimi CLI every extra ms before the rotated token lands on
        // disk widens the window in which the peer clobbers it.
        let parent_created = !parent.exists();
        fs::create_dir_all(parent).map_err(|e| AppError::io(parent, e))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(parent, fs::Permissions::from_mode(0o700))
                .map_err(|e| AppError::io(parent, e))?;
        }
        #[cfg(windows)]
        {
            if parent_created {
                if let Err(error) = restrict_path_to_current_user(parent) {
                    log::warn!("Failed to restrict Kimi credentials directory ACL: {error}");
                }
            }
        }
        #[cfg(not(windows))]
        let _ = parent_created;
    }
    let mut bytes = serde_json::to_vec_pretty(token)
        .map_err(|e| AppError::Message(format!("Failed to serialize Kimi OAuth token: {e}")))?;
    bytes.push(b'\n');
    atomic_write(&path, &bytes)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))
            .map_err(|e| AppError::io(&path, e))?;
    }
    #[cfg(windows)]
    {
        if let Err(error) = restrict_path_to_current_user(&path) {
            log::warn!("Failed to restrict Kimi credentials file ACL: {error}");
        }
    }
    Ok(())
}

/// Return a usable managed Kimi access token, refreshing it when it is near
/// expiry. Combines an in-process mutex with a cross-process lock file so
/// concurrent proxy requests and multi-instance launches cannot race refresh
/// token rotation.
pub async fn ensure_fresh_oauth_token(force: bool) -> Result<Option<String>, AppError> {
    ensure_fresh_oauth_token_with_expected(force, None).await
}

/// Refresh after an upstream 401, but reuse a token another request already
/// rotated while this request was in flight. This prevents a burst of 401s
/// from consuming a rotated refresh token more than once.
pub async fn refresh_oauth_token_after_unauthorized(
    rejected_access_token: Option<&str>,
) -> Result<Option<String>, AppError> {
    ensure_fresh_oauth_token_with_expected(true, rejected_access_token).await
}

async fn ensure_fresh_oauth_token_with_expected(
    force: bool,
    expected_access_token: Option<&str>,
) -> Result<Option<String>, AppError> {
    // Fast path: this runs on every proxied Kimi request. When the token is
    // comfortably fresh, skip the in-process mutex AND the cross-process lock
    // file (fs create/delete + heartbeat thread) entirely — otherwise all
    // concurrent Kimi traffic serializes here, and a refresh held by another
    // process (up to 30s) would stall requests whose token is still valid.
    if !force {
        match load_oauth_token()? {
            Some(current) => {
                let now = chrono::Utc::now().timestamp();
                let threshold = 300_i64.max(current.expires_in.max(0) / 2);
                if current.expires_at.saturating_sub(now) > threshold {
                    return Ok(Some(current.access_token));
                }
            }
            None => return Ok(None),
        }
    }

    static REFRESH_LOCK: OnceLock<tokio::sync::Mutex<()>> = OnceLock::new();
    let lock = REFRESH_LOCK.get_or_init(|| tokio::sync::Mutex::new(()));
    let _guard = lock.lock().await;
    // Cross-process lock must be acquired after the in-process mutex so we do
    // not hold the file lock while waiting for other tasks in this process.
    let _file_lock = tokio::task::spawn_blocking(try_acquire_oauth_refresh_file_lock)
        .await
        .map_err(|e| AppError::Message(format!("OAuth refresh lock task failed: {e}")))??;

    let Some(current) = load_oauth_token()? else {
        return Ok(None);
    };
    if let Some(expected) = expected_access_token {
        if current.access_token != expected {
            return Ok(Some(current.access_token));
        }
    }
    let now = chrono::Utc::now().timestamp();
    let threshold = 300_i64.max(current.expires_in.max(0) / 2);
    if !force && current.expires_at.saturating_sub(now) > threshold {
        return Ok(Some(current.access_token));
    }
    if current.refresh_token.trim().is_empty() {
        return Err(AppError::Message(
            "Kimi OAuth token has expired and no refresh token is available; please log in again"
                .to_string(),
        ));
    }

    let client = reqwest::Client::builder()
        .default_headers(get_kimi_device_headers().map_err(AppError::Message)?)
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|error| {
            AppError::Message(format!("Failed to build Kimi OAuth client: {error}"))
        })?;
    // Mirror the official CLI: retry transient failures (429/5xx/transport)
    // with 1s/2s backoff; only auth rejections short-circuit.
    const RETRYABLE: [u16; 5] = [429, 500, 502, 503, 504];
    let mut attempt = 0usize;
    let (status, payload): (reqwest::StatusCode, Value) = loop {
        attempt += 1;
        let result = client
            .post(format!("{}/api/oauth/token", get_kimi_oauth_host()))
            .form(&[
                ("client_id", KIMI_OAUTH_CLIENT_ID),
                ("grant_type", "refresh_token"),
                ("refresh_token", current.refresh_token.as_str()),
            ])
            .send()
            .await;
        let retry_after = std::time::Duration::from_millis(1000 * (1 << (attempt - 1).min(4)));
        match result {
            Ok(response) => {
                let status = response.status();
                if RETRYABLE.contains(&status.as_u16()) && attempt < 3 {
                    tokio::time::sleep(retry_after).await;
                    continue;
                }
                let payload: Value = response.json().await.map_err(|error| {
                    AppError::Message(format!("Invalid Kimi OAuth refresh response: {error}"))
                })?;
                break (status, payload);
            }
            Err(error) if attempt < 3 => {
                log::warn!("Kimi OAuth refresh transport error (attempt {attempt}): {error}");
                tokio::time::sleep(retry_after).await;
                continue;
            }
            Err(error) => {
                return Err(AppError::Message(format!(
                    "Kimi OAuth refresh failed: {error}"
                )));
            }
        }
    };
    if !status.is_success() {
        let error_code = payload
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        // The real Kimi CLI shares this credentials file but uses a different
        // (or on Windows, no) cross-process lock, so a losing concurrent
        // refresh gets invalid_grant after the peer rotated the token.
        // Mirror the CLI's recovery: re-read the file and adopt the peer's
        // rotated token instead of surfacing an auth failure.
        if status.as_u16() == 401 || status.as_u16() == 403 || error_code == "invalid_grant" {
            tokio::time::sleep(std::time::Duration::from_millis(150)).await;
            if let Some(peer) = load_oauth_token()? {
                if peer.refresh_token != current.refresh_token {
                    log::info!(
                        "Kimi OAuth refresh lost a cross-process race; adopting peer-rotated token"
                    );
                    return Ok(Some(peer.access_token));
                }
            }
            // Genuine revocation (no peer rotated): write the CLI's revoked
            // tombstone so the Kimi CLI sharing this credentials file stops
            // burning the dead refresh token too (oauth-manager.ts:384-389).
            let tombstone = KimiOAuthToken {
                access_token: String::new(),
                refresh_token: String::new(),
                expires_at: 0,
                scope: String::new(),
                token_type: "Bearer".to_string(),
                expires_in: 0,
                extra: Default::default(),
            };
            match tokio::task::spawn_blocking(move || save_oauth_token(&tombstone)).await {
                Ok(Ok(())) => {}
                Ok(Err(error)) => {
                    log::warn!("Failed to write Kimi OAuth revoked tombstone: {error}")
                }
                Err(error) => {
                    log::warn!("Kimi OAuth tombstone task failed to join: {error}")
                }
            }
        }
        let detail = payload
            .get("error_description")
            .or_else(|| payload.get("message"))
            .or_else(|| payload.get("error"))
            .and_then(Value::as_str)
            .unwrap_or("authorization rejected");
        return Err(AppError::Message(format!(
            "Kimi OAuth refresh rejected (HTTP {}): {detail}",
            status.as_u16()
        )));
    }

    let access_token = payload
        .get("access_token")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            AppError::Message("Kimi OAuth refresh response missing access_token".into())
        })?;
    let refresh_token = payload
        .get("refresh_token")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(&current.refresh_token);
    let expires_in = payload
        .get("expires_in")
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
        .unwrap_or_else(|| current.expires_in.max(3600));
    let refreshed = KimiOAuthToken {
        access_token: access_token.to_string(),
        refresh_token: refresh_token.to_string(),
        expires_at: now.saturating_add(expires_in),
        scope: payload
            .get("scope")
            .and_then(Value::as_str)
            .unwrap_or(&current.scope)
            .to_string(),
        token_type: payload
            .get("token_type")
            .and_then(Value::as_str)
            .unwrap_or(&current.token_type)
            .to_string(),
        expires_in,
        // W8: carry forward unknown fields from the stored token; merge any
        // new unknown fields the refresh payload introduced.
        extra: {
            let mut extra = current.extra.clone();
            for (k, v) in payload.as_object().into_iter().flatten() {
                if !matches!(
                    k.as_str(),
                    "access_token" | "refresh_token" | "expires_at" | "expires_in" | "scope"
                        | "token_type"
                ) {
                    extra.insert(k.clone(), v.clone());
                }
            }
            extra
        },
    };
    // save_oauth_token shells out to icacls on Windows; keep it off the
    // async worker since this runs on the proxy request hot path.
    let to_save = refreshed.clone();
    tokio::task::spawn_blocking(move || save_oauth_token(&to_save))
        .await
        .map_err(|e| AppError::Message(format!("OAuth token save task failed: {e}")))??;
    Ok(Some(refreshed.access_token))
}

fn remove_managed_config_from_document(doc: &mut DocumentMut) -> bool {
    let mut changed = false;
    if let Some(providers) = doc.get_mut("providers").and_then(Item::as_table_mut) {
        changed |= providers.remove(MANAGED_KIMI_PROVIDER).is_some();
    }

    let mut removed_models = Vec::new();
    if let Some(models) = doc.get_mut("models").and_then(Item::as_table_mut) {
        removed_models = models
            .iter()
            .filter_map(|(alias, item)| {
                let table = item.as_table()?;
                (table_str(table, "provider").as_deref() == Some(MANAGED_KIMI_PROVIDER))
                    .then(|| alias.to_string())
            })
            .collect();
        for alias in &removed_models {
            models.remove(alias);
            changed = true;
        }
    }

    let default_is_managed = doc
        .get("default_model")
        .and_then(Item::as_str)
        .is_some_and(|alias| removed_models.iter().any(|removed| removed == alias));
    if default_is_managed {
        let fallback = doc
            .get("models")
            .and_then(Item::as_table)
            .and_then(|models| models.iter().next().map(|(alias, _)| alias.to_string()));
        if let Some(alias) = fallback {
            doc["default_model"] = Item::Value(TomlEditValue::from(alias.as_str()));
        } else {
            doc.as_table_mut().remove("default_model");
        }
        changed = true;
    }

    if let Some(services) = doc.get_mut("services").and_then(Item::as_table_mut) {
        changed |= services.remove("moonshot_search").is_some();
        changed |= services.remove("moonshot_fetch").is_some();
        if services.is_empty() {
            doc.as_table_mut().remove("services");
        }
    }
    changed
}

pub fn provision_managed_provider(models_payload: &Value) -> Result<KimiWriteOutcome, AppError> {
    let models = models_payload
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| AppError::Message("Unexpected Kimi models response".into()))?;
    if models.is_empty() {
        return Err(AppError::Message(
            "Kimi returned no available models".into(),
        ));
    }

    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;
    let mut doc = read_document()?;
    let previous_default = get_default_model()?;

    let providers = ensure_table_mut(&mut doc, "providers")?;
    let provider = ensure_nested_table_mut(providers, MANAGED_KIMI_PROVIDER)?;
    provider.insert("type", Item::Value(TomlEditValue::from("kimi")));
    provider.insert(
        "base_url",
        Item::Value(TomlEditValue::from(KIMI_API_BASE_URL)),
    );
    provider.insert("api_key", Item::Value(TomlEditValue::from("")));
    let oauth = ensure_nested_table_mut(provider, "oauth")?;
    oauth.insert("storage", Item::Value(TomlEditValue::from("file")));
    oauth.insert("key", Item::Value(TomlEditValue::from(KIMI_OAUTH_KEY)));

    let incoming_aliases: Vec<String> = models
        .iter()
        .filter_map(|model| model.get("id").and_then(Value::as_str))
        .map(|id| format!("kimi-code/{id}"))
        .collect();
    if incoming_aliases.is_empty() {
        return Err(AppError::Message(
            "Kimi returned no models with valid IDs".into(),
        ));
    }
    if let Some(root) = doc.get_mut("models").and_then(Item::as_table_mut) {
        let stale: Vec<String> = root
            .iter()
            .filter_map(|(alias, item)| {
                let table = item.as_table()?;
                (table_str(table, "provider").as_deref() == Some(MANAGED_KIMI_PROVIDER)
                    && !incoming_aliases.iter().any(|incoming| incoming == alias))
                .then(|| alias.to_string())
            })
            .collect();
        for alias in stale {
            root.remove(&alias);
        }
    }

    let root = ensure_table_mut(&mut doc, "models")?;
    for model in models {
        let Some(id) = model.get("id").and_then(Value::as_str) else {
            continue;
        };
        let context = model
            .get("context_length")
            .and_then(Value::as_i64)
            .filter(|value| *value > 0)
            .ok_or_else(|| {
                AppError::Message(format!("Kimi model '{id}' has no valid context_length"))
            })?;
        let alias = format!("kimi-code/{id}");
        let entry = ensure_nested_table_mut(root, &alias)?;
        entry.insert(
            "provider",
            Item::Value(TomlEditValue::from(MANAGED_KIMI_PROVIDER)),
        );
        entry.insert("model", Item::Value(TomlEditValue::from(id)));
        entry.insert(
            "max_context_size",
            Item::Value(TomlEditValue::from(context)),
        );

        let thinking_type = model.get("supports_thinking_type").and_then(Value::as_str);
        let supports_reasoning = model
            .get("supports_reasoning")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let mut capabilities = Vec::new();
        if matches!(thinking_type, Some("only" | "both"))
            || thinking_type.is_none() && supports_reasoning
        {
            capabilities.push("thinking");
        }
        if thinking_type == Some("only") {
            capabilities.push("always_thinking");
        }
        if model.get("supports_image_in").and_then(Value::as_bool) == Some(true) {
            capabilities.push("image_in");
        }
        if model.get("supports_video_in").and_then(Value::as_bool) == Some(true) {
            capabilities.push("video_in");
        }
        if model.get("supports_tool_use").and_then(Value::as_bool) != Some(false) {
            capabilities.push("tool_use");
        }
        let values = Value::Array(
            capabilities
                .into_iter()
                .map(|value| Value::String(value.to_string()))
                .collect(),
        );
        if let Some(value) = json_to_toml_value(&values) {
            entry.insert("capabilities", Item::Value(value));
        }

        if let Some(value) = model.get("display_name").and_then(Value::as_str) {
            entry.insert("display_name", Item::Value(TomlEditValue::from(value)));
        } else {
            entry.remove("display_name");
        }
        // Kimi Code 0.27's schema is z.literal("anthropic") for protocol; any
        // other value makes zod drop the whole model entry. Mirror the CLI's
        // parseModelProtocol: write it only when it is exactly "anthropic".
        if model.get("protocol").and_then(Value::as_str) == Some("anthropic") {
            entry.insert("protocol", Item::Value(TomlEditValue::from("anthropic")));
        } else {
            entry.remove("protocol");
        }
        let efforts = model.get("think_efforts").and_then(Value::as_object);
        if let Some(valid) = efforts.and_then(|value| value.get("valid_efforts")) {
            if let Some(value) = json_to_toml_value(valid) {
                entry.insert("support_efforts", Item::Value(value));
            }
        } else {
            entry.remove("support_efforts");
        }
        if let Some(value) = efforts
            .and_then(|value| value.get("default_effort"))
            .and_then(Value::as_str)
        {
            entry.insert("default_effort", Item::Value(TomlEditValue::from(value)));
        } else {
            entry.remove("default_effort");
        }
        let anthropic = model.get("protocol").and_then(Value::as_str) == Some("anthropic");
        if anthropic {
            entry.insert("beta_api", Item::Value(TomlEditValue::from(true)));
        } else {
            entry.remove("beta_api");
        }
        let adaptive = anthropic && matches!(thinking_type, Some("only" | "both"));
        if adaptive {
            entry.insert("adaptive_thinking", Item::Value(TomlEditValue::from(true)));
        } else {
            entry.remove("adaptive_thinking");
        }
    }

    let preserve_default = previous_default.as_deref().is_some_and(|alias| {
        !alias.starts_with("kimi-code/")
            || incoming_aliases.iter().any(|incoming| incoming == alias)
    });
    if !preserve_default {
        doc["default_model"] = Item::Value(TomlEditValue::from(incoming_aliases[0].as_str()));
    }

    let services = ensure_table_mut(&mut doc, "services")?;
    for (name, suffix) in [("moonshot_search", "search"), ("moonshot_fetch", "fetch")] {
        let service = ensure_nested_table_mut(services, name)?;
        service.insert(
            "base_url",
            Item::Value(TomlEditValue::from(
                format!("{KIMI_API_BASE_URL}/{suffix}").as_str(),
            )),
        );
        service.insert("api_key", Item::Value(TomlEditValue::from("")));
        let oauth = ensure_nested_table_mut(service, "oauth")?;
        oauth.insert("storage", Item::Value(TomlEditValue::from("file")));
        oauth.insert("key", Item::Value(TomlEditValue::from(KIMI_OAUTH_KEY)));
    }

    write_document(&doc)
}

pub fn logout_managed_provider() -> Result<KimiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;
    let path = get_kimi_credentials_path();
    if let Err(error) = fs::remove_file(&path) {
        if error.kind() != std::io::ErrorKind::NotFound {
            return Err(AppError::io(&path, error));
        }
    }
    let mut doc = read_document()?;
    if remove_managed_config_from_document(&mut doc) {
        write_document(&doc)
    } else {
        Ok(KimiWriteOutcome::default())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        body::Bytes,
        extract::State,
        http::{HeaderMap, StatusCode},
        response::IntoResponse,
        routing::post,
        Json, Router,
    };
    use futures::future::join_all;
    use serde_json::json;
    use serial_test::serial;
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc, Mutex,
    };

    #[derive(Default)]
    struct MockOAuthState {
        requests: AtomicUsize,
        forms: Mutex<Vec<(String, String)>>,
    }

    async fn mock_oauth_token(
        State(state): State<Arc<MockOAuthState>>,
        headers: HeaderMap,
        body: Bytes,
    ) -> impl IntoResponse {
        let request_number = state.requests.fetch_add(1, Ordering::SeqCst);
        state.forms.lock().unwrap().push((
            String::from_utf8_lossy(&body).into_owned(),
            headers
                .get("x-msh-platform")
                .and_then(|value| value.to_str().ok())
                .unwrap_or_default()
                .to_string(),
        ));

        match request_number {
            0 => (
                StatusCode::OK,
                Json(json!({
                    "access_token": "access-1",
                    "refresh_token": "refresh-rotated-1",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "coding"
                })),
            )
                .into_response(),
            1 => (
                StatusCode::OK,
                Json(json!({
                    "access_token": "access-2",
                    "refresh_token": "refresh-rotated-2",
                    "expires_in": 3600
                })),
            )
                .into_response(),
            _ => (
                StatusCode::UNAUTHORIZED,
                Json(json!({
                    "error": "invalid_grant",
                    "error_description": "refresh token is invalid"
                })),
            )
                .into_response(),
        }
    }

    fn with_temp_home<F: FnOnce(&Path)>(f: F) {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("home");
        fs::create_dir_all(home.join(".kimi-code")).unwrap();
        // Point via env override used by get_kimi_dir when settings override is None.
        // We set KIMI_CODE_HOME to our temp dir.
        let previous_home = std::env::var_os("KIMI_CODE_HOME");
        let previous_oauth_host = std::env::var_os("KIMI_CODE_OAUTH_HOST");
        std::env::set_var("KIMI_CODE_HOME", home.join(".kimi-code"));
        f(&home);
        match previous_home {
            Some(value) => std::env::set_var("KIMI_CODE_HOME", value),
            None => std::env::remove_var("KIMI_CODE_HOME"),
        }
        match previous_oauth_host {
            Some(value) => std::env::set_var("KIMI_CODE_OAUTH_HOST", value),
            None => std::env::remove_var("KIMI_CODE_OAUTH_HOST"),
        }
    }

    #[test]
    #[serial]
    fn update_document_writes_only_when_mutator_reports_change() {
        with_temp_home(|_home| {
            let path = get_kimi_config_path();
            fs::write(&path, "default_model = \"a/model\"\n").unwrap();

            // 无变更：不重写文件、不产生 .bak 备份
            let outcome = update_document(|_doc| Ok(false)).unwrap();
            assert!(outcome.backup_path.is_none());
            assert_eq!(
                fs::read_to_string(&path).unwrap(),
                "default_model = \"a/model\"\n"
            );
            let backups: Vec<_> = fs::read_dir(path.parent().unwrap())
                .unwrap()
                .filter_map(|entry| entry.ok())
                .filter(|entry| {
                    entry
                        .file_name()
                        .to_string_lossy()
                        .starts_with("config.toml.bak.")
                })
                .collect();
            assert!(
                backups.is_empty(),
                "no-change RMW must not create backups: {backups:?}"
            );

            // 有变更：单次落盘并产生备份
            let outcome = update_document(|doc| {
                doc["default_model"] = Item::Value(TomlEditValue::from("b/model"));
                Ok(true)
            })
            .unwrap();
            assert!(outcome.backup_path.is_some());
            assert!(fs::read_to_string(&path).unwrap().contains("b/model"));
        });
    }

    #[test]
    #[serial]
    fn set_provider_writes_providers_and_models() {
        with_temp_home(|_home| {
            let settings = json!({
                "name": "demo",
                "type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
                "models": [
                    { "id": "gpt-5.5", "max_context_size": 128000 }
                ]
            });
            set_provider("demo", settings).unwrap();
            let text = fs::read_to_string(get_kimi_config_path()).unwrap();
            assert!(text.contains("[providers.demo]"));
            assert!(text.contains("base_url = \"https://api.example.com/v1\""));
            assert!(
                text.contains("[models.\"demo/gpt-5.5\"]")
                    || text.contains("[models.demo/gpt-5.5]")
            );
            let providers = get_providers().unwrap();
            assert!(providers.contains_key("demo"));
        });
    }

    #[test]
    fn upsert_provider_into_text_preserves_default_model() {
        let original = r#"default_model = "a/model"

[thinking]
enabled = true

[providers.a]
type = "openai"
base_url = "https://a.example/v1"
api_key = "a-key"

[models."a/model"]
provider = "a"
model = "model"
"#;
        let settings = serde_json::json!({
            "type": "openai",
            "base_url": "https://b.example/v1",
            "api_key": "b-key",
            "models": [{ "id": "b-model", "alias": "b/b-model" }]
        });
        let updated = upsert_provider_into_text(original, "b", &settings)
            .expect("upsert into snapshot without switch");
        assert!(
            updated.contains("default_model = \"a/model\""),
            "bulk upsert must not stomp default_model: {updated}"
        );
        assert!(updated.contains("[providers.b]"));
        assert!(updated.contains("[thinking]"));
    }

    #[test]
    fn apply_switch_defaults_to_text_updates_default_without_touching_live() {
        let original = r#"default_model = "a/model"

[thinking]
enabled = true

[providers.a]
type = "openai"
base_url = "https://a.example/v1"
api_key = "a-key"

[models."a/model"]
provider = "a"
model = "model"
"#;
        let settings = serde_json::json!({
            "type": "openai",
            "base_url": "https://b.example/v1",
            "api_key": "b-key",
            "models": [{ "id": "b-model", "alias": "b/b-model" }]
        });
        let updated = apply_switch_defaults_to_text(original, "b", &settings)
            .expect("project switch into snapshot text");
        assert!(updated.contains("default_model = \"b/b-model\""));
        assert!(updated.contains("[providers.b]"));
        assert!(updated.contains("[thinking]"));
        assert!(updated.contains("enabled = true"));
        assert!(updated.contains("[providers.a]"));
    }

    #[test]
    #[serial]
    fn apply_switch_sets_default_model_and_preserves_other_tables() {
        with_temp_home(|_home| {
            let path = get_kimi_config_path();
            fs::write(
                &path,
                r#"
[thinking]
enabled = true
effort = "high"

[providers.other]
type = "openai"
api_key = "x"
"#,
            )
            .unwrap();

            let settings = json!({
                "type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
                "models": [{ "id": "m1" }]
            });
            apply_switch_defaults("demo", &settings).unwrap();
            let text = fs::read_to_string(path).unwrap();
            assert!(text.contains("default_model"));
            assert!(text.contains("demo/m1"));
            assert!(text.contains("[thinking]"));
            assert!(text.contains("effort = \"high\""));
            assert!(text.contains("[providers.other]"));
        });
    }

    #[test]
    #[serial]
    fn proxy_takeover_preserves_unrelated_toml_and_restores_exact_snapshot() {
        with_temp_home(|_home| {
            let original = r#"default_model = "demo/model"

[thinking]
enabled = true

[providers.demo]
type = "openai"
base_url = "https://example.test/v1"
api_key = "secret"

[models."demo/model"]
provider = "demo"
model = "model"
"#;
            fs::write(get_kimi_config_path(), original).unwrap();
            apply_proxy_takeover("http://127.0.0.1:15721/kimicode/v1", "PROXY_MANAGED").unwrap();
            assert!(is_proxy_takeover_active().unwrap());
            let taken = read_config_text().unwrap();
            assert!(taken.contains("[thinking]"));
            assert!(taken.contains("[providers.demo]"));
            assert!(taken.contains("default_model = \"cc-switch-proxy/default\""));
            write_config_text(original).unwrap();
            assert_eq!(read_config_text().unwrap(), original);
            assert!(!is_proxy_takeover_active().unwrap());
        });
    }

    #[test]
    #[serial]
    fn remove_provider_cleans_models_and_default() {
        with_temp_home(|_home| {
            let settings = json!({
                "type": "openai",
                "api_key": "sk",
                "models": [{ "id": "m1" }]
            });
            apply_switch_defaults("demo", &settings).unwrap();
            remove_provider("demo").unwrap();
            let text = fs::read_to_string(get_kimi_config_path()).unwrap();
            assert!(!text.contains("[providers.demo]"));
            assert!(!text.contains("demo/m1"));
        });
    }

    #[test]
    #[serial]
    fn managed_provider_cannot_be_overwritten_or_removed() {
        with_temp_home(|_home| {
            fs::write(
                get_kimi_config_path(),
                r#"[providers."managed:kimi-code".oauth]
storage = "file"
key = "oauth/kimi-code"

[models."kimi-for-coding"]
provider = "managed:kimi-code"
model = "kimi-for-coding"
"#,
            )
            .unwrap();
            assert!(set_provider(MANAGED_KIMI_PROVIDER, json!({"type": "openai"})).is_err());
            assert!(remove_provider(MANAGED_KIMI_PROVIDER).is_err());
        });
    }

    #[test]
    #[serial]
    fn reserved_managed_name_without_live_table_is_not_reported_as_managed() {
        with_temp_home(|_home| {
            assert!(!is_managed_provider(MANAGED_KIMI_PROVIDER).unwrap());
            assert!(set_provider(MANAGED_KIMI_PROVIDER, json!({"type": "openai"})).is_err());
        });
    }

    #[test]
    #[serial]
    fn managed_provider_switch_updates_default_without_rewriting_oauth_table() {
        with_temp_home(|_home| {
            fs::write(
                get_kimi_config_path(),
                r#"[providers."managed:kimi-code"]
type = "kimi"

[providers."managed:kimi-code".oauth]
storage = "file"
key = "oauth/kimi-code"

[models."kimi-for-coding"]
provider = "managed:kimi-code"
model = "kimi-for-coding"
"#,
            )
            .unwrap();

            apply_switch_defaults(
                MANAGED_KIMI_PROVIDER,
                &json!({
                    "type": "kimi",
                    "models": [{
                        "id": "kimi-for-coding",
                        "alias": "kimi-for-coding"
                    }]
                }),
            )
            .unwrap();

            let text = fs::read_to_string(get_kimi_config_path()).unwrap();
            assert!(text.contains("default_model = \"kimi-for-coding\""));
            assert!(text.contains("[providers.\"managed:kimi-code\".oauth]"));
            assert!(text.contains("key = \"oauth/kimi-code\""));
        });
    }

    #[test]
    #[serial]
    fn logout_managed_provider_falls_back_to_remaining_model() {
        with_temp_home(|_home| {
            fs::write(
                get_kimi_config_path(),
                r#"default_model = "kimi-for-coding"

[providers.custom]
type = "openai"

[providers."managed:kimi-code".oauth]
storage = "file"
key = "oauth/kimi-code"

[models."custom/gpt-4.1"]
provider = "custom"
model = "gpt-4.1"

[models."kimi-for-coding"]
provider = "managed:kimi-code"
model = "kimi-for-coding"
"#,
            )
            .unwrap();

            logout_managed_provider().unwrap();

            assert_eq!(
                get_default_model().unwrap().as_deref(),
                Some("custom/gpt-4.1")
            );
            assert_eq!(get_default_provider().unwrap().as_deref(), Some("custom"));
            let text = fs::read_to_string(get_kimi_config_path()).unwrap();
            assert!(!text.contains("managed:kimi-code"));
        });
    }

    #[test]
    #[serial]
    fn oauth_refresh_file_lock_is_exclusive_and_releases() {
        with_temp_home(|home| {
            let lock_path = home.join(".kimi-code").join(".oauth-refresh.lock");
            let first = try_acquire_oauth_refresh_file_lock().expect("first lock");
            assert!(lock_path.exists(), "lock file should exist while held");

            // Stale lock recovery: age the file past the 60s threshold.
            drop(first);
            // Hold lock in another "process" simulation by creating the file
            // without going through Drop, then verify acquire times out only
            // when the lock is fresh.
            fs::write(&lock_path, b"99999\n").expect("plant lock");
            // Fresh lock: spawn_blocking style acquire should fail fast path
            // after deadline — use a short direct attempt instead.
            assert!(
                OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&lock_path)
                    .is_err(),
                "create_new must fail while lock file exists"
            );
            fs::remove_file(&lock_path).expect("clear planted lock");

            let second = try_acquire_oauth_refresh_file_lock().expect("re-acquire");
            drop(second);
            assert!(!lock_path.exists(), "lock file must be removed on Drop");
        });
    }

    #[test]
    #[serial]
    fn empty_credentials_clear_existing_values() {
        with_temp_home(|_home| {
            fs::write(
                get_kimi_config_path(),
                r#"[providers.demo]
type = "openai"
api_key = "old"
base_url = "https://old.example/v1"
"#,
            )
            .unwrap();
            set_provider(
                "demo",
                json!({
                    "type": "openai",
                    "api_key": "",
                    "base_url": ""
                }),
            )
            .unwrap();
            let text = fs::read_to_string(get_kimi_config_path()).unwrap();
            assert!(!text.contains("api_key"));
            assert!(!text.contains("base_url"));
        });
    }

    #[test]
    #[serial]
    fn default_provider_resolves_from_model_reference() {
        with_temp_home(|_home| {
            fs::write(
                get_kimi_config_path(),
                r#"default_model = "alias"

[models.alias]
provider = "real-provider"
model = "real-model"
"#,
            )
            .unwrap();
            assert_eq!(
                get_default_provider().unwrap().as_deref(),
                Some("real-provider")
            );
        });
    }

    #[test]
    #[serial]
    fn oauth_token_uses_official_wire_format() {
        with_temp_home(|_home| {
            let token = KimiOAuthToken {
                access_token: "access".to_string(),
                refresh_token: "refresh".to_string(),
                expires_at: 123,
                scope: "".to_string(),
                token_type: "Bearer".to_string(),
                expires_in: 3600,
                    extra: Default::default(),
                };
            save_oauth_token(&token).unwrap();
            let value: Value =
                serde_json::from_slice(&fs::read(get_kimi_credentials_path()).unwrap()).unwrap();
            assert_eq!(value["access_token"], "access");
            assert_eq!(value["refresh_token"], "refresh");
            assert_eq!(load_oauth_token().unwrap().unwrap().expires_in, 3600);
        });
    }

    #[test]
    #[serial]
    fn oauth_refresh_mock_covers_rotation_coalescing_and_rejection() {
        with_temp_home(|_home| {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .expect("OAuth test runtime");
            runtime.block_on(async {
                let state = Arc::new(MockOAuthState::default());
                let app = Router::new()
                    .route("/api/oauth/token", post(mock_oauth_token))
                    .with_state(state.clone());
                let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
                    .await
                    .expect("mock oauth listener");
                let host = format!("http://{}", listener.local_addr().unwrap());
                let server = tokio::spawn(async move {
                    axum::serve(listener, app).await.expect("mock oauth server");
                });
                std::env::set_var("KIMI_CODE_OAUTH_HOST", &host);

                let now = chrono::Utc::now().timestamp();
                save_oauth_token(&KimiOAuthToken {
                    access_token: "access-valid".into(),
                    refresh_token: "refresh-valid".into(),
                    expires_at: now + 3600,
                    scope: "coding".into(),
                    token_type: "Bearer".into(),
                    expires_in: 3600,
                    extra: Default::default(),
                })
                .unwrap();
                assert_eq!(
                    ensure_fresh_oauth_token(false).await.unwrap().as_deref(),
                    Some("access-valid")
                );
                assert_eq!(state.requests.load(Ordering::SeqCst), 0);

                save_oauth_token(&KimiOAuthToken {
                    access_token: "access-expired".into(),
                    refresh_token: "refresh-1".into(),
                    expires_at: now - 1,
                    scope: String::new(),
                    token_type: "Bearer".into(),
                    expires_in: 3600,
                    extra: Default::default(),
                })
                .unwrap();
                assert_eq!(
                    ensure_fresh_oauth_token(false).await.unwrap().as_deref(),
                    Some("access-1")
                );
                assert_eq!(
                    load_oauth_token().unwrap().unwrap().refresh_token,
                    "refresh-rotated-1"
                );
                assert_eq!(state.requests.load(Ordering::SeqCst), 1);
                {
                    let forms = state.forms.lock().unwrap();
                    assert!(forms[0].0.contains("grant_type=refresh_token"));
                    assert!(forms[0].0.contains("refresh_token=refresh-1"));
                    assert_eq!(forms[0].1, "kimi_code_cli");
                }

                save_oauth_token(&KimiOAuthToken {
                    access_token: "access-expired-2".into(),
                    refresh_token: "refresh-2".into(),
                    expires_at: now - 1,
                    scope: String::new(),
                    token_type: "Bearer".into(),
                    expires_in: 3600,
                    extra: Default::default(),
                })
                .unwrap();
                let results = join_all((0..8).map(|_| ensure_fresh_oauth_token(false))).await;
                assert!(results
                    .iter()
                    .all(|result| result.as_ref().unwrap().as_deref() == Some("access-2")));
                assert_eq!(state.requests.load(Ordering::SeqCst), 2);

                assert_eq!(
                    refresh_oauth_token_after_unauthorized(Some("access-1"))
                        .await
                        .unwrap()
                        .as_deref(),
                    Some("access-2")
                );
                assert_eq!(state.requests.load(Ordering::SeqCst), 2);

                save_oauth_token(&KimiOAuthToken {
                    access_token: "access-expired-3".into(),
                    refresh_token: "refresh-3".into(),
                    expires_at: now - 1,
                    scope: String::new(),
                    token_type: "Bearer".into(),
                    expires_in: 3600,
                    extra: Default::default(),
                })
                .unwrap();
                let error = refresh_oauth_token_after_unauthorized(Some("access-expired-3"))
                    .await
                    .unwrap_err()
                    .to_string();
                assert!(error.contains("HTTP 401"));
                assert_eq!(state.requests.load(Ordering::SeqCst), 3);

                server.abort();
            });
        });
    }

    #[test]
    #[serial]
    #[ignore = "requires an explicit real Kimi OAuth account"]
    fn real_oauth_refresh_smoke() {
        assert_eq!(
            std::env::var("CC_SWITCH_RUN_REAL_KIMI_OAUTH").as_deref(),
            Ok("1"),
            "set CC_SWITCH_RUN_REAL_KIMI_OAUTH=1 explicitly"
        );
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("real OAuth smoke runtime");
        let access_token = runtime
            .block_on(ensure_fresh_oauth_token(false))
            .expect("real Kimi OAuth refresh")
            .expect("real Kimi OAuth credentials");
        let refreshed = load_oauth_token()
            .expect("load refreshed Kimi token")
            .expect("refreshed Kimi credentials");
        assert!(!access_token.is_empty());
        assert_eq!(access_token, refreshed.access_token);
        assert!(refreshed.expires_at > chrono::Utc::now().timestamp());
    }
}
