//! Kimi Code MCP adapter for `~/.kimi-code/mcp.json`.

use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use crate::app_config::{McpApps, McpServer, MultiAppConfig};
use crate::config::atomic_write;
use crate::error::AppError;
use crate::kimi_config;

use super::validation::validate_server_spec;

fn config_path() -> PathBuf {
    kimi_config::get_kimi_dir().join("mcp.json")
}

fn read_root() -> Result<Value, AppError> {
    let path = config_path();
    if !path.exists() {
        return Ok(json!({ "mcpServers": {} }));
    }
    let bytes = fs::read(&path).map_err(|e| AppError::io(&path, e))?;
    serde_json::from_slice(&bytes).map_err(|e| {
        AppError::localized(
            "mcp.kimicode.invalid_json",
            format!("Kimi Code mcp.json 格式错误: {e}"),
            format!("Invalid Kimi Code mcp.json: {e}"),
        )
    })
}

fn write_root(root: &Value) -> Result<(), AppError> {
    let path = config_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::io(parent, e))?;
    }
    let mut bytes = serde_json::to_vec_pretty(root)
        .map_err(|e| AppError::Message(format!("Failed to serialize Kimi MCP config: {e}")))?;
    bytes.push(b'\n');
    atomic_write(&path, &bytes)
}

fn server_map_mut(root: &mut Value) -> Result<&mut Map<String, Value>, AppError> {
    let object = root
        .as_object_mut()
        .ok_or_else(|| AppError::McpValidation("Kimi mcp.json root must be an object".into()))?;
    if !object.get("mcpServers").is_some_and(Value::is_object) {
        object.insert("mcpServers".into(), Value::Object(Map::new()));
    }
    Ok(object
        .get_mut("mcpServers")
        .and_then(Value::as_object_mut)
        .expect("mcpServers was just initialized"))
}

fn to_kimi(spec: &Value) -> Result<Value, AppError> {
    validate_server_spec(spec)?;
    let mut output = spec
        .as_object()
        .cloned()
        .ok_or_else(|| AppError::McpValidation("MCP spec must be an object".into()))?;
    let kind = output
        .remove("type")
        .and_then(|value| value.as_str().map(str::to_string))
        .unwrap_or_else(|| "stdio".to_string());
    if output.contains_key("url") {
        if kind == "sse" {
            output.insert("transport".into(), Value::String("sse".into()));
        } else if output.get("transport").and_then(Value::as_str) != Some("sse") {
            output.remove("transport");
        }
    }
    Ok(Value::Object(output))
}

fn from_kimi(id: &str, spec: &Value) -> Result<Value, AppError> {
    let mut output = spec.as_object().cloned().ok_or_else(|| {
        AppError::McpValidation(format!("Kimi MCP server '{id}' must be an object"))
    })?;
    let kind = if output.contains_key("command") {
        "stdio"
    } else if output.contains_key("url") {
        if output.get("transport").and_then(Value::as_str) == Some("sse") {
            "sse"
        } else {
            "http"
        }
    } else {
        return Err(AppError::McpValidation(format!(
            "Kimi MCP server '{id}' has neither command nor url"
        )));
    };
    output.remove("transport");
    output.insert("type".into(), Value::String(kind.into()));
    let output = Value::Object(output);
    validate_server_spec(&output)?;
    Ok(output)
}

pub fn sync_single_server_to_kimi(
    _config: &MultiAppConfig,
    id: &str,
    server_spec: &Value,
) -> Result<(), AppError> {
    let mut root = read_root()?;
    server_map_mut(&mut root)?.insert(id.to_string(), to_kimi(server_spec)?);
    write_root(&root)
}

pub fn remove_server_from_kimi(id: &str) -> Result<(), AppError> {
    let path = config_path();
    if !path.exists() {
        return Ok(());
    }
    let mut root = read_root()?;
    server_map_mut(&mut root)?.remove(id);
    write_root(&root)
}

pub fn import_from_kimi(config: &mut MultiAppConfig) -> Result<usize, AppError> {
    let root = read_root()?;
    let Some(kimi_servers) = root.get("mcpServers").and_then(Value::as_object) else {
        return Ok(0);
    };
    let servers = config.mcp.servers.get_or_insert_with(HashMap::new);
    let mut changed = 0;
    for (id, spec) in kimi_servers {
        let unified = match from_kimi(id, spec) {
            Ok(value) => value,
            Err(error) => {
                log::warn!("Skipping invalid Kimi MCP server '{id}': {error}");
                continue;
            }
        };
        if let Some(existing) = servers.get_mut(id) {
            if !existing.apps.hermes {
                existing.apps.hermes = true;
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
                        hermes: true,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_remote_transport_without_losing_fields() {
        let source = json!({
            "type": "sse",
            "url": "https://example.com/mcp",
            "headers": { "Authorization": "Bearer test" }
        });
        let kimi = to_kimi(&source).unwrap();
        assert_eq!(kimi["transport"], "sse");
        assert_eq!(from_kimi("remote", &kimi).unwrap(), source);
    }

    #[test]
    fn maps_http_without_explicit_transport() {
        let source = json!({ "type": "http", "url": "https://example.com/mcp" });
        let kimi = to_kimi(&source).unwrap();
        assert!(kimi.get("transport").is_none());
        assert_eq!(from_kimi("remote", &kimi).unwrap(), source);
    }
}
