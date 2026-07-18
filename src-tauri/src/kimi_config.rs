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
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
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
    let values = [
        ("x-msh-platform", "kimi_code_cli".to_string()),
        ("x-msh-version", version.to_string()),
        ("x-msh-device-name", device_name),
        (
            "x-msh-device-model",
            format!("{} {}", std::env::consts::OS, std::env::consts::ARCH),
        ),
        ("x-msh-os-version", std::env::consts::OS.to_string()),
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

pub fn read_document() -> Result<DocumentMut, AppError> {
    let path = get_kimi_config_path();
    if !path.exists() {
        return Ok(DocumentMut::new());
    }
    let text = fs::read_to_string(&path).map_err(|e| AppError::io(&path, e))?;
    text.parse::<DocumentMut>().map_err(|e| {
        AppError::localized(
            "provider.kimicode.config.invalid_toml",
            format!("Kimi Code config.toml 格式错误: {e}"),
            format!("Invalid Kimi Code config.toml: {e}"),
        )
    })
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

fn ensure_table_mut<'a>(doc: &'a mut DocumentMut, key: &str) -> &'a mut Table {
    if !doc.contains_key(key) || !doc[key].is_table() {
        doc[key] = Item::Table(Table::new());
    }
    doc[key].as_table_mut().expect("just inserted table")
}

fn ensure_nested_table_mut<'a>(parent: &'a mut Table, key: &str) -> &'a mut Table {
    if !parent.contains_key(key) || !parent[key].is_table() {
        let mut t = Table::new();
        t.set_implicit(true);
        parent.insert(key, Item::Table(t));
    }
    parent
        .get_mut(key)
        .and_then(Item::as_table_mut)
        .expect("just inserted nested table")
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

fn merge_json_object_into_table(table: &mut Table, object: &Map<String, Value>, skipped: &[&str]) {
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
                let nested = ensure_nested_table_mut(table, key);
                merge_json_object_into_table(nested, object, &[]);
            }
            _ => {
                if let Some(value) = json_to_toml_value(value) {
                    table.insert(key, Item::Value(value));
                }
            }
        }
    }
}

fn provider_table_is_managed(name: &str, table: Option<&Table>) -> bool {
    name.starts_with("managed:") || table.is_some_and(|table| table.contains_key("oauth"))
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
        obj.insert(
            "_cc_managed".into(),
            Value::Bool(name.starts_with("managed:") || obj.contains_key("oauth")),
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

/// Upsert a provider + its models into live config.toml.
pub fn set_provider(name: &str, provider_config: Value) -> Result<KimiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;

    let name = name.trim();
    if name.is_empty() {
        return Err(AppError::localized(
            "provider.kimicode.name.empty",
            "Kimi Code 供应商名称不能为空",
            "Kimi Code provider name cannot be empty",
        ));
    }

    let mut doc = read_document()?;
    let existing = doc
        .get("providers")
        .and_then(Item::as_table)
        .and_then(|providers| providers.get(name))
        .and_then(Item::as_table);
    if provider_table_is_managed(name, existing) {
        return Err(managed_provider_error(name));
    }
    let providers = ensure_table_mut(&mut doc, "providers");
    let entry = ensure_nested_table_mut(providers, name);

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
    );

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
            let stale: Vec<String> = models_root
                .iter()
                .filter_map(|(alias, item)| {
                    let table = item.as_table()?;
                    (table_str(table, "provider").as_deref() == Some(name)
                        && !aliases.iter().any(|incoming| incoming == alias))
                    .then(|| alias.to_string())
                })
                .collect();
            for alias in stale {
                models_root.remove(&alias);
            }
        }
        let models_root = ensure_table_mut(&mut doc, "models");
        for model in models {
            let Some(alias) = model_alias_for_entry(name, model) else {
                continue;
            };
            let mt = ensure_nested_table_mut(models_root, &alias);
            if let Some(object) = model.as_object() {
                merge_json_object_into_table(
                    mt,
                    object,
                    &["id", "alias", "name", "context_length"],
                );
            }
            mt.insert("provider", Item::Value(TomlEditValue::from(name)));
            let model_id = model
                .get("id")
                .and_then(|v| v.as_str())
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .unwrap_or(alias.as_str());
            mt.insert("model", Item::Value(TomlEditValue::from(model_id)));
            if let Some(ctx) = model
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
            {
                mt.insert("max_context_size", Item::Value(TomlEditValue::from(ctx)));
            }
        }
    }

    write_document(&doc)
}

