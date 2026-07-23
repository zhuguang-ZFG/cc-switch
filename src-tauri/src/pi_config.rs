//! Pi agent configuration (`~/.pi/agent/{models,auth,settings}.json`).
//!
//! Live layout (verified against real Pi installs):
//! - `models.json` — `providers.<id>` with name / baseUrl / api / apiKey / compat / models[]
//! - `auth.json` — typed credentials: `{ "<id>": { "type": "api_key", "key": "..." } }`
//! - `settings.json` — `defaultProvider` / `defaultModel` (current-switch semantics)
//!
//! `models-store.json` is Pi's built-in catalog and is never written by CC Switch.

use crate::config::{atomic_write, get_home_dir};
use crate::error::AppError;
use crate::settings::{effective_backup_retain_count, get_pi_override_dir};
use chrono::Local;
use indexmap::IndexMap;
use serde_json::{json, Map, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

pub const PI_PROXY_PROVIDER: &str = "cc-switch-proxy";
/// Must match `is_cc_switch_proxy_model` so the proxy rewrites it to the
/// selected SSOT provider's real model before upstream forward.
pub const PI_PROXY_MODEL: &str = "cc-switch-proxy-default";
pub const PI_PROXY_API_KEY: &str = "PROXY_MANAGED";
pub const PI_DEFAULT_API: &str = "openai-completions";

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct PiWriteOutcome {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub backup_path: Option<String>,
}

/// Composite snapshot stored in the live_backup slot.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, Default)]
pub struct PiSnapshot {
    #[serde(default)]
    pub models: Value,
    #[serde(default)]
    pub auth: Value,
    #[serde(default)]
    pub settings: Value,
}

fn write_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

// ============================================================================
// Paths
// ============================================================================

fn default_pi_agent_dir() -> PathBuf {
    get_home_dir().join(".pi").join("agent")
}

/// Resolve Pi agent directory.
///
/// Order:
/// 1. `PI_AGENT_HOME` env
/// 2. `PI_HOME` env → `<PI_HOME>/agent`
/// 3. CC Switch settings override (`pi_config_dir`)
/// 4. `~/.pi/agent`
///
/// env 优先于 settings 覆盖：PI_AGENT_HOME 并非 Pi 官方变量，仅作显式指定/
/// 测试隔离用途，显式设置者应赢过持久化覆盖（同时保证 cargo test 不会
/// 因本机配过 pi_config_dir 而写坏真实配置）。
pub fn get_pi_dir() -> PathBuf {
    if let Some(raw) = std::env::var_os("PI_AGENT_HOME") {
        let value = raw.to_string_lossy();
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }

    if let Some(raw) = std::env::var_os("PI_HOME") {
        let value = raw.to_string_lossy();
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed).join("agent");
        }
    }

    if let Some(override_dir) = get_pi_override_dir() {
        return override_dir;
    }

    default_pi_agent_dir()
}

pub fn get_models_path() -> PathBuf {
    get_pi_dir().join("models.json")
}

pub fn get_auth_path() -> PathBuf {
    get_pi_dir().join("auth.json")
}

pub fn get_settings_path() -> PathBuf {
    get_pi_dir().join("settings.json")
}

// ============================================================================
// JSON I/O
// ============================================================================

fn read_json_file(path: &Path) -> Result<Value, AppError> {
    if !path.exists() {
        return Ok(Value::Object(Map::new()));
    }
    let text = fs::read_to_string(path).map_err(|e| AppError::io(path, e))?;
    if text.trim().is_empty() {
        return Ok(Value::Object(Map::new()));
    }
    serde_json::from_str(&text).map_err(|e| {
        AppError::localized(
            "provider.pi.config.invalid_json",
            format!("Pi 配置 JSON 格式错误 ({}): {e}", path.display()),
            format!("Invalid Pi config JSON ({}): {e}", path.display()),
        )
    })
}

fn backup_file_if_exists(path: &Path, prefix: &str) -> Result<Option<String>, AppError> {
    if !path.exists() {
        return Ok(None);
    }
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    let stamp = Local::now().format("%Y%m%d_%H%M%S");
    let backup = parent.join(format!("{prefix}.bak.{stamp}"));
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
                        .is_some_and(|n| n.starts_with(&format!("{prefix}.bak.")))
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

fn write_json_file(path: &Path, value: &Value, backup_prefix: &str) -> Result<Option<String>, AppError> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::io(parent, e))?;
    }
    let backup_path = backup_file_if_exists(path, backup_prefix)?;
    let text = serde_json::to_string_pretty(value).map_err(|e| {
        AppError::Message(format!("Failed to serialize Pi config {}: {e}", path.display()))
    })?;
    // Pretty-print ends without trailing newline sometimes; keep stable JSON.
    let payload = if text.ends_with('\n') {
        text
    } else {
        format!("{text}\n")
    };
    atomic_write(path, payload.as_bytes())?;
    Ok(backup_path)
}

