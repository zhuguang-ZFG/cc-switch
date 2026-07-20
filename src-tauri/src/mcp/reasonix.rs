//! Reasonix MCP adapter — projects unified MCP specs into `[[plugins]]`.

use serde_json::{json, Map, Value};
use std::collections::HashMap;

use crate::app_config::{McpApps, McpServer, MultiAppConfig};
use crate::error::AppError;
use crate::reasonix_config;

use super::validation::validate_server_spec;

fn should_sync_reasonix_mcp() -> bool {
    reasonix_config::get_reasonix_dir().exists()
}

fn to_reasonix_plugin(spec: &Value) -> Result<Value, AppError> {
    validate_server_spec(spec)?;
    let obj = spec
        .as_object()
        .ok_or_else(|| AppError::McpValidation("MCP spec must be an object".into()))?;

    let mut output = Map::new();
    for (key, value) in obj {
        if key == "type" {
            continue;
        }
        output.insert(key.clone(), value.clone());
    }

    let typ = obj.get("type").and_then(Value::as_str).unwrap_or("stdio");
    if typ != "stdio" {
        output.insert("type".into(), Value::String(typ.into()));
    }

    Ok(Value::Object(output))
}

fn from_reasonix_plugin(id: &str, spec: &Value) -> Result<Value, AppError> {
    let mut output = spec.as_object().cloned().ok_or_else(|| {
        AppError::McpValidation(format!("Reasonix MCP plugin '{id}' must be an object"))
    })?;

    let kind = if output.contains_key("command") {
        "stdio"
    } else if output.contains_key("url") {
        match output.get("type").and_then(Value::as_str) {
            Some("http") => "http",
            _ => "sse",
        }
    } else {
        return Err(AppError::McpValidation(format!(
            "Reasonix MCP plugin '{id}' has neither command nor url"
        )));
    };

    output.insert("type".into(), Value::String(kind.into()));
    let output = Value::Object(output);
    validate_server_spec(&output)?;
    Ok(output)
}

pub fn sync_single_server_to_reasonix(
    _config: &MultiAppConfig,
    id: &str,
    server_spec: &Value,
) -> Result<(), AppError> {
    if !should_sync_reasonix_mcp() {
        return Ok(());
    }
    let mut plugin = to_reasonix_plugin(server_spec)?;
    if let Some(obj) = plugin.as_object_mut() {
        obj.insert("name".into(), json!(id));
    }
    reasonix_config::set_mcp_plugin(id, &plugin).map(|_| ())
}

pub fn remove_server_from_reasonix(id: &str) -> Result<(), AppError> {
    if !reasonix_config::get_reasonix_config_path().exists() {
        return Ok(());
    }
    reasonix_config::remove_mcp_plugin(id).map(|_| ())
}

pub fn import_from_reasonix(config: &mut MultiAppConfig) -> Result<usize, AppError> {
    if !should_sync_reasonix_mcp() {
        return Ok(0);
    }

    let plugins = reasonix_config::get_mcp_plugins()?;
    if plugins.is_empty() {
        return Ok(0);
    }

    let servers = config.mcp.servers.get_or_insert_with(HashMap::new);
    let mut changed = 0;

    for (id, spec) in plugins {
        let unified = match from_reasonix_plugin(&id, &spec) {
            Ok(value) => value,
            Err(error) => {
                log::warn!("Skipping invalid Reasonix MCP plugin '{id}': {error}");
                continue;
            }
        };

        if let Some(existing) = servers.get_mut(&id) {
            if !existing.apps.reasonix {
                existing.apps.reasonix = true;
                changed += 1;
            }
        } else {
            servers.insert(
                id.clone(),
                McpServer {
                    id: id.clone(),
                    name: id.clone(),
                    server: unified,
                    apps: McpApps {
                        reasonix: true,
                        ..Default::default()
                    },
                    description: None,
                    homepage: None,
                    docs: None,
                    tags: Vec::new(),
                },
            );
            changed += 1;
        }
    }

    Ok(changed)
}
