//! Pi 会话用量同步
//!
//! 从 `<pi home>/sessions/<工作目录slug>/<timestamp>_<uuid>.jsonl` 提取
//! assistant message 行中的 usage（fresh input，不含缓存），写入
//! `proxy_request_logs`（`app_type=pi`, `data_source=pi_session`）。
//!
//! ## 事件契约（pi agent session log，每行一个 JSON 事件）
//! ```json
//! {"type":"message","timestamp":"2026-07-22T01:02:05.000Z","message":{
//!   "role":"assistant","api":"openai-completions","provider":"nvidia",
//!   "model":"nemotron","usage":{
//!     "input":161,"output":88,"cacheRead":3072,"cacheWrite":0,
//!     "reasoning":0,"totalTokens":3321,
//!     "cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}
//!   },"stopReason":"toolUse","timestamp":1784697733847,
//!   "responseId":"chatcmpl-..."}}
//! ```
//!
//! 关键语义（已实测验证 input+output+cacheRead+cacheWrite==totalTokens）：
//! pi 的 `usage.input` 是 **fresh input（不含缓存）**，与 kimi/reasonix 的
//! OpenAI 含缓存语义相反。入库直接映射 `input/cacheRead/cacheWrite`，计价用
//! 非 for_app 的 `CostCalculator::calculate`，不走缓存扣减路径。

use crate::database::{lock_conn, Database};
use crate::error::AppError;
use crate::proxy::usage::calculator::CostCalculator;
use crate::proxy::usage::parser::TokenUsage;
use crate::services::session_usage::{
    get_sync_state, metadata_modified_nanos, update_sync_state, SessionSyncResult,
};
use crate::services::usage_stats::{find_model_pricing, should_skip_session_insert, DedupKey};
use rust_decimal::Decimal;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

const REQUEST_ID_PREFIX: &str = "pi_session:v1";

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct UsageTokens {
    /// Fresh input（pi 语义：不含缓存，与 OpenAI 含缓存语义相反）
    input: u64,
    output: u64,
    cache_read: u64,
    cache_write: u64,
}

impl UsageTokens {
    fn is_zero(&self) -> bool {
        self.input == 0 && self.output == 0
    }
}

/// 同步 Pi 会话用量
pub fn sync_pi_usage(db: &Database) -> Result<SessionSyncResult, AppError> {
    let files = collect_session_files();

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
        match sync_single_session_file(db, file_path) {
            Ok((imported, skipped)) => {
                result.imported += imported;
                result.skipped += skipped;
            }
            Err(e) => {
                let msg = format!("Pi 会话文件解析失败 {}: {e}", file_path.display());
                log::warn!("[PI-SYNC] {msg}");
                result.errors.push(msg);
            }
        }
    }

    if result.imported > 0 {
        log::info!(
            "[PI-SYNC] 同步完成: 导入 {} 条, 跳过 {} 条, 扫描 {} 个文件",
            result.imported,
            result.skipped,
            result.files_scanned
        );
    }

    Ok(result)
}