pub fn read_models() -> Result<Value, AppError> {
    read_json_file(&get_models_path())
}

pub fn read_auth() -> Result<Value, AppError> {
    read_json_file(&get_auth_path())
}

pub fn read_settings() -> Result<Value, AppError> {
    read_json_file(&get_settings_path())
}

/// Read a composite snapshot (for live_backup).
pub fn read_snapshot() -> Result<PiSnapshot, AppError> {
    Ok(PiSnapshot {
        models: read_models()?,
        auth: read_auth()?,
        settings: read_settings()?,
    })
}

pub fn read_snapshot_text() -> Result<String, AppError> {
    let snap = read_snapshot()?;
    serde_json::to_string_pretty(&snap)
        .map_err(|e| AppError::Message(format!("Failed to serialize Pi snapshot: {e}")))
}

/// Whether any live Pi config file exists with non-empty content.
pub fn has_live_config() -> bool {
    for path in [get_models_path(), get_auth_path(), get_settings_path()] {
        if path.exists() {
            if let Ok(text) = fs::read_to_string(&path) {
                if !text.trim().is_empty() && text.trim() != "{}" {
                    return true;
                }
            }
        }
    }
    false
}

pub fn write_snapshot(snapshot: &PiSnapshot) -> Result<PiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Pi config write lock poisoned".into()))?;

    let models_bak = write_json_file(&get_models_path(), &snapshot.models, "models.json")?;
    let _ = write_json_file(&get_auth_path(), &snapshot.auth, "auth.json")?;
    let _ = write_json_file(&get_settings_path(), &snapshot.settings, "settings.json")?;
    Ok(PiWriteOutcome {
        backup_path: models_bak,
    })
}

pub fn write_snapshot_text(text: &str) -> Result<PiWriteOutcome, AppError> {
    let snap: PiSnapshot = serde_json::from_str(text).map_err(|e| {
        AppError::localized(
            "provider.pi.snapshot.invalid",
            format!("Pi 备份快照格式错误: {e}"),
            format!("Invalid Pi backup snapshot: {e}"),
        )
    })?;
    write_snapshot(&snap)
}

// ============================================================================
// Providers
// ============================================================================

fn shape_error(file: &str) -> AppError {
    AppError::Message(format!(
        "Pi {file} 内容形状异常（非对象），已中止写入以防清盘；请手动检查该文件"
    ))
}

fn providers_map_mut(models: &mut Value) -> Result<&mut Map<String, Value>, AppError> {
    if !models.is_object() {
        return Err(shape_error("models.json"));
    }
    let root = models.as_object_mut().expect("object");
    let entry = root
        .entry("providers".to_string())
        .or_insert_with(|| json!({}));
    if !entry.is_object() {
        return Err(shape_error("models.json 的 providers"));
    }
    Ok(entry.as_object_mut().expect("providers object"))
}

fn providers_map_ref(models: &Value) -> Option<&Map<String, Value>> {
    models
        .get("providers")
        .and_then(Value::as_object)
}

pub fn get_providers() -> Result<IndexMap<String, Value>, AppError> {
    let models = read_models()?;
    let mut out = IndexMap::new();
    let Some(providers) = providers_map_ref(&models) else {
        return Ok(out);
    };
    for (id, value) in providers {
        let mut obj = match value.as_object() {
            Some(map) => map.clone(),
            None => continue,
        };
        if !obj.contains_key("name") {
            obj.insert("name".into(), Value::String(id.clone()));
        }
        out.insert(id.clone(), Value::Object(obj));
    }
    Ok(out)
}

