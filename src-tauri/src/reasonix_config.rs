//! Reasonix CLI configuration (`config.toml` + `.env` under the Reasonix home).
//!
//! Live layout (official contract):
//! - `<home>/config.toml` — non-secret config (`[[providers]]`, `[[plugins]]`, …)
//! - `<home>/.env` — API keys referenced by `api_key_env`
//! - `<home>/skills/` — skills directory
//!
//! CC Switch uses additive mode: every DB provider is projected into live
//! `[[providers]]`, and switching only updates top-level `default_model`.

use crate::config::{atomic_write, get_home_dir};
use crate::error::AppError;
use crate::settings::{effective_backup_retain_count, get_reasonix_override_dir};
use chrono::Local;
use indexmap::IndexMap;
use serde_json::{Map, Value};
use std::collections::hash_map::DefaultHasher;
use std::collections::HashMap;
use std::fs;
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use toml_edit::{
    Array, ArrayOfTables, DocumentMut, InlineTable, Item, Table, Value as TomlEditValue,
};

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct ReasonixWriteOutcome {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub backup_path: Option<String>,
}

fn write_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

/// Serialize `.env` read-modify-write separately from TOML [`write_lock`].
/// Callers that hold `write_lock` must take this lock **after** the TOML lock
/// (never the reverse) to avoid ABBA deadlocks with [`apply_proxy_takeover`].
fn env_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

// ============================================================================
// Paths
// ============================================================================

fn default_reasonix_home() -> PathBuf {
    #[cfg(windows)]
    {
        if let Ok(app_data) = std::env::var("APPDATA") {
            let trimmed = app_data.trim();
            if !trimmed.is_empty() {
                return PathBuf::from(trimmed).join("reasonix");
            }
        }
    }
    get_home_dir().join(".reasonix")
}

/// Resolve Reasonix home directory.
///
/// Order:
/// 1. CC Switch settings override (`reasonix_config_dir`)
/// 2. `REASONIX_HOME` env (trimmed, non-empty)
/// 3. `%APPDATA%\reasonix` (Windows) or `~/.reasonix`
pub fn get_reasonix_dir() -> PathBuf {
    if let Some(override_dir) = get_reasonix_override_dir() {
        return override_dir;
    }

    if let Some(raw) = std::env::var_os("REASONIX_HOME") {
        let value = raw.to_string_lossy();
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }

    default_reasonix_home()
}

/// State root for sessions / projects / memory (mirrors Reasonix `userSupportDir`).
///
/// Prefer `REASONIX_STATE_HOME`, then the same home as `get_reasonix_dir()`.
/// Config (`config.toml`, `.env`) stays under `get_reasonix_dir()`.
pub fn get_reasonix_state_dir() -> PathBuf {
    if let Some(raw) = std::env::var_os("REASONIX_STATE_HOME") {
        let value = raw.to_string_lossy();
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    get_reasonix_dir()
}

pub fn get_reasonix_config_path() -> PathBuf {
    get_reasonix_dir().join("config.toml")
}

pub fn get_reasonix_env_path() -> PathBuf {
    get_reasonix_dir().join(".env")
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
            "provider.reasonix.config.invalid_toml",
            format!("Reasonix config.toml 格式错误: {e}"),
            format!("Invalid Reasonix config.toml: {e}"),
        )
    })
}

pub fn read_document() -> Result<DocumentMut, AppError> {
    let path = get_reasonix_config_path();
    if !path.exists() {
        return Ok(DocumentMut::new());
    }
    let text = fs::read_to_string(&path).map_err(|e| AppError::io(&path, e))?;
    parse_document_text(&text)
}

fn backup_config_if_exists(path: &Path) -> Result<Option<String>, AppError> {
    if !path.exists() {
        return Ok(None);
    }
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    let stamp = Local::now().format("%Y%m%d_%H%M%S");
    let backup = parent.join(format!("config.toml.bak.{stamp}"));
    fs::copy(path, &backup).map_err(|e| AppError::io(path, e))?;

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

fn write_document(doc: &DocumentMut) -> Result<ReasonixWriteOutcome, AppError> {
    let path = get_reasonix_config_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::io(parent, e))?;
    }
    let backup_path = backup_config_if_exists(&path)?;
    let text = doc.to_string();
    atomic_write(&path, text.as_bytes())?;
    Ok(ReasonixWriteOutcome { backup_path })
}

pub fn read_config_text() -> Result<String, AppError> {
    let path = get_reasonix_config_path();
    if !path.exists() {
        return Ok(String::new());
    }
    fs::read_to_string(&path).map_err(|e| AppError::io(&path, e))
}

pub fn write_config_text(text: &str) -> Result<(), AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Reasonix config write lock poisoned".into()))?;
    let path = get_reasonix_config_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::io(parent, e))?;
    }
    atomic_write(&path, text.as_bytes())
}

// ============================================================================
// Helpers
// ============================================================================

fn table_str(table: &Table, key: &str) -> Option<String> {
    table
        .get(key)
        .and_then(|item| item.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string)
}

fn hash_label(label: &str) -> String {
    let mut hasher = DefaultHasher::new();
    label.hash(&mut hasher);
    format!("{:08x}", hasher.finish())
}

