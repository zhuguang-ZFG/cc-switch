use serde_json::Value;
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

use crate::config::atomic_write;
use crate::kimi_config;
use crate::session_manager::{SessionMessage, SessionMeta};

use super::utils::{extract_text, parse_timestamp_to_ms, path_basename, truncate_summary};

const PROVIDER_ID: &str = "kimicode";

pub fn session_root() -> PathBuf {
    kimi_config::get_kimi_dir().join("sessions")
}

fn session_index_path() -> PathBuf {
    kimi_config::get_kimi_dir().join("session_index.jsonl")
}

fn resolve_indexed_session_dir(
    root: &Path,
    session_dir: &Path,
    session_id: &str,
) -> Option<PathBuf> {
    let canonical_root = root.canonicalize().ok()?;
    let canonical_session_dir = session_dir.canonicalize().ok()?;
    (canonical_session_dir.is_dir()
        && canonical_session_dir.starts_with(&canonical_root)
        && canonical_session_dir
            .file_name()
            .and_then(|value| value.to_str())
            == Some(session_id))
    .then_some(canonical_session_dir)
}

pub fn scan_sessions() -> Vec<SessionMeta> {
    let path = session_index_path();
    let root = session_root();
    let Ok(file) = File::open(&path) else {
        return Vec::new();
    };
    let mut indexed = HashMap::<String, (PathBuf, Option<String>)>::new();
    for line in BufReader::new(file).lines().map_while(Result::ok) {
        let Ok(value) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let Some(session_id) = value.get("sessionId").and_then(Value::as_str) else {
            continue;
        };
        let Some(session_dir) = value.get("sessionDir").and_then(Value::as_str) else {
            continue;
        };
        let indexed_session_dir = PathBuf::from(session_dir);
        let Some(session_dir) =
            resolve_indexed_session_dir(&root, &indexed_session_dir, session_id)
        else {
            log::warn!(
                "Skipping invalid Kimi session path: {}",
                indexed_session_dir.display()
            );
            continue;
        };
        indexed.insert(
            session_id.to_string(),
            (
                session_dir,
                value
                    .get("workDir")
                    .and_then(Value::as_str)
                    .map(str::to_string),
            ),
        );
    }

    indexed
        .into_iter()
        .filter_map(|(session_id, (session_dir, indexed_work_dir))| {
            let state_path = session_dir.join("state.json");
            let state: Value = serde_json::from_slice(&fs::read(&state_path).ok()?).ok()?;
            let work_dir = state
                .get("workDir")
                .and_then(Value::as_str)
                .map(str::to_string)
                .or(indexed_work_dir);
            let title = state
                .get("title")
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .or_else(|| state.get("lastPrompt").and_then(Value::as_str))
                .map(|value| truncate_summary(value, 80))
                .filter(|value| !value.is_empty())
                .or_else(|| work_dir.as_deref().and_then(path_basename));
            Some(SessionMeta {
                provider_id: PROVIDER_ID.to_string(),
                session_id: session_id.clone(),
                title: title.clone(),
                summary: state
                    .get("lastPrompt")
                    .and_then(Value::as_str)
                    .map(|value| truncate_summary(value, 160))
                    .filter(|value| !value.is_empty())
                    .or(title),
                project_dir: work_dir,
                created_at: state.get("createdAt").and_then(parse_timestamp_to_ms),
                last_active_at: state.get("updatedAt").and_then(parse_timestamp_to_ms),
                source_path: Some(session_dir.to_string_lossy().into_owned()),
                resume_command: Some(format!("kimi --session {session_id}")),
            })
        })
        .collect()
}

fn should_include_context_message(message: &Value) -> bool {
    message
        .get("origin")
        .and_then(|origin| origin.get("kind"))
        .and_then(Value::as_str)
        != Some("injection")
}

pub fn load_messages(session_dir: &Path) -> Result<Vec<SessionMessage>, String> {
    let session_id = session_dir
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| format!("Invalid Kimi session directory: {}", session_dir.display()))?;
    let session_dir = resolve_indexed_session_dir(&session_root(), session_dir, session_id)
        .ok_or_else(|| format!("Invalid Kimi session directory: {}", session_dir.display()))?;
    load_messages_from_dir(&session_dir)
}