/// Remove a provider and all models that point at it.
pub fn remove_provider(name: &str) -> Result<KimiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;

    let name = name.trim();
    let mut doc = read_document()?;
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

    if !changed {
        return Ok(KimiWriteOutcome::default());
    }
    write_document(&doc)
}

/// On switch: ensure provider is present and set `default_model` to first model alias.
pub fn apply_switch_defaults(
    provider_id: &str,
    settings_config: &Value,
) -> Result<KimiWriteOutcome, AppError> {
    // Managed providers are provisioned by official login and must not be
    // rewritten from the simplified database projection.
    if !is_managed_provider(provider_id)? {
        set_provider(provider_id, settings_config.clone())?;
    }

    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Kimi config write lock poisoned".into()))?;

    let mut doc = read_document()?;
    if let Some(alias) = first_model_alias(provider_id, settings_config) {
        doc["default_model"] = Item::Value(TomlEditValue::from(alias.as_str()));
    }
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

pub fn save_oauth_token(token: &KimiOAuthToken) -> Result<(), AppError> {
    let path = get_kimi_credentials_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::io(parent, e))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(parent, fs::Permissions::from_mode(0o700))
                .map_err(|e| AppError::io(parent, e))?;
        }
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
    Ok(())
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

    let providers = ensure_table_mut(&mut doc, "providers");
    let provider = ensure_nested_table_mut(providers, MANAGED_KIMI_PROVIDER);
    provider.insert("type", Item::Value(TomlEditValue::from("kimi")));
    provider.insert(
        "base_url",
        Item::Value(TomlEditValue::from(KIMI_API_BASE_URL)),
    );
    provider.insert("api_key", Item::Value(TomlEditValue::from("")));
    let oauth = ensure_nested_table_mut(provider, "oauth");
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

    let root = ensure_table_mut(&mut doc, "models");
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
        let entry = ensure_nested_table_mut(root, &alias);
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

        for (source, target) in [("display_name", "display_name"), ("protocol", "protocol")] {
            if let Some(value) = model.get(source).and_then(Value::as_str) {
                entry.insert(target, Item::Value(TomlEditValue::from(value)));
            } else {
                entry.remove(target);
            }
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

    let services = ensure_table_mut(&mut doc, "services");
    for (name, suffix) in [("moonshot_search", "search"), ("moonshot_fetch", "fetch")] {
        let service = ensure_nested_table_mut(services, name);
        service.insert(
            "base_url",
            Item::Value(TomlEditValue::from(
                format!("{KIMI_API_BASE_URL}/{suffix}").as_str(),
            )),
        );
        service.insert("api_key", Item::Value(TomlEditValue::from("")));
        let oauth = ensure_nested_table_mut(service, "oauth");
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
    use serde_json::json;
    use std::sync::Mutex;

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn with_temp_home<F: FnOnce(&Path)>(f: F) {
        let _g = TEST_LOCK.lock().unwrap();
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("home");
        fs::create_dir_all(home.join(".kimi-code")).unwrap();
        // Point via env override used by get_kimi_dir when settings override is None.
        // We set KIMI_CODE_HOME to our temp dir.
        std::env::set_var("KIMI_CODE_HOME", home.join(".kimi-code"));
        f(&home);
        std::env::remove_var("KIMI_CODE_HOME");
    }

    #[test]
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
    fn reserved_managed_name_without_live_table_is_not_reported_as_managed() {
        with_temp_home(|_home| {
            assert!(!is_managed_provider(MANAGED_KIMI_PROVIDER).unwrap());
            assert!(set_provider(MANAGED_KIMI_PROVIDER, json!({"type": "openai"})).is_err());
        });
    }

    #[test]
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
    fn oauth_token_uses_official_wire_format() {
        with_temp_home(|_home| {
            let token = KimiOAuthToken {
                access_token: "access".to_string(),
                refresh_token: "refresh".to_string(),
                expires_at: 123,
                scope: "".to_string(),
                token_type: "Bearer".to_string(),
                expires_in: 3600,
            };
            save_oauth_token(&token).unwrap();
            let value: Value =
                serde_json::from_slice(&fs::read(get_kimi_credentials_path()).unwrap()).unwrap();
            assert_eq!(value["access_token"], "access");
            assert_eq!(value["refresh_token"], "refresh");
            assert_eq!(load_oauth_token().unwrap().unwrap().expires_in, 3600);
        });
    }
}
