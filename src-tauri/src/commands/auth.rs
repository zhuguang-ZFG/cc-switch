use chrono::Utc;
use serde::Deserialize;
use serde_json::Value;
use std::sync::Arc;
use tauri::State;

use crate::commands::codex_oauth::CodexOAuthState;
use crate::commands::copilot::CopilotAuthState;
use crate::kimi_config::{
    self, KimiOAuthToken, KIMI_API_BASE_URL, KIMI_OAUTH_CLIENT_ID, MANAGED_KIMI_PROVIDER,
};
use crate::proxy::providers::codex_oauth_auth::CodexOAuthError;
use crate::proxy::providers::copilot_auth::{
    CopilotAuthError, GitHubAccount, GitHubDeviceCodeResponse,
};
use crate::services::provider::import_kimicode_providers_from_live;
use crate::AppState;

const AUTH_PROVIDER_GITHUB_COPILOT: &str = "github_copilot";
const AUTH_PROVIDER_CODEX_OAUTH: &str = "codex_oauth";
const AUTH_PROVIDER_KIMI_OAUTH: &str = "kimi_oauth";

#[derive(Debug, Deserialize)]
struct KimiDeviceAuthorization {
    device_code: String,
    user_code: String,
    #[serde(default)]
    verification_uri: String,
    // Optional per RFC 8628; fall back to verification_uri when absent.
    #[serde(default)]
    verification_uri_complete: String,
    expires_in: Option<u64>,
    #[serde(default = "default_poll_interval")]
    interval: u64,
}

