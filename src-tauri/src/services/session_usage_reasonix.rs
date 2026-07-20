//! Reasonix 会话用量同步
//!
//! 从 `<reasonix home>/sessions/**/*.events.jsonl` 提取 `model.final` 事件中的
//! usage（OpenAI 风格 prompt/completion + cache hit/miss），写入
//! `proxy_request_logs`（`app_type=reasonix`, `data_source=reasonix_session`）。
//!
//! ## 事件契约（DeepSeek-Reasonix event log）
//! ```json
//! {"type":"model.turn.started","turn":1,"model":"deepseek-v4-flash",...}
//! {"type":"model.final","turn":1,"usage":{
//!   "prompt_tokens":16679,"completion_tokens":136,
//!   "prompt_cache_hit_tokens":0,"prompt_cache_miss_tokens":16679
//! },"costUsd":0.002}
//! ```
//!
//! `prompt_tokens` 为含缓存的总输入；计费时 CostCalculator 会扣 cache_read。

use crate::database::{lock_conn, Database};
use crate::error::AppError;
use crate::proxy::usage::calculator::CostCalculator;
use crate::proxy::usage::parser::TokenUsage;
use crate::services::session_usage::{
    get_sync_state, metadata_modified_nanos, update_sync_state, SessionSyncResult,
};
use crate::services::usage_stats::{find_model_pricing, should_skip_session_insert, DedupKey};
use rust_decimal::Decimal;
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

const REQUEST_ID_PREFIX: &str = "reasonix_session:v1";

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct UsageTokens {
    /// Total prompt tokens (cache-inclusive, OpenAI style)
    prompt_tokens: u64,
    completion_tokens: u64,
    cache_hit: u64,
    cache_miss: u64,
}

impl UsageTokens {
    fn is_zero(&self) -> bool {
        self.prompt_tokens == 0 && self.completion_tokens == 0
    }
}

/// 同步 Reasonix 会话用量
pub fn sync_reasonix_usage(db: &Database) -> Result<SessionSyncResult, AppError> {
    let sessions_dir = crate::reasonix_config::get_reasonix_dir().join("sessions");
    let files = collect_events_files(&sessions_dir);

    let mut result = SessionSyncResult {
        imported: 0,
        skipped: 0,
        files_scanned: files.len() as u32,
        errors: vec![],
    };

    if files.is_empty() {
        return Ok(result);
    }

    for file_path in &files {
        match sync_single_events_file(db, file_path) {
            Ok((imported, skipped)) => {
                result.imported += imported;
                result.skipped += skipped;
            }
            Err(e) => {
                let msg = format!(
                    "Reasonix 会话文件解析失败 {}: {e}",
                    file_path.display()
                );
                log::warn!("[REASONIX-SYNC] {msg}");
                result.errors.push(msg);
            }
        }
    }

    if result.imported > 0 {
        log::info!(
            "[REASONIX-SYNC] 同步完成: 导入 {} 条, 跳过 {} 条, 扫描 {} 个文件",
            result.imported,
            result.skipped,
            result.files_scanned
        );
    }

    Ok(result)
}

fn collect_events_files(sessions_dir: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    collect_events_recursive(sessions_dir, &mut files, 0, 2);
    files
}

fn collect_events_recursive(dir: &Path, files: &mut Vec<PathBuf>, depth: u32, max_depth: u32) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() && depth < max_depth {
            collect_events_recursive(&path, files, depth + 1, max_depth);
            continue;
        }
        let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        // Transcript sidecars: <stem>.events.jsonl
        if name.ends_with(".events.jsonl") && !name.ends_with(".guardian.jsonl") {
            files.push(path);
        }
    }
}

fn file_ends_with_newline(file: &fs::File, file_len: u64) -> bool {
    use std::io::{Read, Seek, SeekFrom};
    if file_len == 0 {
        return true;
    }
    let mut cursor = file;
    if cursor.seek(SeekFrom::End(-1)).is_err() {
        return false;
    }
    let mut byte = [0u8; 1];
    cursor.read_exact(&mut byte).is_ok() && byte[0] == b'\n'
}