/// Map a provider name to an `.env` key (`NAME_API_KEY`).
pub fn generate_api_key_env(name: &str) -> String {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return "CUSTOM_API_KEY".to_string();
    }

    let mut env_name = if trimmed.chars().all(|c| c.is_ascii()) {
        let normalized: String = trimmed
            .chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() {
                    c.to_ascii_uppercase()
                } else {
                    '_'
                }
            })
            .collect();
        let collapsed = normalized
            .split('_')
            .filter(|part| !part.is_empty())
            .collect::<Vec<_>>()
            .join("_");
        if collapsed.is_empty() {
            format!("CUSTOM_{}_API_KEY", hash_label(trimmed))
        } else {
            format!("{collapsed}_API_KEY")
        }
    } else {
        format!("CUSTOM_{}_API_KEY", hash_label(trimmed))
    };

    if env_name
        .chars()
        .next()
        .is_some_and(|c| c.is_ascii_digit())
    {
        env_name = format!("CUSTOM_{env_name}");
    }

    env_name
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

fn merge_json_into_table(table: &mut Table, object: &Map<String, Value>, skipped: &[&str]) {
    for (key, value) in object {
        if skipped.contains(&key.as_str()) {
            continue;
        }
        if value.is_null()
            || matches!(
                key.as_str(),
                "api_key" | "base_url" | "chat_url" | "models_url"
            ) && value.as_str().is_some_and(|value| value.trim().is_empty())
        {
            table.remove(key);
            continue;
        }
        match value {
            Value::Object(object) => {
                if !table.contains_key(key) || !table[key].is_table() {
                    table.insert(key, Item::Table(Table::new()));
                }
                if let Some(nested) = table.get_mut(key).and_then(Item::as_table_mut) {
                    merge_json_into_table(nested, object, &[]);
                }
            }
            _ => {
                if let Some(value) = json_to_toml_value(value) {
                    table.insert(key, Item::Value(value));
                }
            }
        }
    }
}

fn providers_array_mut(doc: &mut DocumentMut) -> &mut ArrayOfTables {
    if !doc.contains_key("providers") || !doc["providers"].is_array_of_tables() {
        doc["providers"] = Item::ArrayOfTables(ArrayOfTables::new());
    }
    doc["providers"]
        .as_array_of_tables_mut()
        .expect("providers is array-of-tables")
}

fn plugins_array_mut(doc: &mut DocumentMut) -> &mut ArrayOfTables {
    if !doc.contains_key("plugins") || !doc["plugins"].is_array_of_tables() {
        doc["plugins"] = Item::ArrayOfTables(ArrayOfTables::new());
    }
    doc["plugins"]
        .as_array_of_tables_mut()
        .expect("plugins is array-of-tables")
}

fn find_array_table_index(arr: &ArrayOfTables, key: &str, value: &str) -> Option<usize> {
    arr.iter().enumerate().find_map(|(index, table)| {
        table_str(table, key)
            .is_some_and(|candidate| candidate == value)
            .then_some(index)
    })
}

fn models_from_provider_table(table: &Table) -> Vec<String> {
    if let Some(models) = table.get("models").and_then(Item::as_array) {
        return models
            .iter()
            .filter_map(|item| item.as_str().map(str::to_string))
            .collect();
    }
    table_str(table, "model").map(|model| vec![model]).unwrap_or_default()
}

fn default_model_from_provider_table(table: &Table) -> Option<String> {
    table_str(table, "default").or_else(|| table_str(table, "model"))
}

fn provider_table_to_json(table: &Table, env_keys: &HashMap<String, String>) -> Map<String, Value> {
    let parsed: toml::Value =
        toml::from_str(&table.to_string()).unwrap_or(toml::Value::Table(Default::default()));
    let mut obj = match serde_json::to_value(parsed) {
        Ok(Value::Object(map)) => map,
        _ => Map::new(),
    };

    let name = obj
        .get("name")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_default();
    if !name.is_empty() {
        obj.insert("name".into(), Value::String(name.clone()));
    }

    if obj.get("kind").is_none() {
        if let Some(kind) = obj.get("type").and_then(Value::as_str) {
            obj.insert("kind".into(), Value::String(kind.to_string()));
        }
    }

    let models = models_from_provider_table(table);
    if !models.is_empty() {
        obj.insert(
            "models".into(),
            Value::Array(models.iter().cloned().map(Value::String).collect()),
        );
    }
    if let Some(default) = default_model_from_provider_table(table) {
        obj.insert("default".into(), Value::String(default));
    }
    obj.remove("model");

    if let Some(env_key) = table_str(table, "api_key_env") {
        if let Some(api_key) = env_keys.get(&env_key) {
            obj.insert("api_key".into(), Value::String(api_key.clone()));
        }
    }

    obj.remove("api_key_env");
    obj
}

fn resolve_default_model(settings: &Value) -> Option<String> {
    if let Some(default) = settings
        .get("default")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        return Some(default.to_string());
    }

    if let Some(models) = settings.get("models").and_then(Value::as_array) {
        for model in models {
            if let Some(id) = model.as_str().map(str::trim).filter(|s| !s.is_empty()) {
                return Some(id.to_string());
            }
            if let Some(id) = model
                .get("id")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
            {
                return Some(id.to_string());
            }
        }
    }

    settings
        .get("model")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn models_array_from_settings(settings: &Value) -> Vec<String> {
    if let Some(models) = settings.get("models").and_then(Value::as_array) {
        let values: Vec<String> = models
            .iter()
            .filter_map(|model| {
                model
                    .as_str()
                    .map(str::to_string)
                    .or_else(|| {
                        model
                            .get("id")
                            .and_then(Value::as_str)
                            .map(str::to_string)
                    })
            })
            .filter(|value| !value.trim().is_empty())
            .collect();
        if !values.is_empty() {
            return values;
        }
    }

    settings
        .get("model")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|model| vec![model.to_string()])
        .unwrap_or_default()
}

// ============================================================================
// .env helpers
// ============================================================================

pub fn read_env_map() -> Result<HashMap<String, String>, AppError> {
    let path = get_reasonix_env_path();
    if !path.exists() {
        return Ok(HashMap::new());
    }
    let text = fs::read_to_string(&path).map_err(|e| AppError::io(&path, e))?;
    Ok(parse_env_text(&text))
}