fn extract_api_key(settings_config: &Value) -> Option<String> {
    settings_config
        .get("apiKey")
        .or_else(|| settings_config.get("api_key"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn default_compat() -> Value {
    json!({
        "supportsStore": false,
        "supportsDeveloperRole": false,
        "maxTokensField": "max_tokens"
    })
}

fn normalize_models_array(settings_config: &Value) -> Vec<Value> {
    let Some(models) = settings_config.get("models") else {
        return Vec::new();
    };
    match models {
        Value::Array(arr) => arr
            .iter()
            .filter_map(|item| {
                if let Some(id) = item.as_str() {
                    let id = id.trim();
                    if id.is_empty() {
                        return None;
                    }
                    return Some(json!({
                        "id": id,
                        "name": id,
                        "input": ["text"],
                        "contextWindow": 128000,
                        "maxTokens": 8192,
                        "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 }
                    }));
                }
                let obj = item.as_object()?;
                let id = obj
                    .get("id")
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|s| !s.is_empty())?;
                let mut model = obj.clone();
                model
                    .entry("id".to_string())
                    .or_insert_with(|| Value::String(id.to_string()));
                model
                    .entry("name".to_string())
                    .or_insert_with(|| Value::String(id.to_string()));
                model
                    .entry("input".to_string())
                    .or_insert_with(|| json!(["text"]));
                model
                    .entry("contextWindow".to_string())
                    .or_insert_with(|| json!(128000));
                model
                    .entry("maxTokens".to_string())
                    .or_insert_with(|| json!(8192));
                model.entry("cost".to_string()).or_insert_with(|| {
                    json!({ "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 })
                });
                Some(Value::Object(model))
            })
            .collect(),
        Value::Object(map) => map
            .keys()
            .map(|id| {
                json!({
                    "id": id,
                    "name": id,
                    "input": ["text"],
                    "contextWindow": 128000,
                    "maxTokens": 8192,
                    "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 }
                })
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn resolve_default_model(settings_config: &Value, models: &[Value]) -> Option<String> {
    if let Some(dm) = settings_config
        .get("defaultModel")
        .or_else(|| settings_config.get("default_model"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        return Some(dm.to_string());
    }
    models
        .first()
        .and_then(|m| m.get("id"))
        .and_then(Value::as_str)
        .map(str::to_string)
}

/// 以 live 现有条目为底、SSOT 构建结果为覆盖层的深合并：
/// 保留 live 侧多出的字段（用户手改 / 更丰富的元数据），SSOT 键覆盖冲突项。
/// models 数组按 id 逐对象合并（live 对象元数据如 reasoning/contextWindow 不丢）。
fn deep_merge_provider_entry(existing: &Value, built: Value) -> Value {
    if !built.is_object() {
        return built;
    }
    let Some(base) = existing.as_object() else {
        return built;
    };
    let Value::Object(over) = built else {
        unreachable!("checked is_object above")
    };
    let mut merged = base.clone();
    for (k, v) in over {
        match (k.as_str(), merged.get(&k).cloned(), v) {
            ("models", Some(Value::Array(old)), Value::Array(new)) => {
                merged.insert(k, Value::Array(merge_models_by_id(&old, &new)));
            }
            ("compat", Some(Value::Object(old)), Value::Object(new)) => {
                let mut c = old;
                for (ck, cv) in new {
                    c.insert(ck, cv);
                }
                merged.insert(k, Value::Object(c));
            }
            (_, _, v) => {
                merged.insert(k, v);
            }
        }
    }
    Value::Object(merged)
}

fn merge_models_by_id(old: &[Value], new: &[Value]) -> Vec<Value> {
    // normalize_models_array 会给 SSOT 模型对象填默认值（contextWindow 128000、
    // maxTokens 8192、零 cost、input ["text"]）。这些填充值不代表用户意图，
    // 合并时不得覆盖 live 侧的显式值；SSOT 值与填充默认不同才算显式配置。
    fn is_fill_default(key: &str, v: &Value) -> bool {
        match key {
            "contextWindow" => *v == json!(128000),
            "maxTokens" => *v == json!(8192),
            "cost" => *v == json!({ "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 }),
            "input" => *v == json!(["text"]),
            _ => false,
        }
    }
    new.iter()
        .map(|nm| {
            if let Some(id) = nm.get("id").and_then(|i| i.as_str()) {
                if let Some(om) = old
                    .iter()
                    .find(|o| o.get("id").and_then(|i| i.as_str()) == Some(id))
                {
                    if let (Some(ob), Value::Object(nb)) = (om.as_object(), nm.clone()) {
                        let mut m = ob.clone();
                        for (k, v) in nb {
                            if m.contains_key(&k) && is_fill_default(&k, &v) {
                                continue; // 填充默认值让位给 live 显式值
                            }
                            m.insert(k, v);
                        }
                        return Value::Object(m);
                    }
                }
            }
            nm.clone()
        })
        .collect()
}

fn build_provider_entry(name: &str, settings_config: &Value) -> Result<(Value, Option<String>), AppError> {
    let object = settings_config.as_object().ok_or_else(|| {
        AppError::localized(
            "provider.pi.settings.not_object",
            "Pi 供应商配置必须是对象",
            "Pi provider settings must be an object",
        )
    })?;

    let display_name = object
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(name);

    let base_url = object
        .get("baseUrl")
        .or_else(|| object.get("base_url"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            AppError::localized(
                "provider.pi.base_url.missing",
                "Pi 供应商缺少 baseUrl",
                "Pi provider is missing baseUrl",
            )
        })?;

    let api = object
        .get("api")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(PI_DEFAULT_API);

    let models = normalize_models_array(settings_config);
    if models.is_empty() {
        return Err(AppError::localized(
            "provider.pi.models.missing",
            "Pi 供应商至少需要一个模型",
            "Pi provider requires at least one model",
        ));
    }

    let api_key = extract_api_key(settings_config);
    let compat = object
        .get("compat")
        .cloned()
        .unwrap_or_else(default_compat);

    let mut entry = Map::new();
    entry.insert("name".into(), Value::String(display_name.to_string()));
    entry.insert("baseUrl".into(), Value::String(base_url.to_string()));
    entry.insert("api".into(), Value::String(api.to_string()));
    if let Some(ref key) = api_key {
        entry.insert("apiKey".into(), Value::String(key.clone()));
    }
    entry.insert("compat".into(), compat);
    entry.insert("models".into(), Value::Array(models));

    // Preserve optional headers / extra fields (except secrets handled above).
    for (k, v) in object {
        if matches!(
            k.as_str(),
            "name"
                | "baseUrl"
                | "base_url"
                | "api"
                | "apiKey"
                | "api_key"
                | "compat"
                | "models"
                | "defaultModel"
                | "default_model"
        ) {
            continue;
        }
        entry.entry(k.clone()).or_insert_with(|| v.clone());
    }

    Ok((Value::Object(entry), api_key))
}

fn upsert_auth_key(auth: &mut Value, provider_id: &str, api_key: &str) -> Result<(), AppError> {
    if !auth.is_object() {
        return Err(shape_error("auth.json"));
    }
    let root = auth.as_object_mut().expect("object");
    root.insert(
        provider_id.to_string(),
        json!({
            "type": "api_key",
            "key": api_key
        }),
    );
    Ok(())
}

fn remove_auth_key(auth: &mut Value, provider_id: &str) {
    if let Some(root) = auth.as_object_mut() {
        root.remove(provider_id);
    }
}

fn set_settings_defaults(
    settings: &mut Value,
    provider_id: &str,
    model_id: Option<&str>,
) -> Result<(), AppError> {
    if !settings.is_object() {
        return Err(shape_error("settings.json"));
    }
    let root = settings.as_object_mut().expect("object");
    root.insert(
        "defaultProvider".into(),
        Value::String(provider_id.to_string()),
    );
    if let Some(model) = model_id.map(str::trim).filter(|s| !s.is_empty()) {
        root.insert("defaultModel".into(), Value::String(model.to_string()));
    }
    Ok(())
}

pub fn set_provider(name: &str, settings_config: Value) -> Result<PiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Pi config write lock poisoned".into()))?;

    let name = name.trim();
    if name.is_empty() {
        return Err(AppError::localized(
            "provider.pi.name.empty",
            "Pi 供应商名称不能为空",
            "Pi provider name cannot be empty",
        ));
    }

    let (entry, api_key) = build_provider_entry(name, &settings_config)?;
    let mut models = read_models()?;
    let providers = providers_map_mut(&mut models)?;
    // deep-merge：以 live 现有条目为底，SSOT 构建结果覆盖，保住 live 侧元数据
    let entry = match providers.get(name) {
        Some(existing) => deep_merge_provider_entry(existing, entry),
        None => entry,
    };
    providers.insert(name.to_string(), entry);

    let mut auth = read_auth()?;
    if let Some(ref key) = api_key {
        upsert_auth_key(&mut auth, name, key)?;
    }

    let models_bak = write_json_file(&get_models_path(), &models, "models.json")?;
    let _ = write_json_file(&get_auth_path(), &auth, "auth.json")?;
    Ok(PiWriteOutcome {
        backup_path: models_bak,
    })
}

pub fn remove_provider(name: &str) -> Result<PiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Pi config write lock poisoned".into()))?;

    let name = name.trim();
    let mut models = read_models()?;
    let providers = providers_map_mut(&mut models)?;
    let removed = providers.remove(name).is_some();

    let mut settings = read_settings()?;
    let current = settings
        .get("defaultProvider")
        .and_then(Value::as_str)
        .map(str::to_string);
    if current.as_deref() == Some(name) {
        let fallback = providers
            .keys()
            .find(|k| k.as_str() != PI_PROXY_PROVIDER)
            .cloned();
        if let Some(next) = fallback {
            let model = providers
                .get(&next)
                .and_then(|p| p.get("models"))
                .and_then(Value::as_array)
                .and_then(|arr| arr.first())
                .and_then(|m| m.get("id"))
                .and_then(Value::as_str)
                .map(str::to_string);
            set_settings_defaults(&mut settings, &next, model.as_deref())?;
        } else if let Some(root) = settings.as_object_mut() {
            root.remove("defaultProvider");
            root.remove("defaultModel");
        }
    }

    let mut auth = read_auth()?;
    remove_auth_key(&mut auth, name);

    // auth/settings 清理始终落盘（即使 models.json 里本就没有该 provider），
    // 否则部分写入失败后的残留凭据和悬空 default 永远清不掉
    let _ = write_json_file(&get_settings_path(), &settings, "settings.json")?;
    let _ = write_json_file(&get_auth_path(), &auth, "auth.json")?;

    if !removed {
        return Ok(PiWriteOutcome::default());
    }

    let models_bak = write_json_file(&get_models_path(), &models, "models.json")?;
    let _ = write_json_file(&get_auth_path(), &auth, "auth.json")?;
    let _ = write_json_file(&get_settings_path(), &settings, "settings.json")?;
    Ok(PiWriteOutcome {
        backup_path: models_bak,
    })
}

/// Switch current provider by updating settings.json defaults (additive mode).
pub fn apply_switch_defaults(
    provider_id: &str,
    settings_config: &Value,
) -> Result<PiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Pi config write lock poisoned".into()))?;

    let provider_id = provider_id.trim();
    let (entry, api_key) = build_provider_entry(provider_id, settings_config)?;
    let models_list = normalize_models_array(settings_config);
    let default_model = resolve_default_model(settings_config, &models_list);

    let mut models = read_models()?;
    let providers = providers_map_mut(&mut models)?;
    // deep-merge：以 live 现有条目为底，SSOT 构建结果覆盖，保住 live 侧元数据
    let entry = match providers.get(provider_id) {
        Some(existing) => deep_merge_provider_entry(existing, entry),
        None => entry,
    };
    providers.insert(provider_id.to_string(), entry);

    let mut auth = read_auth()?;
    if let Some(ref key) = api_key {
        upsert_auth_key(&mut auth, provider_id, key)?;
    }

    let mut settings = read_settings()?;
    set_settings_defaults(&mut settings, provider_id, default_model.as_deref())?;

    let models_bak = write_json_file(&get_models_path(), &models, "models.json")?;
    let _ = write_json_file(&get_auth_path(), &auth, "auth.json")?;
    let _ = write_json_file(&get_settings_path(), &settings, "settings.json")?;
    Ok(PiWriteOutcome {
        backup_path: models_bak,
    })
}

pub fn get_default_provider() -> Result<Option<String>, AppError> {
    let settings = read_settings()?;
    Ok(settings
        .get("defaultProvider")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string))
}

pub fn get_default_model() -> Result<Option<String>, AppError> {
    let settings = read_settings()?;
    Ok(settings
        .get("defaultModel")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string))
}

// ============================================================================
// Snapshot text helpers (edit backup during takeover without touching live)
// ============================================================================

pub fn upsert_provider_into_snapshot_text(
    text: &str,
    provider_id: &str,
    settings_config: &Value,
) -> Result<String, AppError> {
    let mut snap: PiSnapshot = serde_json::from_str(text).map_err(|e| {
        AppError::Message(format!("Invalid Pi snapshot: {e}"))
    })?;
    let (entry, api_key) = build_provider_entry(provider_id.trim(), settings_config)?;
    providers_map_mut(&mut snap.models)?.insert(provider_id.trim().to_string(), entry);
    if let Some(key) = api_key {
        upsert_auth_key(&mut snap.auth, provider_id.trim(), &key)?;
    }
    serde_json::to_string_pretty(&snap)
        .map_err(|e| AppError::Message(format!("Failed to serialize Pi snapshot: {e}")))
}

pub fn apply_switch_defaults_to_snapshot_text(
    text: &str,
    provider_id: &str,
    settings_config: &Value,
) -> Result<String, AppError> {
    let mut snap: PiSnapshot = serde_json::from_str(text).map_err(|e| {
        AppError::Message(format!("Invalid Pi snapshot: {e}"))
    })?;
    let provider_id = provider_id.trim();
    let (entry, api_key) = build_provider_entry(provider_id, settings_config)?;
    let models_list = normalize_models_array(settings_config);
    let default_model = resolve_default_model(settings_config, &models_list);
    providers_map_mut(&mut snap.models)?.insert(provider_id.to_string(), entry);
    if let Some(key) = api_key {
        upsert_auth_key(&mut snap.auth, provider_id, &key)?;
    }
    set_settings_defaults(&mut snap.settings, provider_id, default_model.as_deref())?;
    serde_json::to_string_pretty(&snap)
        .map_err(|e| AppError::Message(format!("Failed to serialize Pi snapshot: {e}")))
}

pub fn remove_provider_from_snapshot_text(
    text: &str,
    name: &str,
) -> Result<String, AppError> {
    let mut snap: PiSnapshot = serde_json::from_str(text).map_err(|e| {
        AppError::Message(format!("Invalid Pi snapshot: {e}"))
    })?;
    let name = name.trim();
    let providers = providers_map_mut(&mut snap.models)?;
    providers.remove(name);
    remove_auth_key(&mut snap.auth, name);

    if snap
        .settings
        .get("defaultProvider")
        .and_then(Value::as_str)
        == Some(name)
    {
        let fallback = providers
            .keys()
            .find(|k| k.as_str() != PI_PROXY_PROVIDER)
            .cloned();
        if let Some(next) = fallback {
            let model = providers
                .get(&next)
                .and_then(|p| p.get("models"))
                .and_then(Value::as_array)
                .and_then(|arr| arr.first())
                .and_then(|m| m.get("id"))
                .and_then(Value::as_str)
                .map(str::to_string);
            set_settings_defaults(&mut snap.settings, &next, model.as_deref())?;
        } else if let Some(root) = snap.settings.as_object_mut() {
            root.remove("defaultProvider");
            root.remove("defaultModel");
        }
    }

    serde_json::to_string_pretty(&snap)
        .map_err(|e| AppError::Message(format!("Failed to serialize Pi snapshot: {e}")))
}

pub fn provider_exists_in_snapshot_text(text: &str, name: &str) -> Result<bool, AppError> {
    let snap: PiSnapshot = serde_json::from_str(text).map_err(|e| {
        AppError::Message(format!("Invalid Pi snapshot: {e}"))
    })?;
    Ok(providers_map_ref(&snap.models)
        .is_some_and(|p| p.contains_key(name.trim())))
}

// ============================================================================
// Proxy takeover
// ============================================================================

fn proxy_provider_entry(proxy_base_url: &str) -> Value {
    json!({
        "name": "CC Switch Proxy",
        "baseUrl": proxy_base_url.trim_end_matches('/'),
        "api": PI_DEFAULT_API,
        "apiKey": PI_PROXY_API_KEY,
        "compat": {
            "supportsStore": false,
            "supportsDeveloperRole": false,
            "maxTokensField": "max_tokens"
        },
        "models": [{
            "id": PI_PROXY_MODEL,
            "name": "CC Switch Proxy",
            "input": ["text"],
            "contextWindow": 128000,
            "maxTokens": 8192,
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 }
        }]
    })
}

/// Project Pi onto the stable local OpenAI ingress.
pub fn apply_proxy_takeover(proxy_base_url: &str) -> Result<PiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Pi config write lock poisoned".into()))?;

    let mut models = read_models()?;
    providers_map_mut(&mut models)?
        .insert(PI_PROXY_PROVIDER.to_string(), proxy_provider_entry(proxy_base_url));

    let mut auth = read_auth()?;
    upsert_auth_key(&mut auth, PI_PROXY_PROVIDER, PI_PROXY_API_KEY)?;

    let mut settings = read_settings()?;
    set_settings_defaults(&mut settings, PI_PROXY_PROVIDER, Some(PI_PROXY_MODEL))?;

    let models_bak = write_json_file(&get_models_path(), &models, "models.json")?;
    let _ = write_json_file(&get_auth_path(), &auth, "auth.json")?;
    let _ = write_json_file(&get_settings_path(), &settings, "settings.json")?;
    Ok(PiWriteOutcome {
        backup_path: models_bak,
    })
}

pub fn is_proxy_takeover_active() -> Result<bool, AppError> {
    is_proxy_takeover_active_for_url(None)
}

/// Detect CC Switch proxy takeover. When `expected_base_url` is set, require an
/// exact baseUrl match. Otherwise require `/pi/v1` in the URL.
pub fn is_proxy_takeover_active_for_url(expected_base_url: Option<&str>) -> Result<bool, AppError> {
    let models = read_models()?;
    let Some(providers) = providers_map_ref(&models) else {
        return Ok(false);
    };
    let Some(provider) = providers.get(PI_PROXY_PROVIDER).and_then(Value::as_object) else {
        return Ok(false);
    };

    let api_key_ok = provider
        .get("apiKey")
        .and_then(Value::as_str)
        .is_some_and(|k| k == PI_PROXY_API_KEY);

    // Also accept auth.json typed key when models.json omitted apiKey.
    let auth_ok = if api_key_ok {
        true
    } else {
        read_auth()?
            .get(PI_PROXY_PROVIDER)
            .and_then(Value::as_object)
            .is_some_and(|cred| {
                cred.get("type").and_then(Value::as_str) == Some("api_key")
                    && cred.get("key").and_then(Value::as_str) == Some(PI_PROXY_API_KEY)
            })
    };

    let url_ok = provider
        .get("baseUrl")
        .and_then(Value::as_str)
        .is_some_and(|url| {
            if let Some(expected) = expected_base_url.map(str::trim).filter(|s| !s.is_empty()) {
                url.trim_end_matches('/') == expected.trim_end_matches('/')
            } else {
                url.contains("/pi/v1")
            }
        });

    let settings = read_settings()?;
    let defaults_ok = settings
        .get("defaultProvider")
        .and_then(Value::as_str)
        .is_some_and(|p| p == PI_PROXY_PROVIDER);

    Ok(auth_ok && url_ok && defaults_ok)
}

/// Remove only the CC Switch-owned projection if no backup is available.
pub fn clear_proxy_takeover() -> Result<PiWriteOutcome, AppError> {
    let _guard = write_lock()
        .lock()
        .map_err(|_| AppError::Message("Pi config write lock poisoned".into()))?;

    let mut models = read_models()?;
    let providers = providers_map_mut(&mut models)?;
    let removed = providers.remove(PI_PROXY_PROVIDER).is_some();

    let mut auth = read_auth()?;
    remove_auth_key(&mut auth, PI_PROXY_PROVIDER);

    let mut settings = read_settings()?;
    let mut settings_changed = false;
    if settings
        .get("defaultProvider")
        .and_then(Value::as_str)
        == Some(PI_PROXY_PROVIDER)
    {
        let fallback = providers
            .keys()
            .find(|k| k.as_str() != PI_PROXY_PROVIDER)
            .cloned();
        if let Some(next) = fallback {
            let model = providers
                .get(&next)
                .and_then(|p| p.get("models"))
                .and_then(Value::as_array)
                .and_then(|arr| arr.first())
                .and_then(|m| m.get("id"))
                .and_then(Value::as_str)
                .map(str::to_string);
            set_settings_defaults(&mut settings, &next, model.as_deref())?;
        } else if let Some(root) = settings.as_object_mut() {
            root.remove("defaultProvider");
            root.remove("defaultModel");
        }
        settings_changed = true;
    }

    // auth 里的 PROXY_MANAGED 凭据始终落盘清除（即使 models/settings 已无接管痕迹），
    // 否则部分清理后的残留 key 永远清不掉
    let _ = write_json_file(&get_auth_path(), &auth, "auth.json")?;

    if !removed && !settings_changed {
        return Ok(PiWriteOutcome::default());
    }

    let models_bak = write_json_file(&get_models_path(), &models, "models.json")?;
    let _ = write_json_file(&get_auth_path(), &auth, "auth.json")?;
    let _ = write_json_file(&get_settings_path(), &settings, "settings.json")?;
    Ok(PiWriteOutcome {
        backup_path: models_bak,
    })
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    struct EnvGuard {
        agent_home: Option<std::ffi::OsString>,
        pi_home: Option<std::ffi::OsString>,
    }

    impl EnvGuard {
        fn set_temp_home(dir: &Path) -> Self {
            let agent_home = std::env::var_os("PI_AGENT_HOME");
            let pi_home = std::env::var_os("PI_HOME");
            std::env::set_var("PI_AGENT_HOME", dir);
            std::env::remove_var("PI_HOME");
            Self {
                agent_home,
                pi_home,
            }
        }
    }

    impl Drop for EnvGuard {
        fn drop(&mut self) {
            match &self.agent_home {
                Some(v) => std::env::set_var("PI_AGENT_HOME", v),
                None => std::env::remove_var("PI_AGENT_HOME"),
            }
            match &self.pi_home {
                Some(v) => std::env::set_var("PI_HOME", v),
                None => std::env::remove_var("PI_HOME"),
            }
        }
    }

    fn sample_settings(model: &str) -> Value {
        json!({
            "name": "Demo",
            "baseUrl": "https://api.example.com/v1",
            "api": "openai-completions",
            "apiKey": "sk-demo",
            "models": [{ "id": model, "name": model }],
            "defaultModel": model
        })
    }

    #[test]
    fn set_provider_writes_models_and_typed_auth() {
        let _lock = TEST_LOCK.lock().unwrap();
        let tmp = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set_temp_home(tmp.path());

        set_provider("demo", sample_settings("gpt-test")).unwrap();

        let models = read_models().unwrap();
        let provider = models
            .pointer("/providers/demo")
            .expect("provider written");
        assert_eq!(provider["baseUrl"], "https://api.example.com/v1");
        assert_eq!(provider["apiKey"], "sk-demo");
        assert_eq!(provider["models"][0]["id"], "gpt-test");

        let auth = read_auth().unwrap();
        assert_eq!(auth["demo"]["type"], "api_key");
        assert_eq!(auth["demo"]["key"], "sk-demo");
    }

    #[test]
    fn apply_switch_defaults_updates_settings() {
        let _lock = TEST_LOCK.lock().unwrap();
        let tmp = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set_temp_home(tmp.path());

        apply_switch_defaults("demo", &sample_settings("gpt-test")).unwrap();
        assert_eq!(get_default_provider().unwrap().as_deref(), Some("demo"));
        assert_eq!(get_default_model().unwrap().as_deref(), Some("gpt-test"));
    }

    #[test]
    fn apply_proxy_takeover_and_clear_is_lossless_via_snapshot() {
        let _lock = TEST_LOCK.lock().unwrap();
        let tmp = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set_temp_home(tmp.path());

        set_provider("demo", sample_settings("gpt-test")).unwrap();
        apply_switch_defaults("demo", &sample_settings("gpt-test")).unwrap();
        let original = read_snapshot_text().unwrap();

        apply_proxy_takeover("http://127.0.0.1:15721/pi/v1").unwrap();
        assert!(is_proxy_takeover_active().unwrap());
        assert_eq!(
            get_default_provider().unwrap().as_deref(),
            Some(PI_PROXY_PROVIDER)
        );
        let models = read_models().unwrap();
        assert!(models.pointer("/providers/cc-switch-proxy").is_some());
        assert!(models.pointer("/providers/demo").is_some());
        assert!(is_proxy_takeover_active_for_url(Some(
            "http://127.0.0.1:15721/pi/v1"
        ))
        .unwrap());
        assert!(!is_proxy_takeover_active_for_url(Some(
            "http://127.0.0.1:9999/pi/v1"
        ))
        .unwrap());

        // Localhost without /pi/v1 must not count as takeover.
        {
            let mut m = read_models().unwrap();
            if let Some(p) = providers_map_mut(&mut m).unwrap().get_mut(PI_PROXY_PROVIDER) {
                p["baseUrl"] = json!("http://127.0.0.1:9999/v1");
            }
            write_json_file(&get_models_path(), &m, "models.json").unwrap();
            assert!(
                !is_proxy_takeover_active().unwrap(),
                "bare localhost without /pi/v1 is not takeover"
            );
        }

        write_snapshot_text(&original).unwrap();
        assert!(!is_proxy_takeover_active().unwrap());
        assert_eq!(get_default_provider().unwrap().as_deref(), Some("demo"));
        let auth = read_auth().unwrap();
        assert_eq!(auth["demo"]["key"], "sk-demo");
        assert!(auth.get(PI_PROXY_PROVIDER).is_none());
    }

    #[test]
    fn clear_proxy_takeover_without_snapshot_removes_projection() {
        let _lock = TEST_LOCK.lock().unwrap();
        let tmp = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set_temp_home(tmp.path());

        set_provider("demo", sample_settings("gpt-test")).unwrap();
        apply_switch_defaults("demo", &sample_settings("gpt-test")).unwrap();
        apply_proxy_takeover("http://127.0.0.1:15721/pi/v1").unwrap();
        clear_proxy_takeover().unwrap();
        assert!(!is_proxy_takeover_active().unwrap());
        let models = read_models().unwrap();
        assert!(models.pointer("/providers/cc-switch-proxy").is_none());
        assert!(models.pointer("/providers/demo").is_some());
        assert_eq!(get_default_provider().unwrap().as_deref(), Some("demo"));
    }

    #[test]
    fn shape_error_on_array_auth_aborts_write() {
        let _lock = TEST_LOCK.lock().unwrap();
        let tmp = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set_temp_home(tmp.path());

        // auth.json 形状异常（数组）时必须报错中止，不能静默清盘
        fs::create_dir_all(tmp.path()).unwrap();
        fs::write(get_auth_path(), "[]").unwrap();
        let original = fs::read_to_string(get_auth_path()).unwrap();

        let err = set_provider("demo", sample_settings("gpt-test")).unwrap_err();
        assert!(err.to_string().contains("auth.json"), "{err}");
        assert_eq!(
            fs::read_to_string(get_auth_path()).unwrap(),
            original,
            "auth.json 必须原样保留"
        );
    }

    #[test]
    fn shape_error_on_array_settings_aborts_write() {
        let _lock = TEST_LOCK.lock().unwrap();
        let tmp = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set_temp_home(tmp.path());

        fs::write(get_settings_path(), "[]").unwrap();
        let original = fs::read_to_string(get_settings_path()).unwrap();

        let err = apply_switch_defaults("demo", &sample_settings("gpt-test")).unwrap_err();
        assert!(err.to_string().contains("settings.json"), "{err}");
        assert_eq!(
            fs::read_to_string(get_settings_path()).unwrap(),
            original,
            "settings.json 必须原样保留"
        );
    }

    #[test]
    fn deep_merge_preserves_live_model_metadata() {
        let _lock = TEST_LOCK.lock().unwrap();
        let tmp = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set_temp_home(tmp.path());

        // 先在 live 造一个带元数据的条目（模拟用户手改 / 更丰富的 live 配置）
        set_provider("demo", sample_settings("gpt-test")).unwrap();
        let mut models = read_models().unwrap();
        models["providers"]["demo"]["models"][0]["reasoning"] = json!(true);
        models["providers"]["demo"]["models"][0]["contextWindow"] = json!(1048576);
        models["providers"]["demo"]["compat"]["supportsReasoningEffort"] = json!(true);
        models["providers"]["demo"]["customField"] = json!("keep-me");
        write_json_file(&get_models_path(), &models, "models.json").unwrap();

        // SSOT 侧只有裸字段，再次写入不应抹掉 live 元数据
        set_provider("demo", sample_settings("gpt-test")).unwrap();
        let after = read_models().unwrap();
        let p = &after["providers"]["demo"];
        assert_eq!(p["models"][0]["reasoning"], json!(true));
        assert_eq!(p["models"][0]["contextWindow"], json!(1048576));
        assert_eq!(p["compat"]["supportsReasoningEffort"], json!(true));
        assert_eq!(p["customField"], json!("keep-me"));
    }

    #[test]
    fn auth_requires_type_field_in_projection() {
        let _lock = TEST_LOCK.lock().unwrap();
        let tmp = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set_temp_home(tmp.path());

        set_provider("demo", sample_settings("gpt-test")).unwrap();
        let auth_text = fs::read_to_string(get_auth_path()).unwrap();
        assert!(auth_text.contains("\"type\""));
        assert!(auth_text.contains("api_key"));
    }
}
