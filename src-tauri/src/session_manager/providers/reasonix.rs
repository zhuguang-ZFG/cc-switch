//! Reasonix session scanner.
//!
//! Layout (from DeepSeek-Reasonix `config.SessionDir` / `ProjectSessionDir`):
//!   `<reasonix home>/sessions/*.jsonl`
//!   `<reasonix home>/sessions/<workspace-slug>/*.jsonl`
//!   `<reasonix home>/projects/<slug>/sessions/*.jsonl`  (desktop multi-workspace)
//!
//! Each line is a `{"role","content",...}` message. Sidecars
//! (`.events.jsonl`, `.meta.json`, `.bak`, …) are ignored for listing.

use std::fs::{self, File};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use serde_json::Value;

use crate::session_manager::{SessionMessage, SessionMeta};

use super::utils::{extract_text, truncate_summary, TITLE_MAX_CHARS};

const PROVIDER_ID: &str = "reasonix";

/// Sidecar / non-transcript suffixes that must not appear as sessions.
fn is_transcript_jsonl(name: &str) -> bool {
    if !name.ends_with(".jsonl") {
        return false;
    }
    let lower = name.to_ascii_lowercase();
    // Skip event logs, guardian logs, backups, and lock-adjacent names.
    !lower.ends_with(".events.jsonl")
        && !lower.ends_with(".guardian.jsonl")
        && !lower.ends_with(".jsonl.bak")
        && !lower.contains(".lease.")
        && !lower.ends_with(".lock")
}

pub fn session_root() -> PathBuf {
    crate::reasonix_config::get_reasonix_dir().join("sessions")
}

/// Global sessions + per-project `projects/<slug>/sessions` roots.
pub fn session_roots() -> Vec<PathBuf> {
    let mut roots = vec![session_root()];
    let projects = crate::reasonix_config::get_reasonix_dir().join("projects");
    if let Ok(entries) = fs::read_dir(&projects) {
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let sessions = path.join("sessions");
            if sessions.is_dir() {
                roots.push(sessions);
            }
        }
    }
    roots
}

pub fn scan_sessions() -> Vec<SessionMeta> {
    let mut out = Vec::new();
    for root in session_roots() {
        if !root.is_dir() {
            continue;
        }
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
            // One level of workspace-slug subdirs under the global sessions root
            // (e.g. C_Users_zhugu). Project roots are already .../sessions.
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
        if let Some(meta) = parse_session_file(root, &path) {
            out.push(meta);
        }
    }
}

fn system_time_to_ms(time: SystemTime) -> Option<i64> {
    time.duration_since(SystemTime::UNIX_EPOCH)
        .ok()
        .and_then(|d| i64::try_from(d.as_millis()).ok())
}