fn parse_env_text(text: &str) -> HashMap<String, String> {
    let mut map = HashMap::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let Some((key, value)) = trimmed.split_once('=') else {
            continue;
        };
        let key = key.trim();
        if key.is_empty() {
            continue;
        }
        map.insert(key.to_string(), value.trim().to_string());
    }
    map
}

fn render_env_text(map: &HashMap<String, String>, cleared_keys: &[String]) -> String {
    let path = get_reasonix_env_path();
    let mut lines: Vec<String> = if path.exists() {
        fs::read_to_string(&path)
            .unwrap_or_default()
            .lines()
            .map(str::to_string)
            .collect()
    } else {
        Vec::new()
    };

    let mut seen = HashMap::<String, usize>::new();
    for (index, line) in lines.iter().enumerate() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        if let Some((key, _)) = trimmed.split_once('=') {
            seen.insert(key.trim().to_string(), index);
        }
    }

    for key in cleared_keys {
        if let Some(index) = seen.get(key).copied() {
            lines[index] = format!("# reasonix-cleared {key}");
        }
    }

    for (key, value) in map {
        if let Some(index) = seen.get(key).copied() {
            lines[index] = format!("{key}={value}");
        } else {
            lines.push(format!("{key}={value}"));
        }
    }

    let mut output = lines.join("\n");
    if !output.is_empty() {
        output.push('\n');
    }
    output
}

pub fn upsert_env_key(key: &str, value: &str) -> Result<(), AppError> {
    let key = key.trim();
    if key.is_empty() {
        return Ok(());
    }
    let _guard = env_lock()
        .lock()
        .map_err(|_| AppError::Message("Reasonix env write lock poisoned".into()))?;
    let path = get_reasonix_env_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::io(parent, e))?;
    }
    let mut map = HashMap::new();
    map.insert(key.to_string(), value.to_string());
    let text = render_env_text(&map, &[]);
    atomic_write(&path, text.as_bytes())
}

pub fn clear_env_key(key: &str) -> Result<(), AppError> {
    let key = key.trim();
    if key.is_empty() {
        return Ok(());
    }
    let _guard = env_lock()
        .lock()
        .map_err(|_| AppError::Message("Reasonix env write lock poisoned".into()))?;
    let path = get_reasonix_env_path();
    if !path.exists() {
        return Ok(());
    }
    let text = render_env_text(&HashMap::new(), &[key.to_string()]);
    atomic_write(&path, text.as_bytes())
}

// ============================================================================
// Providers
// ============================================================================

pub fn get_providers() -> Result<IndexMap<String, Value>, AppError> {
    let doc = read_document()?;
    let env_keys = read_env_map()?;
    let mut out = IndexMap::new();

    let Some(providers) = doc.get("providers").and_then(Item::as_array_of_tables) else {
        return Ok(out);
    };

    for table in providers {
        let Some(name) = table_str(table, "name") else {
            continue;
        };
        let mut obj = provider_table_to_json(table, &env_keys);
        obj.insert("name".into(), Value::String(name.clone()));
        out.insert(name, Value::Object(obj));
    }

    Ok(out)
}

fn upsert_provider_into_document(
    doc: &mut DocumentMut,
    name: &str,
    settings_config: &Value,
) -> Result<Option<(String, String)>, AppError> {
    let name = name.trim();
    if name.is_empty() {
        return Err(AppError::localized(
            "provider.reasonix.name.empty",
            "Reasonix 供应商名称不能为空",
            "Reasonix provider name cannot be empty",
        ));
    }

    let object = settings_config.as_object().ok_or_else(|| {
        AppError::localized(
            "provider.reasonix.settings.not_object",
            "Reasonix 供应商配置必须是对象",
            "Reasonix provider settings must be an object",
        )
    })?;

    let providers = providers_array_mut(doc);
    let index = find_array_table_index(providers, "name", name);
    let mut table = if let Some(index) = index {
        let existing = providers.get(index).cloned().unwrap_or_default();
        providers.remove(index);
        existing
    } else {
        Table::new()
    };

    merge_json_into_table(
        &mut table,
        object,
        &["name", "api_key", "api_key_env", "type"],
    );

    // Optional URL fields: absence in settings means remove from live TOML.
    // `merge_json_into_table` is additive for missing keys, so clear explicitly.
    for key in ["chat_url", "models_url"] {
        if !object.contains_key(key) {
            table.remove(key);
        }
    }

    table.insert("name", Item::Value(TomlEditValue::from(name)));

    let kind = object
        .get("kind")
        .or_else(|| object.get("type"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("openai");
    table.insert("kind", Item::Value(TomlEditValue::from(kind)));

    let models = models_array_from_settings(settings_config);
    if !models.is_empty() {
        let mut array = Array::new();
        for model in &models {
            array.push(model.as_str());
        }
        table.insert("models", Item::Value(TomlEditValue::Array(array)));
        table.remove("model");
        if let Some(default) = resolve_default_model(settings_config) {
            table.insert("default", Item::Value(TomlEditValue::from(default.as_str())));
        } else if let Some(first) = models.first() {
            table.insert("default", Item::Value(TomlEditValue::from(first.as_str())));
        }
    }

    let existing_env_key = table_str(&table, "api_key_env");
    let api_key_env = existing_env_key.unwrap_or_else(|| generate_api_key_env(name));
    table.insert(
        "api_key_env",
        Item::Value(TomlEditValue::from(api_key_env.as_str())),
    );
    table.remove("api_key");

    providers.push(table);

    let api_key = object
        .get("api_key")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);

    Ok(api_key.map(|value| (api_key_env, value)))
}

pub fn set_provider(name: &str, settings_config: Value) -> Result<ReasonixWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Reasonix config write lock poisoned".into()))?;

    let mut doc = read_document()?;
    let env_update = upsert_provider_into_document(&mut doc, name, &settings_config)?;
    let outcome = write_document(&doc)?;

    if let Some((env_key, api_key)) = env_update {
        upsert_env_key(&env_key, &api_key)?;
    }

    Ok(outcome)
}

