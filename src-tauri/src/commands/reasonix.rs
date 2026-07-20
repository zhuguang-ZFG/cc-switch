//! Reasonix CLI commands — live import / introspection (parity with Kimi Code).

use crate::error::AppError;
use crate::reasonix_config;
use crate::services::provider::import_reasonix_providers_from_live as import_reasonix_from_live;
use crate::AppState;
use std::sync::Arc;
use tauri::State;

/// Import providers from Reasonix live `config.toml` into the database.
#[tauri::command]
pub fn import_reasonix_providers_from_live(
    state: State<'_, Arc<AppState>>,
) -> Result<usize, String> {
    import_reasonix_from_live(state.inner().as_ref()).map_err(|e: AppError| e.to_string())
}

/// Provider names present in the Reasonix live config.
#[tauri::command]
pub fn get_reasonix_live_provider_ids() -> Result<Vec<String>, String> {
    let providers = reasonix_config::get_providers().map_err(|e| e.to_string())?;
    Ok(providers
        .keys()
        .filter(|name| {
            *name != reasonix_config::REASONIX_PROXY_PROVIDER && !name.starts_with("cc-switch-")
        })
        .cloned()
        .collect())
}

/// A single Reasonix provider fragment from live config.
#[tauri::command]
pub fn get_reasonix_live_provider(name: String) -> Result<Option<serde_json::Value>, String> {
    let providers = reasonix_config::get_providers().map_err(|e| e.to_string())?;
    Ok(providers.get(&name).cloned())
}

/// Current `default_model` from live config.
#[tauri::command]
pub fn get_reasonix_default_model() -> Result<Option<String>, String> {
    reasonix_config::get_default_model().map_err(|e| e.to_string())
}