fn default_poll_interval() -> u64 {
    5
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ManagedAuthAccount {
    pub id: String,
    pub provider: String,
    pub login: String,
    pub avatar_url: Option<String>,
    pub authenticated_at: i64,
    pub is_default: bool,
    pub github_domain: String,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ManagedAuthStatus {
    pub provider: String,
    pub authenticated: bool,
    pub default_account_id: Option<String>,
    pub migration_error: Option<String>,
    pub accounts: Vec<ManagedAuthAccount>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ManagedAuthDeviceCodeResponse {
    pub provider: String,
    pub device_code: String,
    pub user_code: String,
    pub verification_uri: String,
    pub expires_in: u64,
    pub interval: u64,
}

fn ensure_auth_provider(auth_provider: &str) -> Result<&'static str, String> {
    match auth_provider {
        AUTH_PROVIDER_GITHUB_COPILOT => Ok(AUTH_PROVIDER_GITHUB_COPILOT),
        AUTH_PROVIDER_CODEX_OAUTH => Ok(AUTH_PROVIDER_CODEX_OAUTH),
        AUTH_PROVIDER_KIMI_OAUTH => Ok(AUTH_PROVIDER_KIMI_OAUTH),
        _ => Err(format!("Unsupported auth provider: {auth_provider}")),
    }
}

fn kimi_account(token: &KimiOAuthToken) -> ManagedAuthAccount {
    ManagedAuthAccount {
        id: "kimi-code".to_string(),
        provider: AUTH_PROVIDER_KIMI_OAUTH.to_string(),
        login: "Kimi Code".to_string(),
        avatar_url: None,
        authenticated_at: token.expires_at.saturating_sub(token.expires_in),
        is_default: true,
        github_domain: String::new(),
    }
}

fn kimi_status() -> Result<ManagedAuthStatus, String> {
    let token = kimi_config::load_oauth_token().map_err(|e| e.to_string())?;
    let configured =
        kimi_config::is_managed_provider(MANAGED_KIMI_PROVIDER).map_err(|e| e.to_string())?;
    let token = token.filter(|_| configured);
    let accounts = token.as_ref().map(kimi_account).into_iter().collect();
    Ok(ManagedAuthStatus {
        provider: AUTH_PROVIDER_KIMI_OAUTH.to_string(),
        authenticated: token.is_some(),
        default_account_id: token.as_ref().map(|_| "kimi-code".to_string()),
        migration_error: None,
        accounts,
    })
}

async fn kimi_start_device_flow() -> Result<ManagedAuthDeviceCodeResponse, String> {
    let client = reqwest::Client::builder()
        .default_headers(kimi_config::get_kimi_device_headers()?)
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())?;
    let response = client
        .post(format!(
            "{}/api/oauth/device_authorization",
            kimi_config::get_kimi_oauth_host()
        ))
        .form(&[("client_id", KIMI_OAUTH_CLIENT_ID)])
        .send()
        .await
        .map_err(|e| format!("Kimi device authorization failed: {e}"))?;
    let status = response.status();
    let body = response
        .text()
        .await
        .map_err(|e| format!("Failed to read Kimi authorization response: {e}"))?;
    if !status.is_success() {
        return Err(format!(
            "Kimi device authorization failed (HTTP {}): {}",
            status.as_u16(),
            body
        ));
    }
    let result: KimiDeviceAuthorization = serde_json::from_str(&body)
        .map_err(|e| format!("Invalid Kimi device authorization response: {e}"))?;
    let verification_uri = if result.verification_uri_complete.is_empty() {
        result.verification_uri
    } else {
        result.verification_uri_complete
    };
    Ok(ManagedAuthDeviceCodeResponse {
        provider: AUTH_PROVIDER_KIMI_OAUTH.to_string(),
        device_code: result.device_code,
        user_code: result.user_code,
        verification_uri,
        expires_in: result.expires_in.unwrap_or(600),
        interval: result.interval,
    })
}

async fn kimi_poll_device_flow(
    device_code: &str,
    state: &AppState,
) -> Result<Option<ManagedAuthAccount>, String> {
    let client = reqwest::Client::builder()
        .default_headers(kimi_config::get_kimi_device_headers()?)
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())?;
    let response = client
        .post(format!(
            "{}/api/oauth/token",
            kimi_config::get_kimi_oauth_host()
        ))
        .form(&[
            ("client_id", KIMI_OAUTH_CLIENT_ID),
            ("device_code", device_code),
            ("grant_type", "urn:ietf:params:oauth:grant-type:device_code"),
        ])
        .send()
        .await
        .map_err(|e| format!("Kimi token polling failed: {e}"))?;
    let status = response.status();
    let payload: Value = response
        .json()
        .await
        .map_err(|e| format!("Invalid Kimi token response: {e}"))?;
    if !status.is_success() {
        let code = payload.get("error").and_then(Value::as_str).unwrap_or("");
        if matches!(code, "authorization_pending" | "slow_down") {
            return Ok(None);
        }
        let detail = payload
            .get("error_description")
            .or_else(|| payload.get("message"))
            .and_then(Value::as_str)
            .unwrap_or(code);
        return Err(format!(
            "Kimi token polling failed (HTTP {}): {}",
            status.as_u16(),
            detail
        ));
    }

    let access_token = payload
        .get("access_token")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "Kimi OAuth response missing access_token".to_string())?;
    let refresh_token = payload
        .get("refresh_token")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "Kimi OAuth response missing refresh_token".to_string())?;
    let expires_in = payload
        .get("expires_in")
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
        .ok_or_else(|| "Kimi OAuth response missing expires_in".to_string())?;
    let token = KimiOAuthToken {
        access_token: access_token.to_string(),
        refresh_token: refresh_token.to_string(),
        expires_at: Utc::now().timestamp().saturating_add(expires_in),
        scope: payload
            .get("scope")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        token_type: payload
            .get("token_type")
            .and_then(Value::as_str)
            .unwrap_or("Bearer")
            .to_string(),
        expires_in,
    };
    kimi_config::save_oauth_token(&token).map_err(|e| e.to_string())?;

    let provision_result: Result<(), String> = async {
        let models = client
            .get(format!("{KIMI_API_BASE_URL}/models"))
            .bearer_auth(&token.access_token)
            .header(reqwest::header::ACCEPT, "application/json")
            .send()
            .await
            .map_err(|e| format!("Failed to list Kimi models: {e}"))?;
        let models_status = models.status();
        let models_payload: Value = models
            .json()
            .await
            .map_err(|e| format!("Invalid Kimi models response: {e}"))?;
        if !models_status.is_success() {
            return Err(format!(
                "Failed to list Kimi models (HTTP {}): {}",
                models_status.as_u16(),
                models_payload
            ));
        }
        kimi_config::provision_managed_provider(&models_payload).map_err(|e| e.to_string())?;
        import_kimicode_providers_from_live(state).map_err(|e| e.to_string())?;
        Ok(())
    }
    .await;
    if let Err(error) = provision_result {
        let cleanup = kimi_config::logout_managed_provider().map_err(|e| e.to_string());
        return Err(match cleanup {
            Ok(_) => error,
            Err(cleanup_error) => format!("{error}; cleanup failed: {cleanup_error}"),
        });
    }
    Ok(Some(kimi_account(&token)))
}

fn kimi_logout(state: &AppState) -> Result<(), String> {
    kimi_config::logout_managed_provider().map_err(|e| e.to_string())?;
    state
        .db
        .delete_provider("kimicode", MANAGED_KIMI_PROVIDER)
        .map_err(|e| e.to_string())
}

fn map_account(
    provider: &str,
    account: GitHubAccount,
    default_account_id: Option<&str>,
) -> ManagedAuthAccount {
    ManagedAuthAccount {
        is_default: default_account_id == Some(account.id.as_str()),
        id: account.id,
        provider: provider.to_string(),
        login: account.login,
        avatar_url: account.avatar_url,
        authenticated_at: account.authenticated_at,
        github_domain: account.github_domain,
    }
}