fn session_id_from_events_path(path: &Path) -> String {
    path.file_name()
        .and_then(|n| n.to_str())
        .and_then(|n| n.strip_suffix(".events.jsonl"))
        .unwrap_or("unknown")
        .to_string()
}

fn parse_usage(usage: &serde_json::Value) -> Option<UsageTokens> {
    if !usage.is_object() {
        return None;
    }
    let prompt_tokens = usage
        .get("prompt_tokens")
        .or_else(|| usage.get("input_tokens"))
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let completion_tokens = usage
        .get("completion_tokens")
        .or_else(|| usage.get("output_tokens"))
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let cache_hit = usage
        .get("prompt_cache_hit_tokens")
        .or_else(|| usage.get("cache_read_tokens"))
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let cache_miss = usage
        .get("prompt_cache_miss_tokens")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let tokens = UsageTokens {
        prompt_tokens,
        completion_tokens,
        cache_hit,
        cache_miss,
    };
    if tokens.is_zero() {
        None
    } else {
        Some(tokens)
    }
}

fn parse_rfc3339_secs(raw: &str) -> Option<i64> {
    chrono::DateTime::parse_from_rfc3339(raw)
        .ok()
        .map(|dt| dt.timestamp())
}

fn sync_single_events_file(db: &Database, file_path: &Path) -> Result<(u32, u32), AppError> {
    let file_path_str = file_path.to_string_lossy().to_string();
    let metadata = fs::metadata(file_path)
        .map_err(|e| AppError::Config(format!("无法读取文件元数据: {e}")))?;
    let file_modified = metadata_modified_nanos(&metadata);
    let file_len = metadata.len();

    let (last_modified, mut last_offset) = get_sync_state(db, &file_path_str)?;
    if file_modified <= last_modified {
        return Ok((0, 0));
    }

    let session_id = session_id_from_events_path(file_path);

    let file =
        fs::File::open(file_path).map_err(|e| AppError::Config(format!("无法打开文件: {e}")))?;
    let total_lines = BufReader::new(&file).lines().count() as i64;
    let partial_tail = !file_ends_with_newline(&file, file_len);
    if total_lines < last_offset {
        log::info!(
            "[REASONIX-SYNC] 检测到 {} 被截断或重写（{total_lines} 行 < offset {last_offset}），从头同步",
            file_path.display()
        );
        last_offset = 0;
    }

    let file =
        fs::File::open(file_path).map_err(|e| AppError::Config(format!("无法打开文件: {e}")))?;
    let reader = BufReader::new(file);

    let mut line_offset: i64 = 0;
    let mut final_index: u32 = 0;
    let mut imported: u32 = 0;
    let mut skipped: u32 = 0;
    // turn → last model from model.turn.started
    let mut turn_models: HashMap<i64, String> = HashMap::new();
    let mut last_model = String::from("unknown");

    for line_result in reader.lines() {
        line_offset += 1;
        let line = match line_result {
            Ok(l) => l,
            Err(_) => continue,
        };

        // Cheap prefilter before JSON parse
        if !line.contains("\"model.final\"") && !line.contains("\"model.turn.started\"") {
            continue;
        }

        let value: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };

        let event_type = value.get("type").and_then(|v| v.as_str()).unwrap_or("");

        if event_type == "model.turn.started" {
            if let Some(model) = value.get("model").and_then(|v| v.as_str()) {
                let model = model.trim();
                if !model.is_empty() {
                    last_model = model.to_string();
                    if let Some(turn) = value.get("turn").and_then(|v| v.as_i64()) {
                        turn_models.insert(turn, last_model.clone());
                    }
                }
            }
            continue;
        }

        if event_type != "model.final" {
            continue;
        }

        let Some(usage_val) = value.get("usage") else {
            continue;
        };
        let Some(tokens) = parse_usage(usage_val) else {
            continue;
        };

        // Stable index for all non-zero model.final events (even past lines)
        final_index += 1;

        if line_offset <= last_offset {
            // Still track model map for continuity when replaying history
            continue;
        }

        let turn = value.get("turn").and_then(|v| v.as_i64());
        let model = turn
            .and_then(|t| turn_models.get(&t).cloned())
            .filter(|m| !m.is_empty())
            .unwrap_or_else(|| last_model.clone());

        let request_id = format!("{REQUEST_ID_PREFIX}:{session_id}:{final_index}");
        let created_at = value
            .get("ts")
            .and_then(|v| v.as_str())
            .and_then(parse_rfc3339_secs);

        // Prefer provider-reported cost when present; still store token breakdown.
        let reported_cost = value
            .get("costUsd")
            .and_then(|v| v.as_f64())
            .filter(|c| c.is_finite() && *c >= 0.0);

        match insert_reasonix_session_entry(
            db,
            &request_id,
            &tokens,
            &model,
            Some(session_id.as_str()),
            created_at,
            reported_cost,
        ) {
            Ok(true) => imported += 1,
            Ok(false) => skipped += 1,
            Err(e) => {
                log::warn!("[REASONIX-SYNC] 插入失败 ({request_id}): {e}");
                skipped += 1;
            }
        }
    }

    let committed_offset = if partial_tail {
        (line_offset - 1).max(0)
    } else {
        line_offset
    };
    update_sync_state(db, &file_path_str, file_modified, committed_offset)?;

    Ok((imported, skipped))
}