/// `sessions/<一层工作目录slug>/*.jsonl`（无嵌套）
fn collect_session_files() -> Vec<PathBuf> {
    let sessions = crate::pi_config::get_pi_dir().join("sessions");
    let mut files = Vec::new();
    let Ok(entries) = fs::read_dir(&sessions) else {
        return files;
    };
    for entry in entries.flatten() {
        let dir = entry.path();
        if !dir.is_dir() {
            continue;
        }
        let Ok(inner) = fs::read_dir(&dir) else {
            continue;
        };
        for f in inner.flatten() {
            let path = f.path();
            let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            if name.ends_with(".jsonl") {
                files.push(path);
            }
        }
    }
    files
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

/// 从 `<timestamp>_<uuid>.jsonl` 提取 uuid 部分（timestamp 段不含下划线）。
/// uuid 全局唯一，作为 request_id 的稳定 session key。
fn session_uuid_from_path(path: &Path) -> String {
    let stem = path
        .file_stem()
        .and_then(|n| n.to_str())
        .unwrap_or("unknown");
    stem.rsplit_once('_')
        .map(|(_, uuid)| uuid)
        .unwrap_or(stem)
        .to_string()
}

/// 解析 usage 对象，返回 token 明细与上报成本（`cost.total > 0` 时有效）。
fn parse_usage(usage: &serde_json::Value) -> Option<(UsageTokens, Option<f64>)> {
    if !usage.is_object() {
        return None;
    }
    let input = usage.get("input").and_then(|v| v.as_u64()).unwrap_or(0);
    let output = usage.get("output").and_then(|v| v.as_u64()).unwrap_or(0);
    let cache_read = usage
        .get("cacheRead")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let cache_write = usage
        .get("cacheWrite")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let reported_cost = usage
        .get("cost")
        .and_then(|c| c.get("total"))
        .and_then(|v| v.as_f64())
        .filter(|c| c.is_finite() && *c > 0.0);
    let tokens = UsageTokens {
        input,
        output,
        cache_read,
        cache_write,
    };
    if tokens.is_zero() {
        None
    } else {
        Some((tokens, reported_cost))
    }
}

fn parse_rfc3339_secs(raw: &str) -> Option<i64> {
    chrono::DateTime::parse_from_rfc3339(raw)
        .ok()
        .map(|dt| dt.timestamp())
}

fn sync_single_session_file(db: &Database, file_path: &Path) -> Result<(u32, u32), AppError> {
    let file_path_str = file_path.to_string_lossy().to_string();
    let metadata = fs::metadata(file_path)
        .map_err(|e| AppError::Config(format!("无法读取文件元数据: {e}")))?;
    let file_modified = metadata_modified_nanos(&metadata);
    let file_len = metadata.len();

    let (last_modified, mut last_offset) = get_sync_state(db, &file_path_str)?;
    if file_modified <= last_modified {
        return Ok((0, 0));
    }

    let session_uuid = session_uuid_from_path(file_path);
    let session_label = file_path
        .file_stem()
        .and_then(|n| n.to_str())
        .unwrap_or("unknown")
        .to_string();

    let file =
        fs::File::open(file_path).map_err(|e| AppError::Config(format!("无法打开文件: {e}")))?;
    let total_lines = BufReader::new(&file).lines().count() as i64;
    let partial_tail = !file_ends_with_newline(&file, file_len);
    if total_lines < last_offset {
        log::info!(
            "[PI-SYNC] 检测到 {} 被截断或重写（{total_lines} 行 < offset {last_offset}），从头同步",
            file_path.display()
        );
        last_offset = 0;
    }

    let file =
        fs::File::open(file_path).map_err(|e| AppError::Config(format!("无法打开文件: {e}")))?;
    let reader = BufReader::new(file);

    let mut line_offset: i64 = 0;
    let mut message_index: u32 = 0;
    let mut imported: u32 = 0;
    let mut skipped: u32 = 0;

    for line_result in reader.lines() {
        line_offset += 1;
        let line = match line_result {
            Ok(l) => l,
            Err(_) => continue,
        };

        // 粗筛：只关心 assistant message 行
        if !line.contains("\"role\":\"assistant\"") {
            continue;
        }

        let value: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };

        if value.get("type").and_then(|v| v.as_str()) != Some("message") {
            continue;
        }
        let Some(message) = value.get("message") else {
            continue;
        };
        if message.get("role").and_then(|v| v.as_str()) != Some("assistant") {
            continue;
        }
        let Some(usage_val) = message.get("usage") else {
            continue;
        };
        let Some((tokens, reported_cost)) = parse_usage(usage_val) else {
            continue;
        };

        // 全文件稳定计数（含已同步的历史行），保证 request_id 幂等
        message_index += 1;

        if line_offset <= last_offset {
            continue;
        }

        let model = message
            .get("model")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|m| !m.is_empty())
            .unwrap_or("unknown");

        let request_id = format!("{REQUEST_ID_PREFIX}:{session_uuid}:{message_index}");
        // 优先用 message 内的毫秒时间戳，回退顶层 RFC3339 字符串
        let created_at = message
            .get("timestamp")
            .and_then(|v| v.as_i64())
            .map(|ms| ms / 1000)
            .or_else(|| {
                value
                    .get("timestamp")
                    .and_then(|v| v.as_str())
                    .and_then(parse_rfc3339_secs)
            });

        match insert_pi_session_entry(
            db,
            &request_id,
            &tokens,
            model,
            Some(session_label.as_str()),
            created_at,
            reported_cost,
        ) {
            Ok(true) => imported += 1,
            Ok(false) => skipped += 1,
            Err(e) => {
                log::warn!("[PI-SYNC] 插入失败 ({request_id}): {e}");
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

fn insert_pi_session_entry(
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

    // pi 的 usage.input 是 fresh input；入库按 cache-inclusive 语义存
    // （input + cache_read），与 OpenAI 协议代理行及 CACHE_INCLUSIVE 白名单
    // 对齐，避免聚合查询 fresh input 时被二次扣减
    let input_tokens = (tokens.input + tokens.cache_read) as u32;
    let output_tokens = tokens.output as u32;
    let cache_read = tokens.cache_read as u32;
    let cache_creation = tokens.cache_write as u32;

    let dedup_key = DedupKey {
        app_type: "pi",
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
        // 计价用 fresh input：缓存读按缓存价另计，不吃全价
        input_tokens: tokens.input as u32,
        output_tokens,
        cache_read_tokens: cache_read,
        cache_creation_tokens: cache_creation,
        model: Some(model.to_string()),
        message_id: None,
    };

    let (input_cost, output_cost, cache_read_cost, cache_creation_cost, total_cost) =
        if let Some(cost) = reported_cost_usd {
            // 信任上报的 cost.total；分项置 0，避免看板按分项求和时重复计数
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
                    // pi input 是 fresh input，用非 for_app 的 calculate（不扣缓存）
                    let cost = CostCalculator::calculate(&usage, &p, Decimal::from(1));
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
                "_pi_session",
                "pi",
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
                Some("pi_session"),
                1i64,
                "1.0",
                created_at,
                "pi_session",
            ],
        )
        .map_err(|e| AppError::Database(format!("插入 Pi 会话日志失败: {e}")))?;

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

    fn write_session(path: &Path, lines: &[&str]) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, lines.join("\n") + "\n").unwrap();
    }

    #[test]
    fn parse_usage_fresh_input_semantics() {
        let usage = serde_json::json!({
            "input": 161,
            "output": 88,
            "cacheRead": 3072,
            "cacheWrite": 512,
            "reasoning": 0,
            "totalTokens": 3833,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0}
        });
        let (tokens, cost) = parse_usage(&usage).unwrap();
        assert_eq!(tokens.input, 161);
        assert_eq!(tokens.output, 88);
        assert_eq!(tokens.cache_read, 3072);
        assert_eq!(tokens.cache_write, 512);
        assert_eq!(cost, None);

        let usage = serde_json::json!({
            "input": 10,
            "output": 5,
            "cacheRead": 0,
            "cacheWrite": 0,
            "totalTokens": 15,
            "cost": {"total": 0.0003}
        });
        let (_, cost) = parse_usage(&usage).unwrap();
        assert_eq!(cost, Some(0.0003));
    }

    #[test]
    fn session_uuid_extracted_from_filename() {
        assert_eq!(
            session_uuid_from_path(Path::new(
                "/x/sessions/--D--Users-demo--/2026-07-22T01-02-03-456Z_019f84e4-e5a8-7d5f.jsonl"
            )),
            "019f84e4-e5a8-7d5f"
        );
        assert_eq!(
            session_uuid_from_path(Path::new("/x/plain.jsonl")),
            "plain"
        );
    }

    #[test]
    fn sync_imports_assistant_usage_and_is_idempotent() {
        let dir = tempdir().unwrap();
        let home = dir.path().join("pi-home");
        let file = home
            .join("sessions")
            .join("--D--Users-demo--")
            .join("2026-07-22T01-02-03-456Z_019f84e4-e5a8-7d5f-abd3-6d47e800819d.jsonl");
        write_session(
            &file,
            &[
                // user message：应被跳过
                r#"{"type":"message","timestamp":"2026-07-22T01:02:04.000Z","message":{"role":"user","content":"hi"}}"#,
                // assistant：cacheRead/cacheWrite 非零，cost.total=0（内存库无定价 → 成本 0）
                r#"{"type":"message","timestamp":"2026-07-22T01:02:05.000Z","message":{"role":"assistant","api":"openai-completions","provider":"nvidia","model":"nemotron","usage":{"input":161,"output":88,"cacheRead":3072,"cacheWrite":512,"reasoning":0,"totalTokens":3833,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"toolUse","timestamp":1784697733847,"responseId":"chatcmpl-1"}}"#,
                // assistant：cost.total>0 → 信任上报成本
                r#"{"type":"message","timestamp":"2026-07-22T01:02:06.000Z","message":{"role":"assistant","api":"openai-completions","provider":"nvidia","model":"nemotron","usage":{"input":10,"output":5,"cacheRead":0,"cacheWrite":0,"totalTokens":15,"cost":{"input":0.0001,"output":0.0002,"cacheRead":0,"cacheWrite":0,"total":0.0003}},"stopReason":"stop","timestamp":1784697735000,"responseId":"chatcmpl-2"}}"#,
            ],
        );

        let previous = std::env::var_os("PI_AGENT_HOME");
        std::env::set_var("PI_AGENT_HOME", &home);

        let db = Database::memory().expect("create memory db");
        let result = sync_pi_usage(&db).expect("sync");

        assert_eq!(result.files_scanned, 1);
        assert_eq!(result.imported, 2);
        assert!(result.errors.is_empty());

        let conn = db.conn.lock().expect("db lock");
        // 第一条：token 直接映射（input 不扣缓存）
        let (app_type, provider_id, input, output, cache_read, cache_write, data_source): (
            String,
            String,
            i64,
            i64,
            i64,
            i64,
            String,
        ) = conn
            .query_row(
                "SELECT app_type, provider_id, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, data_source
                 FROM proxy_request_logs
                 WHERE request_id = 'pi_session:v1:019f84e4-e5a8-7d5f-abd3-6d47e800819d:1'",
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
            .expect("row 1");
        assert_eq!(app_type, "pi");
        assert_eq!(provider_id, "_pi_session");
        // 含缓存存储：fresh input 161 + cacheRead 3072 = 3233（与白名单口径对齐）
        assert_eq!(input, 3233);
        assert_eq!(output, 88);
        assert_eq!(cache_read, 3072);
        assert_eq!(cache_write, 512);
        assert_eq!(data_source, "pi_session");

        // 第二条：cost.total>0 → 信任上报成本
        let total_cost: String = conn
            .query_row(
                "SELECT total_cost_usd FROM proxy_request_logs
                 WHERE request_id = 'pi_session:v1:019f84e4-e5a8-7d5f-abd3-6d47e800819d:2'",
                [],
                |row| row.get(0),
            )
            .expect("row 2");
        assert!(total_cost.starts_with("0.0003"), "total_cost={total_cost}");

        drop(conn);
        // 重复同步：mtime/offset 未变 → 幂等无新导入
        let again = sync_pi_usage(&db).expect("resync");

        // 在两次同步都完成后再恢复环境变量，避免落到真实 sessions 目录
        match previous {
            Some(v) => std::env::set_var("PI_AGENT_HOME", v),
            None => std::env::remove_var("PI_AGENT_HOME"),
        }

        assert_eq!(again.imported, 0);
    }
}
