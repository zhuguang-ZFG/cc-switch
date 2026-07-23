//! Pi session scanner.
//!
//! Layout (Pi agent, `~/.pi/agent`):
//!   `<pi dir>/sessions/<workspace-slug>/*.jsonl`
//!
//! The first line is a `{"type":"session","id","timestamp","cwd"}` header;
//! each `{"type":"message","message":{"role","content",...}}` line is a
//! message whose content is `[{"type":"text","text":...}]` blocks.
//! Workspace slugs are encoded directory names (e.g. `--D--Users-foo--`);
//! the real project directory comes from the header `cwd`.

use std::fs::{self, File};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use serde_json::Value;

use crate::session_manager::{SessionMessage, SessionMeta};

use super::utils::{extract_text, parse_timestamp_to_ms, truncate_summary, TITLE_MAX_CHARS};

const PROVIDER_ID: &str = "pi";

/// Sidecar / non-transcript suffixes that must not appear as sessions.
fn is_transcript_jsonl(name: &str) -> bool {
    if !name.ends_with(".jsonl") {
        return false;
    }
    let lower = name.to_ascii_lowercase();
    !lower.ends_with(".jsonl.bak") && !lower.ends_with(".lock")
}

pub fn session_root() -> PathBuf {
    crate::pi_config::get_pi_dir().join("sessions")
}

pub fn scan_sessions() -> Vec<SessionMeta> {
    let root = session_root();
    let mut out = Vec::new();
    if root.is_dir() {
        collect_jsonl_sessions(&root, &root, &mut out);
    }
    out
}

fn collect_jsonl_sessions(root: &Path, dir: &Path, out: &mut Vec<SessionMeta>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            // One level of workspace-slug subdirs under the sessions root.
            if path.parent() == Some(root) {
                collect_jsonl_sessions(root, &path, out);
            }
            continue;
        }
        let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if !is_transcript_jsonl(name) {
            continue;
        }
        if let Some(meta) = parse_session_file(&path) {
            out.push(meta);
        }
    }
}

fn system_time_to_ms(time: SystemTime) -> Option<i64> {
    time.duration_since(SystemTime::UNIX_EPOCH)
        .ok()
        .and_then(|d| i64::try_from(d.as_millis()).ok())
}

/// Pi session header fields (`{"type":"session","id","timestamp","cwd"}`).
struct SessionHeader {
    id: Option<String>,
    cwd: Option<String>,
    created_at: Option<i64>,
}

fn read_header(path: &Path) -> SessionHeader {
    let mut header = SessionHeader {
        id: None,
        cwd: None,
        created_at: None,
    };
    let Ok(file) = File::open(path) else {
        return header;
    };
    for line in BufReader::new(file).lines().map_while(Result::ok).take(16) {
        let Ok(value) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        if value.get("type").and_then(Value::as_str) != Some("session") {
            continue;
        }
        header.id = value
            .get("id")
            .and_then(Value::as_str)
            .map(str::to_string)
            .filter(|s| !s.is_empty());
        header.cwd = value
            .get("cwd")
            .and_then(Value::as_str)
            .map(str::to_string)
            .filter(|s| !s.is_empty());
        header.created_at = value.get("timestamp").and_then(parse_timestamp_to_ms);
        break;
    }
    header
}

fn first_user_preview(path: &Path) -> Option<String> {
    let file = File::open(path).ok()?;
    for line in BufReader::new(file).lines().map_while(Result::ok).take(64) {
        let Ok(value) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        if value.get("type").and_then(Value::as_str) != Some("message") {
            continue;
        }
        let Some(message) = value.get("message") else {
            continue;
        };
        let role = message.get("role").and_then(Value::as_str).unwrap_or("");
        if role != "user" {
            continue;
        }
        let content = message
            .get("content")
            .map(extract_text)
            .unwrap_or_default();
        let trimmed = content.trim();
        if trimmed.is_empty() {
            continue;
        }
        return Some(truncate_summary(trimmed, TITLE_MAX_CHARS));
    }
    None
}