fn first_user_preview(path: &Path) -> Option<String> {
    let file = File::open(path).ok()?;
    for line in BufReader::new(file).lines().map_while(Result::ok).take(64) {
        let Ok(value) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let role = value.get("role").and_then(Value::as_str).unwrap_or("");
        if role != "user" {
            continue;
        }
        let content = value
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

fn parse_session_file(root: &Path, path: &Path) -> Option<SessionMeta> {
    let file_name = path.file_name()?.to_str()?;
    let session_id = file_name
        .strip_suffix(".jsonl")
        .unwrap_or(file_name)
        .to_string();
    if session_id.is_empty() {
        return None;
    }

    let meta = fs::metadata(path).ok()?;
    let modified = meta.modified().ok().and_then(system_time_to_ms);
    let created = meta.created().ok().and_then(system_time_to_ms);

    // Prefer CustomTitle from sibling .meta.json when present.
    let custom_title = read_custom_title(path);
    let preview = first_user_preview(path);
    let title = custom_title.or_else(|| preview.clone());

    let project_dir = path
        .parent()
        .filter(|parent| *parent != root)
        .map(|parent| parent.display().to_string());

    Some(SessionMeta {
        provider_id: PROVIDER_ID.to_string(),
        session_id: session_id.clone(),
        title,
        summary: preview,
        project_dir,
        created_at: created.or(modified),
        last_active_at: modified,
        source_path: Some(path.display().to_string()),
        // Prefer full path: CLI --resume accepts path or query; id alone is ambiguous.
        resume_command: Some(format!("reasonix --resume \"{}\"", path.display())),
    })
}

fn read_custom_title(session_path: &Path) -> Option<String> {
    // Prefer .titles.json in the same directory (desktop renames).
    if let Some(title) = read_title_from_titles_json(session_path) {
        return Some(title);
    }

    // Sidecars next to the transcript:
    //   <name>.meta.json  (legacy naming)
    //   <name>.jsonl.meta (current DeepSeek-Reasonix branch meta)
    let meta_path = session_path.with_extension("meta.json");
    let jsonl_meta = {
        let mut p = session_path.to_path_buf();
        let name = session_path.file_name()?.to_str()?.to_string();
        p.set_file_name(format!("{name}.meta"));
        p
    };
    for candidate in [meta_path, jsonl_meta] {
        if !candidate.is_file() {
            continue;
        }
        let Ok(raw) = fs::read_to_string(&candidate) else {
            continue;
        };
        let Ok(value) = serde_json::from_str::<Value>(&raw) else {
            continue;
        };
        // Custom title keys first, then official `preview` from branch meta.
        for key in [
            "customTitle",
            "CustomTitle",
            "title",
            "Title",
            "preview",
            "Preview",
        ] {
            if let Some(title) = value
                .get(key)
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
            {
                return Some(truncate_summary(title, TITLE_MAX_CHARS));
            }
        }
    }
    None
}

/// `.titles.json` maps basename → display title (desktop rename UI).
fn read_title_from_titles_json(session_path: &Path) -> Option<String> {
    let parent = session_path.parent()?;
    let titles_path = parent.join(".titles.json");
    if !titles_path.is_file() {
        return None;
    }
    let raw = fs::read_to_string(&titles_path).ok()?;
    let value: Value = serde_json::from_str(&raw).ok()?;
    let file_name = session_path.file_name()?.to_str()?;
    let title = value
        .get(file_name)
        .and_then(|v| {
            v.as_str()
                .map(str::to_string)
                .or_else(|| v.get("title").and_then(Value::as_str).map(str::to_string))
        })?
        .trim()
        .to_string();
    if title.is_empty() {
        None
    } else {
        Some(truncate_summary(&title, TITLE_MAX_CHARS))
    }
}

pub fn load_messages(path: &Path) -> Result<Vec<SessionMessage>, String> {
    let file = File::open(path)
        .map_err(|e| format!("Failed to open Reasonix session {}: {e}", path.display()))?;
    let mut messages = Vec::new();
    for line in BufReader::new(file).lines().map_while(Result::ok) {
        let Ok(value) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let role = value
            .get("role")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if role.is_empty() {
            continue;
        }
        let content = value
            .get("content")
            .map(extract_text)
            .unwrap_or_default();
        // Keep tool/assistant rows even when content is empty if tool_calls exist.
        let has_tool_calls = value
            .get("tool_calls")
            .and_then(Value::as_array)
            .is_some_and(|a| !a.is_empty());
        if content.trim().is_empty() && !has_tool_calls {
            continue;
        }
        let display = if content.trim().is_empty() && has_tool_calls {
            "[tool_calls]".to_string()
        } else {
            content
        };
        messages.push(SessionMessage {
            role,
            content: display,
            ts: None,
        });
    }
    Ok(messages)
}

pub fn delete_session(root: &Path, path: &Path, session_id: &str) -> Result<bool, String> {
    if !path.starts_with(root) {
        return Err(format!(
            "Reasonix session source is outside the session root: {}",
            path.display()
        ));
    }
    let file_name = path
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| format!("Invalid Reasonix session path: {}", path.display()))?;
    if !is_transcript_jsonl(file_name) {
        return Err(format!(
            "Unexpected Reasonix session source: {}",
            path.display()
        ));
    }
    let stem = file_name.strip_suffix(".jsonl").unwrap_or(file_name);
    if stem != session_id {
        return Err(format!(
            "Reasonix session ID mismatch: expected {session_id}, found {stem}"
        ));
    }

    // Remove the transcript and common sidecars sharing the same stem.
    let parent = path
        .parent()
        .ok_or_else(|| format!("Invalid Reasonix session path: {}", path.display()))?;
    let mut removed = false;
    if path.is_file() {
        fs::remove_file(path)
            .map_err(|e| format!("Failed to delete Reasonix session {}: {e}", path.display()))?;
        removed = true;
    }
    let sidecar_suffixes = [
        ".events.jsonl",
        ".meta.json",
        ".jsonl.bak",
        ".pending.json",
        ".plan.json",
        ".guardian.jsonl",
    ];
    for suffix in sidecar_suffixes {
        let sidecar = parent.join(format!("{stem}{suffix}"));
        if sidecar.is_file() {
            let _ = fs::remove_file(&sidecar);
        }
    }
    Ok(removed)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn is_transcript_filters_sidecars() {
        assert!(is_transcript_jsonl("chat.jsonl"));
        assert!(!is_transcript_jsonl("chat.events.jsonl"));
        assert!(!is_transcript_jsonl("chat.jsonl.bak"));
        assert!(!is_transcript_jsonl("chat.guardian.jsonl"));
        assert!(!is_transcript_jsonl("readme.md"));
    }

    #[test]
    fn scan_and_load_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let sessions = dir.path().join("sessions");
        fs::create_dir_all(sessions.join("ws")).unwrap();
        let path = sessions.join("demo.jsonl");
        {
            let mut f = File::create(&path).unwrap();
            writeln!(f, r#"{{"role":"user","content":"hello reasonix"}}"#).unwrap();
            writeln!(f, r#"{{"role":"assistant","content":"hi"}}"#).unwrap();
        }
        let nested = sessions.join("ws").join("nested.jsonl");
        {
            let mut f = File::create(&nested).unwrap();
            writeln!(f, r#"{{"role":"user","content":"nested prompt"}}"#).unwrap();
        }
        // Desktop multi-workspace layout
        let project_sessions = dir
            .path()
            .join("projects")
            .join("c--users-demo")
            .join("sessions");
        fs::create_dir_all(&project_sessions).unwrap();
        let project_path = project_sessions.join("proj-chat.jsonl");
        {
            let mut f = File::create(&project_path).unwrap();
            writeln!(f, r#"{{"role":"user","content":"project workspace chat"}}"#).unwrap();
        }

        let previous = std::env::var_os("REASONIX_HOME");
        std::env::set_var("REASONIX_HOME", dir.path());
        let scanned = scan_sessions();
        match previous {
            Some(v) => std::env::set_var("REASONIX_HOME", v),
            None => std::env::remove_var("REASONIX_HOME"),
        }

        assert_eq!(scanned.len(), 3);
        let demo = scanned
            .iter()
            .find(|s| s.session_id == "demo")
            .expect("demo session");
        assert_eq!(demo.provider_id, "reasonix");
        assert!(demo.title.as_deref().unwrap_or("").contains("hello"));
        assert!(
            scanned.iter().any(|s| s.session_id == "proj-chat"),
            "project workspace session must be listed"
        );

        let messages = load_messages(&path).unwrap();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0].role, "user");
    }
}
