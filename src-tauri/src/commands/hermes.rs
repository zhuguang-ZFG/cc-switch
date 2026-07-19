//! Kimi Code commands (formerly Hermes Agent).
//!
//! MVP surface: import providers from live config and list live provider IDs.
//! Memory / Web UI commands from Hermes are intentionally not exposed.

use crate::error::AppError;
use crate::kimi_config;
use crate::services::provider::import_kimicode_providers_from_live as import_kimicode_from_live;
use crate::AppState;
use std::sync::Arc;
use tauri::State;

/// Import providers from Kimi Code live `config.toml` into the database.
#[tauri::command]
pub fn import_kimicode_providers_from_live(
    state: State<'_, Arc<AppState>>,
) -> Result<usize, String> {
    import_kimicode_from_live(state.inner().as_ref()).map_err(|e: AppError| e.to_string())
}

/// Get provider names present in the Kimi Code live config.
#[tauri::command]
pub fn get_kimicode_live_provider_ids() -> Result<Vec<String>, String> {
    let providers = kimi_config::get_providers().map_err(|e| e.to_string())?;
    Ok(providers.keys().cloned().collect())
}

/// Get a single Kimi Code provider fragment from live config.
#[tauri::command]
pub fn get_kimicode_live_provider(name: String) -> Result<Option<serde_json::Value>, String> {
    let providers = kimi_config::get_providers().map_err(|e| e.to_string())?;
    Ok(providers.get(&name).cloned())
}

/// Default model alias currently selected in live config.
#[tauri::command]
pub fn get_kimicode_default_model() -> Result<Option<String>, String> {
    kimi_config::get_default_model().map_err(|e| e.to_string())
}

/// Provider referenced by the current default model alias.
#[tauri::command]
pub fn get_kimicode_default_provider() -> Result<Option<String>, String> {
    kimi_config::get_default_provider().map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// Legacy Hermes command names kept as thin stubs so old frontends fail clearly
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn get_hermes_live_provider(_name: String) -> Result<Option<serde_json::Value>, String> {
    Err("Hermes was replaced by Kimi Code; use get_kimicode_live_provider".into())
}

#[tauri::command]
pub fn get_hermes_model_config() -> Result<Option<serde_json::Value>, String> {
    Err("Hermes was replaced by Kimi Code".into())
}

#[tauri::command]
pub async fn open_hermes_web_ui(
    _app: tauri::AppHandle,
    _path: Option<String>,
) -> Result<(), String> {
    Err("Hermes Web UI is not available; use Kimi Code CLI".into())
}

#[tauri::command]
pub async fn launch_hermes_dashboard() -> Result<(), String> {
    Err("Hermes dashboard is not available; use Kimi Code CLI".into())
}

#[tauri::command]
pub fn get_hermes_memory(_kind: String) -> Result<String, String> {
    Err("Hermes memory is not available".into())
}

#[tauri::command]
pub fn set_hermes_memory(_kind: String, _content: String) -> Result<(), String> {
    Err("Hermes memory is not available".into())
}

#[tauri::command]
pub fn get_hermes_memory_limits() -> Result<serde_json::Value, String> {
    Err("Hermes memory is not available".into())
}

#[tauri::command]
pub fn set_hermes_memory_enabled(_kind: String, _enabled: bool) -> Result<(), String> {
    Err("Hermes memory is not available".into())
}