fn load_messages_from_dir(session_dir: &Path) -> Result<Vec<SessionMessage>, String> {
    let path = session_dir.join("agents").join("main").join("wire.jsonl");
    let file = File::open(&path)
        .map_err(|e| format!("Failed to open Kimi session {}: {e}", path.display()))?;
    let mut messages = Vec::new();
    let mut assistant_text = String::new();
    let mut assistant_ts = None;

    let flush_assistant =
        |messages: &mut Vec<SessionMessage>, text: &mut String, ts: &mut Option<i64>| {
            if !text.trim().is_empty() {
                messages.push(SessionMessage {
                    role: "assistant".to_string(),
                    content: std::mem::take(text),
                    ts: ts.take(),
                });
            }
        };

    for line in BufReader::new(file).lines().map_while(Result::ok) {
        let Ok(record) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let ts = record.get("time").and_then(parse_timestamp_to_ms);
        match record.get("type").and_then(Value::as_str) {
            Some("context.append_message") => {
                flush_assistant(&mut messages, &mut assistant_text, &mut assistant_ts);
                let Some(message) = record.get("message") else {
                    continue;
                };
                if !should_include_context_message(message) {
                    continue;
                }
                let content = extract_text(message.get("content").unwrap_or(&Value::Null));
                if content.trim().is_empty() {
                    continue;
                }
                messages.push(SessionMessage {
                    role: message
                        .get("role")
                        .and_then(Value::as_str)
                        .unwrap_or("unknown")
                        .to_string(),
                    content,
                    ts,
                });
            }
            Some("context.append_loop_event") => {
                let Some(event) = record.get("event") else {
                    continue;
                };
                match event.get("type").and_then(Value::as_str) {
                    Some("content.part") => {
                        let text = extract_text(event.get("part").unwrap_or(&Value::Null));
                        if !text.is_empty() {
                            if assistant_ts.is_none() {
                                assistant_ts = ts;
                            }
                            assistant_text.push_str(&text);
                        }
                    }
                    Some("step.end") => {
                        flush_assistant(&mut messages, &mut assistant_text, &mut assistant_ts);
                    }
                    _ => {}
                }
            }
            _ => {}
        }
    }
    flush_assistant(&mut messages, &mut assistant_text, &mut assistant_ts);
    Ok(messages)
}

pub fn delete_session(root: &Path, source_path: &Path, session_id: &str) -> Result<bool, String> {
    let resolved = resolve_indexed_session_dir(root, source_path, session_id)
        .ok_or_else(|| format!("Invalid Kimi session directory: {}", source_path.display()))?;
    fs::remove_dir_all(&resolved)
        .map_err(|e| format!("Failed to delete Kimi session directory: {e}"))?;

    let index_path = session_index_path();
    if index_path.exists() {
        let content = fs::read_to_string(&index_path)
            .map_err(|e| format!("Failed to read Kimi session index: {e}"))?;
        let retained = content
            .lines()
            .filter(|line| {
                serde_json::from_str::<Value>(line)
                    .ok()
                    .and_then(|value| {
                        value
                            .get("sessionId")
                            .and_then(Value::as_str)
                            .map(str::to_string)
                    })
                    .as_deref()
                    != Some(session_id)
            })
            .collect::<Vec<_>>()
            .join("\n");
        let output = if retained.is_empty() {
            retained
        } else {
            format!("{retained}\n")
        };
        atomic_write(&index_path, output.as_bytes()).map_err(|e| e.to_string())?;
    }
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn loads_user_and_streamed_assistant_messages() {
        let temp = tempdir().unwrap();
        let wire_dir = temp.path().join("agents").join("main");
        fs::create_dir_all(&wire_dir).unwrap();
        fs::write(
            wire_dir.join("wire.jsonl"),
            concat!(
                "{\"type\":\"context.append_message\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"hello\"}],\"origin\":{\"kind\":\"user\"}},\"time\":1000}\n",
                "{\"type\":\"context.append_loop_event\",\"event\":{\"type\":\"content.part\",\"part\":{\"type\":\"text\",\"text\":\"world\"}},\"time\":2000}\n",
                "{\"type\":\"context.append_loop_event\",\"event\":{\"type\":\"step.end\"},\"time\":3000}\n"
            ),
        )
        .unwrap();
        let messages = load_messages_from_dir(temp.path()).unwrap();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0].content, "hello");
        assert_eq!(messages[1].content, "world");
    }

    #[test]
    fn resolves_only_real_session_paths_inside_root_with_matching_id() {
        let temp = tempdir().unwrap();
        let root = temp.path().join("sessions");
        let valid = root.join("work").join("session-1");
        let wrong_id = root.join("work").join("session-2");
        let outside = temp.path().join("outside").join("session-1");
        fs::create_dir_all(&valid).unwrap();
        fs::create_dir_all(&wrong_id).unwrap();
        fs::create_dir_all(&outside).unwrap();

        assert_eq!(
            resolve_indexed_session_dir(&root, &valid, "session-1"),
            valid.canonicalize().ok()
        );
        assert!(resolve_indexed_session_dir(&root, &outside, "session-1").is_none());
        assert!(resolve_indexed_session_dir(&root, &wrong_id, "session-1").is_none());

        let traversal = root
            .join("work")
            .join("..")
            .join("..")
            .join("outside")
            .join("session-1");
        assert!(resolve_indexed_session_dir(&root, &traversal, "session-1").is_none());
    }
}