fn map_device_code_response(
    provider: &str,
    response: GitHubDeviceCodeResponse,
) -> ManagedAuthDeviceCodeResponse {
    ManagedAuthDeviceCodeResponse {
        provider: provider.to_string(),
        device_code: response.device_code,
        user_code: response.user_code,
        verification_uri: response.verification_uri,
        expires_in: response.expires_in,
        interval: response.interval,
    }
}

#[tauri::command(rename_all = "camelCase")]
pub async fn auth_start_login(
    auth_provider: String,
    github_domain: Option<String>,
    copilot_state: State<'_, CopilotAuthState>,
    codex_state: State<'_, CodexOAuthState>,
) -> Result<ManagedAuthDeviceCodeResponse, String> {
    let auth_provider = ensure_auth_provider(&auth_provider)?;
    match auth_provider {
        AUTH_PROVIDER_GITHUB_COPILOT => {
            let auth_manager = copilot_state.0.read().await;
            let response = auth_manager
                .start_device_flow(github_domain.as_deref())
                .await
                .map_err(|e| e.to_string())?;
            Ok(map_device_code_response(auth_provider, response))
        }
        AUTH_PROVIDER_CODEX_OAUTH => {
            let auth_manager = codex_state.0.read().await;
            let response = auth_manager
                .start_device_flow()
                .await
                .map_err(|e| e.to_string())?;
            Ok(map_device_code_response(auth_provider, response))
        }
        AUTH_PROVIDER_KIMI_OAUTH => kimi_start_device_flow().await,
        _ => unreachable!(),
    }
}

#[tauri::command(rename_all = "camelCase")]
pub async fn auth_poll_for_account(
    auth_provider: String,
    device_code: String,
    github_domain: Option<String>,
    copilot_state: State<'_, CopilotAuthState>,
    codex_state: State<'_, CodexOAuthState>,
    state: State<'_, Arc<AppState>>,
) -> Result<Option<ManagedAuthAccount>, String> {
    let auth_provider = ensure_auth_provider(&auth_provider)?;
    match auth_provider {
        AUTH_PROVIDER_GITHUB_COPILOT => {
            let auth_manager = copilot_state.0.write().await;
            match auth_manager
                .poll_for_token(&device_code, github_domain.as_deref())
                .await
            {
                Ok(account) => {
                    let default_account_id = auth_manager.get_status().await.default_account_id;
                    Ok(account.map(|account| {
                        map_account(auth_provider, account, default_account_id.as_deref())
                    }))
                }
                Err(CopilotAuthError::AuthorizationPending) => Ok(None),
                Err(e) => Err(e.to_string()),
            }
        }
        AUTH_PROVIDER_CODEX_OAUTH => {
            let auth_manager = codex_state.0.write().await;
            match auth_manager.poll_for_token(&device_code).await {
                Ok(account) => {
                    let default_account_id = auth_manager.get_status().await.default_account_id;
                    Ok(account.map(|account| {
                        map_account(auth_provider, account, default_account_id.as_deref())
                    }))
                }
                Err(CodexOAuthError::AuthorizationPending) => Ok(None),
                Err(e) => Err(e.to_string()),
            }
        }
        AUTH_PROVIDER_KIMI_OAUTH => kimi_poll_device_flow(&device_code, state.inner()).await,
        _ => unreachable!(),
    }
}

#[tauri::command(rename_all = "camelCase")]
pub async fn auth_list_accounts(
    auth_provider: String,
    copilot_state: State<'_, CopilotAuthState>,
    codex_state: State<'_, CodexOAuthState>,
) -> Result<Vec<ManagedAuthAccount>, String> {
    let auth_provider = ensure_auth_provider(&auth_provider)?;
    match auth_provider {
        AUTH_PROVIDER_GITHUB_COPILOT => {
            let auth_manager = copilot_state.0.read().await;
            let status = auth_manager.get_status().await;
            let default_account_id = status.default_account_id.clone();
            Ok(status
                .accounts
                .into_iter()
                .map(|account| map_account(auth_provider, account, default_account_id.as_deref()))
                .collect())
        }
        AUTH_PROVIDER_CODEX_OAUTH => {
            let auth_manager = codex_state.0.read().await;
            let status = auth_manager.get_status().await;
            let default_account_id = status.default_account_id.clone();
            Ok(status
                .accounts
                .into_iter()
                .map(|account| map_account(auth_provider, account, default_account_id.as_deref()))
                .collect())
        }
        AUTH_PROVIDER_KIMI_OAUTH => Ok(kimi_status()?.accounts),
        _ => unreachable!(),
    }
}