fn parse_session_file(path: &Path) -> Option<SessionMeta> {
    let file_name = path.file_name()?.to_str()?;
    let stem = file_name
        .strip_suffix(".jsonl")
        .unwrap_or(file_name)
        .to_string();

    let header = read_header(path);
    let base_id = header.id.clone().unwrap_or(stem);
    if base_id.is_empty() {
        return None;
    }

    let meta = fs::metadata(path).ok()?;
    let modified = meta.modified().ok().and_then(system_time_to_ms);
    let created = meta.created().ok().and_then(system_time_to_ms);

    let preview = first_user_preview(path);
    let project_dir = header.cwd.clone();

    // Qualify id with the project basename so two workspaces with the same
    // session id don't collide in the UI (mirrors the reasonix scanner).
    let session_id = if let Some(ref project) = project_dir {
        if let Some(slug) = Path::new(project)
            .file_name()
            .and_then(|n| n.to_str())
            .filter(|s| !s.is_empty())
        {
            format!("{slug}/{base_id}")
        } else {
            base_id
        }
    } else {
        base_id
    };

    Some(SessionMeta {
        provider_id: PROVIDER_ID.to_string(),
        session_id,
        title: preview.clone(),
        summary: preview,
        project_dir,
        created_at: header.created_at.or(created).or(modified),
        last_active_at: modified,
        source_path: Some(path.display().to_string()),
        // Prefer full path: CLI --resume accepts path or id; id alone is ambiguous.
        resume_command: Some(format!("pi --resume \"{}\"", path.display())),
    })
}

pub fn load_messages(path: &Path) -> Result<Vec<SessionMessage>, String> {
    let file = File::open(path)
        .map_err(|e| format!("Failed to open Pi session {}: {e}", path.display()))?;
    let mut messages = Vec::new();
    for line in BufReader::new(file).lines().map_while(Result::ok) {
        let Ok(value) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        if value.get("type").and_then(Value::as_str) != Some("message") {
            continue;
        }
        let Some(message) = value.get("message") else {
            continue;
        };
        let role = message
            .get("role")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if role.is_empty() {
            continue;
        }
        let content = message
            .get("content")
            .map(extract_text)
            .unwrap_or_default();
        // Keep assistant rows even when content is empty if toolCalls exist.
        let has_tool_calls = message
            .get("toolCalls")
            .and_then(Value::as_array)
            .is_some_and(|a| !a.is_empty());
        if content.trim().is_empty() && !has_tool_calls {
            continue;
        }
        let display = if content.trim().is_empty() && has_tool_calls {
            "[toolCalls]".to_string()
        } else {
            content
        };
        messages.push(SessionMessage {
            role,
            content: display,
            ts: message.get("timestamp").and_then(parse_timestamp_to_ms),
        });
    }
    Ok(messages)
}