/// Whether top-level `default_model` refers to this provider entry.
/// Switch path writes the provider **name**; older rows may still store a model id.
fn default_model_refers_to_provider(default_model: &str, name: &str, table: &Table) -> bool {
    let default_model = default_model.trim();
    let name = name.trim();
    if default_model.is_empty() || name.is_empty() {
        return false;
    }
    if default_model == name {
        return true;
    }
    // provider/model form
    if default_model
        .split_once('/')
        .is_some_and(|(prefix, _)| prefix == name)
    {
        return true;
    }
    // Legacy: default_model equals the table's default/model field
    if default_model_from_provider_table(table).as_deref() == Some(default_model) {
        return true;
    }
    models_from_provider_table(table)
        .iter()
        .any(|model| model == default_model)
}

fn first_remaining_provider_name(doc: &DocumentMut) -> Option<String> {
    doc.get("providers")
        .and_then(Item::as_array_of_tables)
        .and_then(|providers| {
            providers.iter().find_map(|table| {
                table_str(table, "name")
                    .filter(|n| n != REASONIX_PROXY_PROVIDER && !n.is_empty())
            })
        })
}

pub fn remove_provider(name: &str) -> Result<ReasonixWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Reasonix config write lock poisoned".into()))?;

    let mut doc = read_document()?;
    let name = name.trim();
    let providers = providers_array_mut(&mut doc);
    let index = find_array_table_index(providers, "name", name);
    let Some(index) = index else {
        return Ok(ReasonixWriteOutcome::default());
    };

    let removed = providers.get(index).cloned().unwrap_or_default();
    providers.remove(index);
    let env_key = table_str(&removed, "api_key_env");

    if let Some(default_model) = doc
        .get("default_model")
        .and_then(Item::as_str)
        .map(str::to_string)
    {
        if default_model_refers_to_provider(&default_model, name, &removed) {
            if let Some(next) = first_remaining_provider_name(&doc) {
                doc["default_model"] = Item::Value(TomlEditValue::from(next.as_str()));
            } else {
                doc.as_table_mut().remove("default_model");
            }
        }
    }

    // Always persist after a successful array removal — early-return used to
    // drop the in-memory remove when there was no env key and default_model
    // comparison failed (provider-name vs model-id mismatch).
    let outcome = write_document(&doc)?;
    if let Some(key) = env_key {
        let _ = clear_env_key(&key);
    }
    Ok(outcome)
}

pub const REASONIX_PROXY_PROVIDER: &str = "cc-switch-proxy";
pub const REASONIX_PROXY_MODEL: &str = "cc-switch-proxy-default";
pub const REASONIX_PROXY_API_KEY_ENV: &str = "CC_SWITCH_PROXY_API_KEY";

/// Project Reasonix onto the stable local OpenAI ingress while preserving
/// unrelated `[[providers]]` tables and user-defined fields.
pub fn apply_proxy_takeover(proxy_base_url: &str) -> Result<ReasonixWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Reasonix config write lock poisoned".into()))?;

    let mut doc = read_document()?;
    let providers = providers_array_mut(&mut doc);
    let index = find_array_table_index(providers, "name", REASONIX_PROXY_PROVIDER);
    let mut table = if let Some(index) = index {
        let existing = providers.get(index).cloned().unwrap_or_default();
        providers.remove(index);
        existing
    } else {
        Table::new()
    };

    table.insert(
        "name",
        Item::Value(TomlEditValue::from(REASONIX_PROXY_PROVIDER)),
    );
    table.insert("kind", Item::Value(TomlEditValue::from("openai")));
    table.insert(
        "base_url",
        Item::Value(TomlEditValue::from(proxy_base_url)),
    );

    let mut models = Array::new();
    models.push(REASONIX_PROXY_MODEL);
    table.insert("models", Item::Value(TomlEditValue::Array(models)));
    table.insert(
        "default",
        Item::Value(TomlEditValue::from(REASONIX_PROXY_MODEL)),
    );
    table.insert(
        "api_key_env",
        Item::Value(TomlEditValue::from(REASONIX_PROXY_API_KEY_ENV)),
    );
    table.insert("no_proxy", Item::Value(TomlEditValue::from(true)));
    table.remove("api_key");
    // Live proxy ingress must not probe /models or override chat_url.
    table.remove("models_url");
    table.remove("chat_url");

    providers.push(table);
    // Prefer provider name so ResolveModel uses provider.default (not a bare
    // model id that could collide with a user model named the same).
    doc["default_model"] = Item::Value(TomlEditValue::from(REASONIX_PROXY_PROVIDER));

    let outcome = write_document(&doc)?;
    stash_proxy_env_previous_if_needed()?;
    upsert_env_key(REASONIX_PROXY_API_KEY_ENV, "PROXY_MANAGED")?;
    Ok(outcome)
}

pub fn is_proxy_takeover_active() -> Result<bool, AppError> {
    is_proxy_takeover_active_for_url(None)
}