#[tauri::command(rename_all = "camelCase")]
pub async fn auth_get_status(
    auth_provider: String,
    copilot_state: State<'_, CopilotAuthState>,
    codex_state: State<'_, CodexOAuthState>,
) -> Result<ManagedAuthStatus, String> {
    let auth_provider = ensure_auth_provider(&auth_provider)?;
    match auth_provider {
        AUTH_PROVIDER_GITHUB_COPILOT => {
            let auth_manager = copilot_state.0.read().await;
            let status = auth_manager.get_status().await;
            let default_account_id = status.default_account_id.clone();
            Ok(ManagedAuthStatus {
                provider: auth_provider.to_string(),
                authenticated: status.authenticated,
                default_account_id: default_account_id.clone(),
                migration_error: status.migration_error,
                accounts: status
                    .accounts
                    .into_iter()
                    .map(|account| {
                        map_account(auth_provider, account, default_account_id.as_deref())
                    })
                    .collect(),
            })
        }
        AUTH_PROVIDER_CODEX_OAUTH => {
            let auth_manager = codex_state.0.read().await;
            let status = auth_manager.get_status().await;
            let default_account_id = status.default_account_id.clone();
            Ok(ManagedAuthStatus {
                provider: auth_provider.to_string(),
                authenticated: status.authenticated,
                default_account_id: default_account_id.clone(),
                migration_error: None,
                accounts: status
                    .accounts
                    .into_iter()
                    .map(|account| {
                        map_account(auth_provider, account, default_account_id.as_deref())
                    })
                    .collect(),
            })
        }
        AUTH_PROVIDER_KIMI_OAUTH => kimi_status(),
        _ => unreachable!(),
    }
}

#[tauri::command(rename_all = "camelCase")]
pub async fn auth_remove_account(
    auth_provider: String,
    account_id: String,
    copilot_state: State<'_, CopilotAuthState>,
    codex_state: State<'_, CodexOAuthState>,
    state: State<'_, Arc<AppState>>,
) -> Result<(), String> {
    let auth_provider = ensure_auth_provider(&auth_provider)?;
    match auth_provider {
        AUTH_PROVIDER_GITHUB_COPILOT => {
            let auth_manager = copilot_state.0.write().await;
            auth_manager
                .remove_account(&account_id)
                .await
                .map_err(|e| e.to_string())
        }
        AUTH_PROVIDER_CODEX_OAUTH => {
            let auth_manager = codex_state.0.write().await;
            auth_manager
                .remove_account(&account_id)
                .await
                .map_err(|e| e.to_string())
        }
        AUTH_PROVIDER_KIMI_OAUTH => {
            if account_id != "kimi-code" {
                return Err(format!("Unknown Kimi account: {account_id}"));
            }
            kimi_logout(state.inner())
        }
        _ => unreachable!(),
    }
}

#[tauri::command(rename_all = "camelCase")]
pub async fn auth_set_default_account(
    auth_provider: String,
    account_id: String,
    copilot_state: State<'_, CopilotAuthState>,
    codex_state: State<'_, CodexOAuthState>,
) -> Result<(), String> {
    let auth_provider = ensure_auth_provider(&auth_provider)?;
    match auth_provider {
        AUTH_PROVIDER_GITHUB_COPILOT => {
            let auth_manager = copilot_state.0.write().await;
            auth_manager
                .set_default_account(&account_id)
                .await
                .map_err(|e| e.to_string())
        }
        AUTH_PROVIDER_CODEX_OAUTH => {
            let auth_manager = codex_state.0.write().await;
            auth_manager
                .set_default_account(&account_id)
                .await
                .map_err(|e| e.to_string())
        }
        AUTH_PROVIDER_KIMI_OAUTH => {
            if account_id == "kimi-code" {
                Ok(())
            } else {
                Err(format!("Unknown Kimi account: {account_id}"))
            }
        }
        _ => unreachable!(),
    }
}

#[tauri::command(rename_all = "camelCase")]
pub async fn auth_logout(
    auth_provider: String,
    copilot_state: State<'_, CopilotAuthState>,
    codex_state: State<'_, CodexOAuthState>,
    state: State<'_, Arc<AppState>>,
) -> Result<(), String> {
    let auth_provider = ensure_auth_provider(&auth_provider)?;
    match auth_provider {
        AUTH_PROVIDER_GITHUB_COPILOT => {
            let auth_manager = copilot_state.0.write().await;
            auth_manager.clear_auth().await.map_err(|e| e.to_string())
        }
        AUTH_PROVIDER_CODEX_OAUTH => {
            let auth_manager = codex_state.0.write().await;
            auth_manager.clear_auth().await.map_err(|e| e.to_string())
        }
        AUTH_PROVIDER_KIMI_OAUTH => kimi_logout(state.inner()),
        _ => unreachable!(),
    }
}