fn insert_reasonix_session_entry(
    db: &Database,
    request_id: &str,
    tokens: &UsageTokens,
    model: &str,
    session_id: Option<&str>,
    created_at_secs: Option<i64>,
    reported_cost_usd: Option<f64>,
) -> Result<bool, AppError> {
    let conn = lock_conn!(db.conn);

    let created_at = created_at_secs.unwrap_or_else(|| {
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0)
    });

    let input_tokens = tokens.prompt_tokens as u32;
    let output_tokens = tokens.completion_tokens as u32;
    let cache_read = tokens.cache_hit as u32;
    // Reasonix events expose cache hit/miss, not Anthropic-style cache creation.
    let cache_creation = 0u32;

    let dedup_key = DedupKey {
        app_type: "reasonix",
        model,
        input_tokens,
        output_tokens,
        cache_read_tokens: cache_read,
        cache_creation_tokens: cache_creation,
        created_at,
    };
    if should_skip_session_insert(&conn, request_id, &dedup_key)? {
        return Ok(false);
    }

    let usage = TokenUsage {
        input_tokens,
        output_tokens,
        cache_read_tokens: cache_read,
        cache_creation_tokens: cache_creation,
        model: Some(model.to_string()),
        message_id: None,
    };

    let (
        input_cost,
        output_cost,
        cache_read_cost,
        cache_creation_cost,
        total_cost,
    ) = if let Some(cost) = reported_cost_usd {
        // Trust upstream costUsd for total; leave component costs at 0 to avoid
        // double-counting when the dashboard sums components.
        (
            "0".to_string(),
            "0".to_string(),
            "0".to_string(),
            "0".to_string(),
            format!("{cost}"),
        )
    } else {
        let pricing = find_model_pricing(&conn, model);
        match pricing {
            Some(p) => {
                let cost =
                    CostCalculator::calculate_for_app("reasonix", &usage, &p, Decimal::from(1));
                (
                    cost.input_cost.to_string(),
                    cost.output_cost.to_string(),
                    cost.cache_read_cost.to_string(),
                    cost.cache_creation_cost.to_string(),
                    cost.total_cost.to_string(),
                )
            }
            None => (
                "0".to_string(),
                "0".to_string(),
                "0".to_string(),
                "0".to_string(),
                "0".to_string(),
            ),
        }
    };

    let inserted_rows = conn
        .execute(
            "INSERT OR IGNORE INTO proxy_request_logs (
            request_id, provider_id, app_type, model, request_model,
            input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
            input_cost_usd, output_cost_usd, cache_read_cost_usd, cache_creation_cost_usd, total_cost_usd,
            latency_ms, first_token_ms, status_code, error_message, session_id,
            provider_type, is_streaming, cost_multiplier, created_at, data_source
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23, ?24)",
            rusqlite::params![
                request_id,
                "_reasonix_session",
                "reasonix",
                model,
                model,
                input_tokens,
                output_tokens,
                cache_read,
                cache_creation,
                input_cost,
                output_cost,
                cache_read_cost,
                cache_creation_cost,
                total_cost,
                0i64,
                Option::<i64>::None,
                200i64,
                Option::<String>::None,
                session_id.map(|s| s.to_string()),
                Some("reasonix_session"),
                1i64,
                "1.0",
                created_at,
                "reasonix_session",
            ],
        )
        .map_err(|e| AppError::Database(format!("插入 Reasonix 会话日志失败: {e}")))?;

    if inserted_rows > 0 {
        crate::usage_events::notify_log_recorded();
    }

    Ok(inserted_rows > 0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::database::Database;
    use tempfile::tempdir;

    fn write_events(path: &Path, lines: &[&str]) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, lines.join("\n") + "\n").unwrap();
    }

    #[test]
    fn parse_usage_reads_openai_style_fields() {
        let usage = serde_json::json!({
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 40,
            "prompt_cache_miss_tokens": 60
        });
        let tokens = parse_usage(&usage).unwrap();
        assert_eq!(tokens.prompt_tokens, 100);
        assert_eq!(tokens.completion_tokens, 20);
        assert_eq!(tokens.cache_hit, 40);
        assert_eq!(tokens.cache_miss, 60);
    }

    #[test]
    fn sync_imports_model_final_with_turn_model() {
        let dir = tempdir().unwrap();
        let home = dir.path().join("reasonix-home");
        let sessions = home.join("sessions");
        let events = sessions.join("demo.events.jsonl");
        write_events(
            &events,
            &[
                r#"{"id":1,"ts":"2026-05-31T06:22:04.863Z","turn":1,"type":"model.turn.started","model":"deepseek-v4-flash"}"#,
                r#"{"id":2,"ts":"2026-05-31T06:22:05.620Z","turn":1,"type":"model.final","content":"hi","usage":{"prompt_tokens":100,"completion_tokens":10,"total_tokens":110,"prompt_cache_hit_tokens":20,"prompt_cache_miss_tokens":80},"costUsd":0.001}"#,
                r#"{"id":3,"ts":"2026-05-31T06:22:06.000Z","turn":1,"type":"status","text":"ok"}"#,
            ],
        );

        let previous = std::env::var_os("REASONIX_HOME");
        std::env::set_var("REASONIX_HOME", &home);

        let db = Database::memory().expect("create memory db");
        let result = sync_reasonix_usage(&db).expect("sync");

        match previous {
            Some(v) => std::env::set_var("REASONIX_HOME", v),
            None => std::env::remove_var("REASONIX_HOME"),
        }

        assert_eq!(result.files_scanned, 1);
        assert_eq!(result.imported, 1);
        assert!(result.errors.is_empty());

        let conn = db.conn.lock().expect("db lock");
        let (app_type, model, input, output, cache_read, data_source, total_cost): (
            String,
            String,
            i64,
            i64,
            i64,
            String,
            String,
        ) = conn
            .query_row(
                "SELECT app_type, model, input_tokens, output_tokens, cache_read_tokens, data_source, total_cost_usd
                 FROM proxy_request_logs WHERE data_source = 'reasonix_session'",
                [],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                        row.get(5)?,
                        row.get(6)?,
                    ))
                },
            )
            .expect("row");
        assert_eq!(app_type, "reasonix");
        assert_eq!(model, "deepseek-v4-flash");
        assert_eq!(input, 100);
        assert_eq!(output, 10);
        assert_eq!(cache_read, 20);
        assert_eq!(data_source, "reasonix_session");
        assert!(total_cost.starts_with("0.001") || total_cost == "0.001");

        drop(conn);
        // Second sync is a no-op (mtime/offset)
        let again = sync_reasonix_usage(&db).expect("resync");
        assert_eq!(again.imported, 0);
    }

    #[test]
    fn session_id_from_path() {
        assert_eq!(
            session_id_from_events_path(Path::new("/x/code-jh.events.jsonl")),
            "code-jh"
        );
    }
}