pub fn delete_session(root: &Path, path: &Path, session_id: &str) -> Result<bool, String> {
    if !path.starts_with(root) {
        return Err(format!(
            "Pi session source is outside the session root: {}",
            path.display()
        ));
    }
    let file_name = path
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| format!("Invalid Pi session path: {}", path.display()))?;
    if !is_transcript_jsonl(file_name) {
        return Err(format!(
            "Unexpected Pi session source: {}",
            path.display()
        ));
    }
    let stem = file_name.strip_suffix(".jsonl").unwrap_or(file_name);
    // Session id is the header uuid (possibly qualified `project-slug/id`);
    // fall back to the filename stem when the header is missing.
    let header_id = read_header(path).id;
    let base_id = header_id.as_deref().unwrap_or(stem);
    let id_matches = session_id == base_id
        || session_id
            .rsplit_once('/')
            .is_some_and(|(_, tail)| tail == base_id);
    if !id_matches {
        return Err(format!(
            "Pi session ID mismatch: expected {session_id}, found {base_id}"
        ));
    }

    if path.is_file() {
        fs::remove_file(path)
            .map_err(|e| format!("Failed to delete Pi session {}: {e}", path.display()))?;
        return Ok(true);
    }
    Ok(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    const HEADER: &str = r#"{"type":"session","version":3,"id":"019f84b7-11de-785e-9d53-3ef83bd59d23","timestamp":"2026-07-21T12:46:58.014Z","cwd":"D:\\Users\\demo"}"#;
    const USER_MSG: &str = r#"{"type":"message","id":"36e87069","timestamp":"2026-07-21T12:46:58.116Z","message":{"role":"user","content":[{"type":"text","text":"say hi in one word"}],"timestamp":1784638018115}}"#;
    const ASSISTANT_MSG: &str = r#"{"type":"message","id":"e4212b07","timestamp":"2026-07-21T12:46:58.117Z","message":{"role":"assistant","content":[{"type":"text","text":"hi"}],"timestamp":1784638018117}}"#;

    fn write_session(path: &Path) {
        let mut f = File::create(path).unwrap();
        writeln!(f, "{HEADER}").unwrap();
        writeln!(f, "{USER_MSG}").unwrap();
        writeln!(f, "{ASSISTANT_MSG}").unwrap();
    }

    #[test]
    fn is_transcript_filters_sidecars() {
        assert!(is_transcript_jsonl("chat.jsonl"));
        assert!(!is_transcript_jsonl("chat.jsonl.bak"));
        assert!(!is_transcript_jsonl("chat.lock"));
        assert!(!is_transcript_jsonl("readme.md"));
    }

    #[test]
    fn scan_and_load_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let sessions = dir.path().join("sessions");
        let slug = sessions.join("--D--Users-demo--");
        fs::create_dir_all(&slug).unwrap();
        let path = slug.join("2026-07-21T12-46-58-014Z_019f84b7-11de-785e-9d53-3ef83bd59d23.jsonl");
        write_session(&path);

        let previous = std::env::var_os("PI_AGENT_HOME");
        std::env::set_var("PI_AGENT_HOME", dir.path());
        let scanned = scan_sessions();
        match previous {
            Some(v) => std::env::set_var("PI_AGENT_HOME", v),
            None => std::env::remove_var("PI_AGENT_HOME"),
        }

        assert_eq!(scanned.len(), 1);
        let meta = &scanned[0];
        assert_eq!(meta.provider_id, "pi");
        assert_eq!(
            meta.session_id,
            "demo/019f84b7-11de-785e-9d53-3ef83bd59d23"
        );
        assert_eq!(meta.project_dir.as_deref(), Some("D:\\Users\\demo"));
        assert!(meta.title.as_deref().unwrap_or("").contains("say hi"));

        let messages = load_messages(&path).unwrap();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0].role, "user");
        assert_eq!(messages[0].content, "say hi in one word");
        assert_eq!(messages[0].ts, Some(1784638018115));
        assert_eq!(messages[1].role, "assistant");
    }

    #[test]
    fn delete_session_removes_transcript() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("sessions");
        fs::create_dir_all(&root).unwrap();
        let path = root.join("chat.jsonl");
        write_session(&path);

        let deleted = delete_session(
            &root,
            &path,
            "demo/019f84b7-11de-785e-9d53-3ef83bd59d23",
        )
        .expect("delete session");
        assert!(deleted);
        assert!(!path.exists());
    }

    #[test]
    fn delete_session_rejects_id_mismatch() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("sessions");
        fs::create_dir_all(&root).unwrap();
        let path = root.join("chat.jsonl");
        write_session(&path);

        let err = delete_session(&root, &path, "someone-else")
            .expect_err("expected id mismatch to be rejected");
        assert!(err.contains("ID mismatch"));
        assert!(path.exists());
    }
}