/// Detect CC Switch proxy takeover. When `expected_base_url` is set, require an
/// exact base_url match (port-safe). Otherwise require `/reasonix/v1` in the URL
/// — never treat arbitrary localhost providers as takeover.
pub fn is_proxy_takeover_active_for_url(expected_base_url: Option<&str>) -> Result<bool, AppError> {
    let doc = read_document()?;
    let Some(providers) = doc.get("providers").and_then(Item::as_array_of_tables) else {
        return Ok(false);
    };

    let Some(table) = providers.iter().find(|table| {
        table_str(table, "name").as_deref() == Some(REASONIX_PROXY_PROVIDER)
    }) else {
        return Ok(false);
    };

    let env_managed = read_env_map()?
        .get(REASONIX_PROXY_API_KEY_ENV)
        .is_some_and(|value| value == "PROXY_MANAGED");

    let url_ok = table_str(table, "base_url").is_some_and(|url| {
        if let Some(expected) = expected_base_url.map(str::trim).filter(|s| !s.is_empty()) {
            url.trim_end_matches('/') == expected.trim_end_matches('/')
        } else {
            url.contains("/reasonix/v1")
        }
    });

    Ok(
        table_str(table, "kind").as_deref() == Some("openai")
            && table_str(table, "api_key_env").as_deref() == Some(REASONIX_PROXY_API_KEY_ENV)
            && env_managed
            && url_ok,
    )
}

/// Clear or restore the managed proxy env placeholder after restoring a
/// pre-takeover snapshot. Prefer [`restore_proxy_env_placeholder`].
pub fn clear_proxy_env_placeholder() -> Result<(), AppError> {
    restore_proxy_env_placeholder()
}

const PROXY_ENV_ABSENT_MARKER: &str = "__ABSENT__";
const PROXY_ENV_BACKUP_FILENAME: &str = ".cc-switch-proxy-api-key.bak";

fn proxy_env_backup_path() -> PathBuf {
    get_reasonix_dir().join(PROXY_ENV_BACKUP_FILENAME)
}

/// Snapshot the previous `CC_SWITCH_PROXY_API_KEY` value once per takeover
/// session so disable can restore it (PRD R2). Idempotent: does not overwrite
/// an existing stash while takeover remains active.
fn stash_proxy_env_previous_if_needed() -> Result<(), AppError> {
    let backup_path = proxy_env_backup_path();
    if backup_path.exists() {
        return Ok(());
    }
    if let Some(parent) = backup_path.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::io(parent, e))?;
    }
    // Hold env_lock so the snapshot cannot tear against concurrent upsert/clear.
    let _guard = env_lock()
        .lock()
        .map_err(|_| AppError::Message("Reasonix env write lock poisoned".into()))?;
    if backup_path.exists() {
        return Ok(());
    }
    let previous = read_env_map()?
        .get(REASONIX_PROXY_API_KEY_ENV)
        .cloned()
        .filter(|value| value != "PROXY_MANAGED");
    let content = previous.unwrap_or_else(|| PROXY_ENV_ABSENT_MARKER.to_string());
    atomic_write(&backup_path, content.as_bytes())
}

/// Restore the pre-takeover env key (or remove it if it was absent).
pub fn restore_proxy_env_placeholder() -> Result<(), AppError> {
    let backup_path = proxy_env_backup_path();
    if backup_path.exists() {
        let content = fs::read_to_string(&backup_path).map_err(|e| AppError::io(&backup_path, e))?;
        let trimmed = content.trim();
        if trimmed.is_empty() || trimmed == PROXY_ENV_ABSENT_MARKER {
            clear_env_key(REASONIX_PROXY_API_KEY_ENV)?;
        } else {
            upsert_env_key(REASONIX_PROXY_API_KEY_ENV, trimmed)?;
        }
        let _ = fs::remove_file(&backup_path);
        return Ok(());
    }
    clear_env_key(REASONIX_PROXY_API_KEY_ENV)
}

/// Remove only the CC Switch-owned projection if no backup is available.
pub fn clear_proxy_takeover() -> Result<ReasonixWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Reasonix config write lock poisoned".into()))?;

    let mut doc = read_document()?;
    let mut changed = false;

    let providers = providers_array_mut(&mut doc);
    if let Some(index) = find_array_table_index(providers, "name", REASONIX_PROXY_PROVIDER) {
        providers.remove(index);
        changed = true;
    }

    if doc.get("default_model").and_then(Item::as_str).is_some_and(|value| {
        value == REASONIX_PROXY_PROVIDER
            || value == REASONIX_PROXY_MODEL
            || value == "cc-switch-proxy/cc-switch-proxy-default"
    }) {
        // Prefer provider name (switch semantics), not a bare model id.
        if let Some(fallback) = first_remaining_provider_name(&doc) {
            doc["default_model"] = Item::Value(TomlEditValue::from(fallback.as_str()));
        } else {
            doc.as_table_mut().remove("default_model");
        }
        changed = true;
    }

    if changed {
        let outcome = write_document(&doc)?;
        let _ = restore_proxy_env_placeholder();
        Ok(outcome)
    } else {
        // Partial failure: stash may exist without a live proxy provider.
        if proxy_env_backup_path().exists() {
            let _ = restore_proxy_env_placeholder();
        }
        Ok(ReasonixWriteOutcome::default())
    }
}

/// Project switch defaults into a full TOML snapshot text.
///
/// Used by proxy hot-switch / takeover CRUD to update the restore backup's
/// `default_model` (and custom provider projection) while live stays on
/// `cc-switch-proxy`.
///
/// Also syncs `settings_config.api_key` into `.env` under [`env_lock`],
/// matching [`apply_switch_defaults`]. Reasonix stores credentials only in
/// `.env` via `api_key_env`; a TOML-only backup update would restore a
/// provider whose env key is missing after disable (B1).
/// Upsert a provider into a TOML snapshot **without** changing top-level
/// `default_model`. Used by additive full-sync under proxy takeover so bulk
/// projection does not stomp the restore backup's routing default.
pub fn upsert_provider_into_text(
    text: &str,
    provider_id: &str,
    settings_config: &Value,
) -> Result<String, AppError> {
    let mut doc = parse_document_text(text)?;
    let env_update = upsert_provider_into_document(&mut doc, provider_id, settings_config)?;
    let updated = doc.to_string();
    if let Some((env_key, api_key)) = env_update {
        upsert_env_key(&env_key, &api_key)?;
    }
    Ok(updated)
}

