use serde_json::Value;
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

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
    scan_sessions_in(&session_root(), &session_index_path())
}

fn scan_sessions_in(root: &Path, index_path: &Path) -> Vec<SessionMeta> {
    let Ok(file) = File::open(index_path) else {
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
        // 官方 session_index.jsonl 是 append-only：{sessionId, deleted:true} 墓碑
        // 表示该会话已被官方删除（session-index.ts readSessionIndex：
        // 'deleted' in record → result.delete(sessionId)）。即使目录因
        // 半截失败仍存在，也不得复活该会话。
        if value.get("deleted").and_then(Value::as_bool) == Some(true) {
            indexed.remove(session_id);
            continue;
        }
        let Some(session_dir) = value.get("sessionDir").and_then(Value::as_str) else {
            continue;
        };
        let indexed_session_dir = PathBuf::from(session_dir);
        let Some(session_dir) = resolve_indexed_session_dir(root, &indexed_session_dir, session_id)
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
            // 官方 v2 state.json 写 cwd（sessionMetadataService.ts），v1 写 workDir；
            // 回退顺序对齐官方 recoverCwd（sessionIndexService.ts）：cwd → workDir → 索引 workDir
            let work_dir = state
                .get("cwd")
                .and_then(Value::as_str)
                .map(str::to_string)
                .or_else(|| {
                    state
                        .get("workDir")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                })
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
    delete_session_in(root, source_path, session_id, &session_index_path())
}

/// 与官方一致的删除语义（session-store.ts delete）：
/// 向 session_index.jsonl 追加 {sessionId, deleted:true} 墓碑（O_APPEND）后
/// 再 rm 会话目录。先写墓碑再删目录：崩溃时至多多一条 tombstone，不会把已删
/// 会话复活。read-modify-write 整文件重写会覆盖 kimi CLI 并发追加行，故索引
/// 只做追加。
fn delete_session_in(
    root: &Path,
    source_path: &Path,
    session_id: &str,
    index_path: &Path,
) -> Result<bool, String> {
    let resolved = resolve_indexed_session_dir(root, source_path, session_id)
        .ok_or_else(|| format!("Invalid Kimi session directory: {}", source_path.display()))?;
    // Tombstone first so a crash mid-delete never re-lists the session.
    append_index_tombstone(index_path, session_id)?;
    fs::remove_dir_all(&resolved)
        .map_err(|e| format!("Failed to delete Kimi session directory: {e}"))?;
    Ok(true)
}

/// 向 session_index.jsonl 追加一行 {sessionId, deleted:true} 墓碑（O_APPEND）
fn append_index_tombstone(index_path: &Path, session_id: &str) -> Result<(), String> {
    use std::io::{Read, Seek, SeekFrom, Write};

    let mut file = fs::OpenOptions::new()
        .read(true)
        .append(true)
        .create(true)
        .open(index_path)
        .map_err(|e| format!("Failed to open Kimi session index: {e}"))?;

    // 已有内容缺结尾换行时先补一个，避免墓碑行与尾行粘连
    let file_len = file.metadata().map(|m| m.len()).unwrap_or(0);
    if file_len > 0 {
        let mut last = [0u8; 1];
        if file.seek(SeekFrom::End(-1)).is_ok()
            && file.read_exact(&mut last).is_ok()
            && last[0] != b'\n'
        {
            file.write_all(b"\n")
                .map_err(|e| format!("Failed to append Kimi session index tombstone: {e}"))?;
        }
    }

    let tombstone = serde_json::json!({"sessionId": session_id, "deleted": true}).to_string();
    writeln!(file, "{tombstone}")
        .map_err(|e| format!("Failed to append Kimi session index tombstone: {e}"))?;
    Ok(())
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

    fn make_session(root: &Path, wd: &str, session_id: &str, state: &str) -> PathBuf {
        let dir = root.join(wd).join(session_id);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("state.json"), state).unwrap();
        dir
    }

    fn index_entry(session_dir: &Path, session_id: &str, work_dir: &str) -> String {
        serde_json::json!({
            "sessionId": session_id,
            "sessionDir": session_dir.to_string_lossy(),
            "workDir": work_dir,
        })
        .to_string()
    }

    fn scan_map(root: &Path, index_path: &Path) -> HashMap<String, SessionMeta> {
        scan_sessions_in(root, index_path)
            .into_iter()
            .map(|meta| (meta.session_id.clone(), meta))
            .collect()
    }

    /// state.json v2 用 cwd（sessionMetadataService.ts），v1 用 workDir；
    /// 回退顺序对齐官方 recoverCwd：state.cwd → state.workDir → 索引 workDir
    #[test]
    fn scan_recovers_work_dir_from_cwd_then_workdir_then_index() {
        let temp = tempdir().unwrap();
        let root = temp.path().join("sessions");
        let v2 = make_session(
            &root,
            "work",
            "session-v2",
            r#"{"version":2,"cwd":"/v2/work","title":"V2"}"#,
        );
        let v1 = make_session(
            &root,
            "work",
            "session-v1",
            r#"{"workDir":"/v1/work","title":"V1"}"#,
        );
        let bare = make_session(&root, "work", "session-idx", r#"{"title":"Idx"}"#);

        let index_path = temp.path().join("session_index.jsonl");
        fs::write(
            &index_path,
            format!(
                "{}\n{}\n{}\n",
                index_entry(&v2, "session-v2", "/idx/v2"),
                index_entry(&v1, "session-v1", "/idx/v1"),
                index_entry(&bare, "session-idx", "/idx/fallback"),
            ),
        )
        .unwrap();

        let metas = scan_map(&root, &index_path);
        assert_eq!(metas.len(), 3);
        assert_eq!(
            metas["session-v2"].project_dir.as_deref(),
            Some("/v2/work"),
            "v2 state.json 的 cwd 优先"
        );
        assert_eq!(
            metas["session-v1"].project_dir.as_deref(),
            Some("/v1/work"),
            "v1 state.json 的 workDir 兜底"
        );
        assert_eq!(
            metas["session-idx"].project_dir.as_deref(),
            Some("/idx/fallback"),
            "state 两者皆缺时回退索引 workDir"
        );
    }

    /// 官方 session_index.jsonl 有 {sessionId, deleted:true} 墓碑
    /// （session-index.ts readSessionIndex：遇 deleted 从 map 中 delete）。
    /// "墓碑在、目录也在" 的半截失败场景不得复活官方已删会话；
    /// 墓碑之后的同名新条目（官方重建）仍然生效。
    #[test]
    fn scan_removes_tombstoned_sessions_even_if_dir_exists() {
        let temp = tempdir().unwrap();
        let root = temp.path().join("sessions");
        let dir_a = make_session(&root, "work", "session-a", r#"{"title":"A","cwd":"/w/a"}"#);
        let dir_b = make_session(&root, "work", "session-b", r#"{"title":"B","cwd":"/w/b"}"#);

        let index_path = temp.path().join("session_index.jsonl");
        fs::write(
            &index_path,
            format!(
                "{}\n{}\n{}\n",
                index_entry(&dir_a, "session-a", "/w/a"),
                index_entry(&dir_b, "session-b", "/w/b"),
                serde_json::json!({"sessionId": "session-a", "deleted": true}),
            ),
        )
        .unwrap();

        let metas = scan_map(&root, &index_path);
        assert!(
            !metas.contains_key("session-a"),
            "墓碑会话不得因目录残留而复活"
        );
        assert!(metas.contains_key("session-b"));

        // 官方 later-lines-win：墓碑之后出现同名新条目 → 重新出现
        fs::write(
            &index_path,
            format!(
                "{}{}\n",
                fs::read_to_string(&index_path).unwrap(),
                index_entry(&dir_a, "session-a", "/w/a"),
            ),
        )
        .unwrap();
        let metas = scan_map(&root, &index_path);
        assert!(metas.contains_key("session-a"));
    }

    /// 删除 = rm 会话目录 + 向 session_index.jsonl 追加墓碑行（O_APPEND，
    /// 对齐官方 session-store.ts delete），既有索引行保持原样不被重写；
    /// 删除后再 scan 不出现该会话。
    #[test]
    fn delete_session_removes_dir_and_appends_tombstone() {
        let temp = tempdir().unwrap();
        let root = temp.path().join("sessions");
        let dir_a = make_session(&root, "work", "session-a", r#"{"title":"A","cwd":"/w/a"}"#);
        let dir_b = make_session(&root, "work", "session-b", r#"{"title":"B","cwd":"/w/b"}"#);

        let index_path = temp.path().join("session_index.jsonl");
        let original = format!(
            "{}\n{}\n",
            index_entry(&dir_a, "session-a", "/w/a"),
            index_entry(&dir_b, "session-b", "/w/b"),
        );
        fs::write(&index_path, &original).unwrap();

        assert!(delete_session_in(&root, &dir_a, "session-a", &index_path).unwrap());
        assert!(!dir_a.exists(), "会话目录必须被删除");
        assert!(dir_b.exists());

        // 索引只做追加：原有行逐字节保留，末尾新增一行墓碑
        let content = fs::read_to_string(&index_path).unwrap();
        assert!(content.starts_with(&original), "既有索引行不得被重写");
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(lines.len(), 3);
        let tombstone: Value = serde_json::from_str(lines[2]).unwrap();
        assert_eq!(
            tombstone,
            serde_json::json!({"sessionId": "session-a", "deleted": true})
        );

        // 联动第 3 点：删除后再 scan 不出现
        let metas = scan_map(&root, &index_path);
        assert!(!metas.contains_key("session-a"));
        assert!(metas.contains_key("session-b"));
    }

    /// 索引文件缺结尾换行时，追加墓碑前先补换行，避免与尾行粘连
    #[test]
    fn append_index_tombstone_separates_missing_trailing_newline() {
        let temp = tempdir().unwrap();
        let index_path = temp.path().join("session_index.jsonl");
        let entry =
            serde_json::json!({"sessionId": "x", "sessionDir": "/d", "workDir": "/w"}).to_string();
        fs::write(&index_path, &entry).unwrap(); // 无结尾换行

        append_index_tombstone(&index_path, "x").unwrap();

        let content = fs::read_to_string(&index_path).unwrap();
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(lines.len(), 2);
        let first: Value = serde_json::from_str(lines[0]).unwrap();
        assert_eq!(first.get("sessionId").and_then(Value::as_str), Some("x"));
        let second: Value = serde_json::from_str(lines[1]).unwrap();
        assert_eq!(
            second,
            serde_json::json!({"sessionId": "x", "deleted": true})
        );
    }
}