pub fn apply_switch_defaults_to_text(
    text: &str,
    provider_id: &str,
    settings_config: &Value,
) -> Result<String, AppError> {
    let mut doc = parse_document_text(text)?;
    let env_update = upsert_provider_into_document(&mut doc, provider_id, settings_config)?;
    // Reasonix ResolveModel prefers provider name (uses provider.default) or
    // provider/model. Prefer the provider id so multi-provider same-model-id
    // configs stay unambiguous after restore.
    doc["default_model"] = Item::Value(TomlEditValue::from(provider_id.trim()));
    let updated = doc.to_string();
    if let Some((env_key, api_key)) = env_update {
        upsert_env_key(&env_key, &api_key)?;
    }
    Ok(updated)
}

/// Remove a provider table from a TOML snapshot (used to edit restore backups
/// during proxy takeover without touching live).
///
/// Returns `(updated_text, api_key_env)` so callers can clear the orphan `.env`
/// key after deleting the provider from the restore snapshot.
pub fn remove_provider_from_text(
    text: &str,
    name: &str,
) -> Result<(String, Option<String>), AppError> {
    let mut doc = parse_document_text(text)?;
    let name = name.trim();
    let providers = providers_array_mut(&mut doc);
    let Some(index) = find_array_table_index(providers, "name", name) else {
        return Ok((text.to_string(), None));
    };
    let removed = providers.get(index).cloned().unwrap_or_default();
    let env_key = table_str(&removed, "api_key_env");
    providers.remove(index);

    if let Some(default_model) = doc
        .get("default_model")
        .and_then(Item::as_str)
        .map(str::to_string)
    {
        if default_model_refers_to_provider(&default_model, name, &removed) {
            if let Some(next) = first_remaining_provider_name(&doc) {
                doc["default_model"] = Item::Value(TomlEditValue::from(next.as_str()));
            } else {
                doc.as_table_mut().remove("default_model");
            }
        }
    }

    Ok((doc.to_string(), env_key))
}

pub fn provider_exists_in_text(text: &str, name: &str) -> Result<bool, AppError> {
    let doc = parse_document_text(text)?;
    let Some(providers) = doc.get("providers").and_then(Item::as_array_of_tables) else {
        return Ok(false);
    };
    let name = name.trim();
    let found = providers
        .iter()
        .any(|table| table_str(table, "name").as_deref() == Some(name));
    Ok(found)
}

pub fn apply_switch_defaults(
    provider_id: &str,
    settings_config: &Value,
) -> Result<ReasonixWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Reasonix config write lock poisoned".into()))?;

    let mut doc = read_document()?;
    let env_update = upsert_provider_into_document(&mut doc, provider_id, settings_config)?;
    doc["default_model"] = Item::Value(TomlEditValue::from(provider_id.trim()));
    let outcome = write_document(&doc)?;
    if let Some((env_key, api_key)) = env_update {
        upsert_env_key(&env_key, &api_key)?;
    }
    Ok(outcome)
}

pub fn get_default_model() -> Result<Option<String>, AppError> {
    let doc = read_document()?;
    Ok(doc
        .get("default_model")
        .and_then(Item::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string))
}

// ============================================================================
// MCP plugins ([[plugins]])
// ============================================================================

pub fn get_mcp_plugins() -> Result<IndexMap<String, Value>, AppError> {
    let doc = read_document()?;
    let mut out = IndexMap::new();
    let Some(plugins) = doc.get("plugins").and_then(Item::as_array_of_tables) else {
        return Ok(out);
    };

    for table in plugins {
        let Some(name) = table_str(table, "name") else {
            continue;
        };
        let parsed: toml::Value =
            toml::from_str(&table.to_string()).unwrap_or(toml::Value::Table(Default::default()));
        let value = serde_json::to_value(parsed)
            .map_err(|e| AppError::Message(format!("Failed to convert Reasonix plugin: {e}")))?;
        out.insert(name, value);
    }

    Ok(out)
}

pub fn set_mcp_plugin(name: &str, spec: &Value) -> Result<ReasonixWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Reasonix config write lock poisoned".into()))?;

    let mut doc = read_document()?;
    let plugins = plugins_array_mut(&mut doc);
    let index = find_array_table_index(plugins, "name", name.trim());

    let mut table = if let Some(index) = index {
        let existing = plugins.get(index).cloned().unwrap_or_default();
        plugins.remove(index);
        existing
    } else {
        Table::new()
    };

    if let Some(object) = spec.as_object() {
        merge_json_into_table(&mut table, object, &["name"]);
    }
    table.insert("name", Item::Value(TomlEditValue::from(name.trim())));

    plugins.push(table);

    write_document(&doc)
}

pub fn remove_mcp_plugin(name: &str) -> Result<ReasonixWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Reasonix config write lock poisoned".into()))?;

    let mut doc = read_document()?;
    let plugins = plugins_array_mut(&mut doc);
    let index = find_array_table_index(plugins, "name", name.trim());
    if index.is_none() {
        return Ok(ReasonixWriteOutcome::default());
    }
    plugins.remove(index.unwrap());
    write_document(&doc)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use serial_test::serial;

    fn with_temp_home<F: FnOnce()>(f: F) {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("reasonix-home");
        fs::create_dir_all(&home).unwrap();
        let previous = std::env::var_os("REASONIX_HOME");
        std::env::set_var("REASONIX_HOME", &home);
        f();
        match previous {
            Some(value) => std::env::set_var("REASONIX_HOME", value),
            None => std::env::remove_var("REASONIX_HOME"),
        }
    }

    #[test]
    fn generate_api_key_env_ascii_and_non_ascii() {
        assert_eq!(generate_api_key_env("deepseek"), "DEEPSEEK_API_KEY");
        assert_eq!(generate_api_key_env("my-provider"), "MY_PROVIDER_API_KEY");
        assert!(generate_api_key_env("中文").starts_with("CUSTOM_"));
        assert!(generate_api_key_env("123abc").starts_with("CUSTOM_"));
    }

    #[test]
    #[serial]
    fn set_provider_writes_api_key_env_not_plaintext_key() {
        with_temp_home(|| {
            let settings = json!({
                "name": "deepseek",
                "kind": "openai",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-secret",
                "models": ["deepseek-v4-flash"],
                "default": "deepseek-v4-flash"
            });
            set_provider("deepseek", settings).unwrap();
            let text = fs::read_to_string(get_reasonix_config_path()).unwrap();
            assert!(text.contains("api_key_env"));
            assert!(!text.contains("sk-secret"));
            let env = fs::read_to_string(get_reasonix_env_path()).unwrap();
            assert!(env.contains("DEEPSEEK_API_KEY=sk-secret"));
        });
    }

    #[test]
    #[serial]
    fn apply_switch_defaults_sets_default_model() {
        with_temp_home(|| {
            let settings = json!({
                "kind": "openai",
                "base_url": "https://api.example.com",
                "models": ["model-a", "model-b"],
                "default": "model-b"
            });
            apply_switch_defaults("demo", &settings).unwrap();
            let text = fs::read_to_string(get_reasonix_config_path()).unwrap();
            // Prefer provider id so multi-provider same-model-id stays unambiguous.
            assert!(text.contains("default_model = \"demo\""));
            assert_eq!(get_default_model().unwrap().as_deref(), Some("demo"));
            assert!(text.contains("default = \"model-b\""));
        });
    }

    #[test]
    #[serial]
    fn get_providers_reads_legacy_model_field() {
        with_temp_home(|| {
            fs::write(
                get_reasonix_config_path(),
                r#"[[providers]]
name = "legacy"
kind = "openai"
model = "old-model"
api_key_env = "LEGACY_API_KEY"
"#,
            )
            .unwrap();
            upsert_env_key("LEGACY_API_KEY", "legacy-key").unwrap();
            let providers = get_providers().unwrap();
            let provider = providers.get("legacy").unwrap().as_object().unwrap();
            assert_eq!(
                provider.get("models").unwrap(),
                &json!(["old-model"])
            );
            assert_eq!(provider.get("default").unwrap(), "old-model");
            assert_eq!(provider.get("api_key").unwrap(), "legacy-key");
        });
    }

    #[test]
    #[serial]
    fn apply_proxy_takeover_upserts_managed_provider_and_env() {
        with_temp_home(|| {
            fs::write(
                get_reasonix_config_path(),
                r#"default_model = "demo-model"

[[providers]]
name = "demo"
kind = "openai"
base_url = "https://example.test/v1"
models = ["demo-model"]
default = "demo-model"
api_key_env = "DEMO_API_KEY"
"#,
            )
            .unwrap();
            upsert_env_key("DEMO_API_KEY", "secret").unwrap();

            apply_proxy_takeover("http://127.0.0.1:15721/reasonix/v1").unwrap();
            assert!(is_proxy_takeover_active().unwrap());

            let text = fs::read_to_string(get_reasonix_config_path()).unwrap();
            assert!(text.contains("name = \"cc-switch-proxy\""));
            assert!(text.contains("cc-switch-proxy-default"));
            assert!(text.contains("no_proxy = true"));
            assert!(text.contains("default_model = \"cc-switch-proxy\""));
            assert!(text.contains("name = \"demo\""));

            let env = fs::read_to_string(get_reasonix_env_path()).unwrap();
            assert!(env.contains("CC_SWITCH_PROXY_API_KEY=PROXY_MANAGED"));

            clear_proxy_takeover().unwrap();
            assert!(!is_proxy_takeover_active().unwrap());
            let restored = fs::read_to_string(get_reasonix_config_path()).unwrap();
            assert!(!restored.contains("name = \"cc-switch-proxy\""));
            assert!(restored.contains("name = \"demo\""));
        });
    }

    #[test]
    #[serial]
    fn proxy_takeover_restores_previous_env_key_value() {
        with_temp_home(|| {
            fs::write(
                get_reasonix_config_path(),
                r#"default_model = "demo-model"
[[providers]]
name = "demo"
kind = "openai"
base_url = "https://example.test/v1"
models = ["demo-model"]
default = "demo-model"
api_key_env = "DEMO_API_KEY"
"#,
            )
            .unwrap();
            upsert_env_key("DEMO_API_KEY", "secret").unwrap();
            upsert_env_key(REASONIX_PROXY_API_KEY_ENV, "user-owned-secret").unwrap();

            apply_proxy_takeover("http://127.0.0.1:15721/reasonix/v1").unwrap();
            let env_taken = fs::read_to_string(get_reasonix_env_path()).unwrap();
            assert!(env_taken.contains("CC_SWITCH_PROXY_API_KEY=PROXY_MANAGED"));

            // Simulate backup restore path used by ProxyService disable.
            restore_proxy_env_placeholder().unwrap();
            let env_restored = fs::read_to_string(get_reasonix_env_path()).unwrap();
            assert!(
                env_restored.contains("CC_SWITCH_PROXY_API_KEY=user-owned-secret"),
                "previous env value must be restored, got:\n{env_restored}"
            );
            assert!(!proxy_env_backup_path().exists());
        });
    }

    #[test]
    #[serial]
    fn is_proxy_takeover_active_requires_reasonix_ingress_path() {
        with_temp_home(|| {
            fs::write(
                get_reasonix_config_path(),
                r#"default_model = "cc-switch-proxy"

[[providers]]
name = "cc-switch-proxy"
kind = "openai"
base_url = "http://127.0.0.1:15721/v1"
models = ["cc-switch-proxy-default"]
default = "cc-switch-proxy-default"
api_key_env = "CC_SWITCH_PROXY_API_KEY"
"#,
            )
            .unwrap();
            upsert_env_key(REASONIX_PROXY_API_KEY_ENV, "PROXY_MANAGED").unwrap();
            assert!(
                !is_proxy_takeover_active().unwrap(),
                "localhost openai without /reasonix/v1 must not count as takeover"
            );

            apply_proxy_takeover("http://127.0.0.1:15721/reasonix/v1").unwrap();
            assert!(is_proxy_takeover_active().unwrap());
            assert!(is_proxy_takeover_active_for_url(Some(
                "http://127.0.0.1:15721/reasonix/v1"
            ))
            .unwrap());
            assert!(!is_proxy_takeover_active_for_url(Some(
                "http://127.0.0.1:9999/reasonix/v1"
            ))
            .unwrap());
        });
    }

    #[test]
    #[serial]
    fn upsert_provider_into_text_preserves_default_model() {
        with_temp_home(|| {
            let original = r#"default_model = "demo"

[[providers]]
name = "demo"
kind = "openai"
base_url = "https://example.test/v1"
models = ["model-a"]
default = "model-a"
"#;
            let settings = json!({
                "kind": "openai",
                "base_url": "https://other.test/v1",
                "api_key": "other-secret",
                "models": ["other-model"],
                "default": "other-model"
            });
            let updated =
                upsert_provider_into_text(original, "other", &settings).expect("upsert text");
            assert!(
                updated.contains("default_model = \"demo\""),
                "bulk upsert must not stomp default_model: {updated}"
            );
            assert!(updated.contains("name = \"other\""));
            assert!(updated.contains("other-model"));

            let switched =
                apply_switch_defaults_to_text(&updated, "other", &settings).expect("switch");
            assert!(
                switched.contains("default_model = \"other\""),
                "switch projection should set default_model: {switched}"
            );
        });
    }

    #[test]
    #[serial]
    fn apply_switch_defaults_to_text_updates_backup_projection() {
        with_temp_home(|| {
            let original = r#"default_model = "model-a"

[[providers]]
name = "demo"
kind = "openai"
base_url = "https://example.test/v1"
models = ["model-a"]
default = "model-a"
"#;
            let settings = json!({
                "kind": "openai",
                "base_url": "https://other.test/v1",
                "api_key": "other-secret",
                "models": ["other-model"],
                "default": "other-model"
            });
            let updated =
                apply_switch_defaults_to_text(original, "other", &settings).expect("update text");
            assert!(updated.contains("name = \"other\""));
            assert!(updated.contains("default_model = \"other\""));
            assert!(updated.contains("default = \"other-model\""));
            assert!(
                updated.contains("api_key_env"),
                "backup projection must use api_key_env, not plaintext api_key"
            );
            assert!(
                !updated.contains("other-secret"),
                "plaintext api_key must not land in TOML backup"
            );
            let env = read_env_map().expect("read env");
            assert_eq!(
                env.get("OTHER_API_KEY").map(String::as_str),
                Some("other-secret"),
                "backup projection must sync api_key into .env for restore"
            );
        });
    }

    #[test]
    #[serial]
    fn set_provider_clears_optional_urls_when_absent() {
        with_temp_home(|| {
            set_provider(
                "demo",
                json!({
                    "kind": "openai",
                    "base_url": "https://api.example.com",
                    "api_key": "sk-secret",
                    "chat_url": "https://api.example.com/chat/completions",
                    "models_url": "https://api.example.com/models",
                    "models": ["m1"],
                    "default": "m1"
                }),
            )
            .unwrap();
            let with_urls = fs::read_to_string(get_reasonix_config_path()).unwrap();
            assert!(with_urls.contains("chat_url"));
            assert!(with_urls.contains("models_url"));

            set_provider(
                "demo",
                json!({
                    "kind": "openai",
                    "base_url": "https://api.example.com",
                    "api_key": "sk-secret",
                    "models": ["m1"],
                    "default": "m1"
                }),
            )
            .unwrap();
            let cleared = fs::read_to_string(get_reasonix_config_path()).unwrap();
            assert!(
                !cleared.contains("chat_url"),
                "absent chat_url must be removed from live TOML, got:\n{cleared}"
            );
            assert!(
                !cleared.contains("models_url"),
                "absent models_url must be removed from live TOML, got:\n{cleared}"
            );
        });
    }

    #[test]
    #[serial]
    fn mcp_plugins_round_trip() {
        with_temp_home(|| {
            set_mcp_plugin(
                "fetch",
                &json!({
                    "command": "uvx",
                    "args": ["mcp-server-fetch"],
                    "type": "stdio"
                }),
            )
            .unwrap();
            let plugins = get_mcp_plugins().unwrap();
            assert!(plugins.contains_key("fetch"));
            remove_mcp_plugin("fetch").unwrap();
            assert!(get_mcp_plugins().unwrap().is_empty());
        });
    }
}
