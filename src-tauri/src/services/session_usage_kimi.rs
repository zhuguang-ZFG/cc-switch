//! Kimi Code 会话日志使用追踪
//!
//! 从 ~/.kimi-code/sessions/<wd>/<session>/agents/<agent>/wire.jsonl 提取
//! `usage.record` 事件中的精确 token 用量（含主代理 main 与子代理 agent-N）。
//!
//! ## 数据流
//! ```text
//! wire.jsonl → usage.record(usageScope="turn") 增量解析 → 费用计算 → proxy_request_logs 表
//! ```
//!
//! ## 解析的事件类型
//! - `usage.record` (usageScope="turn") → 单次 LLM 调用的增量 token 用量
//! - `usage.record` (usageScope="session") → 会话级汇总，跳过以避免重复计数
//!
//! ## usage 字段（实测自 Kimi Code CLI wire.jsonl）
//! - `inputOther` → 非缓存输入 token
//! - `inputCacheRead` → 缓存读取 token
//! - `inputCacheCreation` → 缓存写入 token
//! - `output` → 输出 token

use crate::database::{lock_conn, Database};
use crate::error::AppError;
use crate::kimi_config::get_kimi_dir;
use crate::proxy::usage::calculator::{CostCalculator, ModelPricing};
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

const KIMI_TURN_REQUEST_ID_PREFIX: &str = "kimi_session:turn-v1";

/// 单次 LLM 调用的 token 增量（usageScope="turn"）
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct TurnTokens {
    input_other: u64,
    cache_read: u64,
    cache_creation: u64,
    output: u64,
}

impl TurnTokens {
    fn is_zero(&self) -> bool {
        self.input_other == 0
            && self.cache_read == 0
            && self.cache_creation == 0
            && self.output == 0
    }

    /// kimicode 计费采用 OpenAI 风格语义：input_tokens 包含缓存部分，
    /// 费用计算时由 CostCalculator 扣除 cache_read/cache_creation。
    fn total_input(&self) -> u64 {
        self.input_other + self.cache_read + self.cache_creation
    }
}

/// 从 usage.record 事件中提取 turn 级 token 增量
fn parse_turn_usage(record: &serde_json::Value) -> Option<TurnTokens> {
    if record.get("type").and_then(|v| v.as_str()) != Some("usage.record") {
        return None;
    }
    if record.get("usageScope").and_then(|v| v.as_str()) != Some("turn") {
        return None;
    }
    let usage = record.get("usage")?;
    if !usage.is_object() {
        return None;
    }
    Some(TurnTokens {
        input_other: usage
            .get("inputOther")
            .and_then(|v| v.as_u64())
            .unwrap_or(0),
        cache_read: usage
            .get("inputCacheRead")
            .and_then(|v| v.as_u64())
            .unwrap_or(0),
        cache_creation: usage
            .get("inputCacheCreation")
            .and_then(|v| v.as_u64())
            .unwrap_or(0),
        output: usage.get("output").and_then(|v| v.as_u64()).unwrap_or(0),
    })
}

/// 归一化 Kimi 模型名：小写 + 剥离 provider 前缀（`kimi-code/k3` → `k3`）
fn normalize_kimi_model(raw: &str) -> String {
    let name = raw.to_lowercase();
    match name.rfind('/') {
        Some(pos) => name[pos + 1..].to_string(),
        None => name,
    }
}

/// wire.jsonl 路径解剖：.../<session_dir>/agents/<agent>/wire.jsonl
/// 返回 (session_id, agent_name)
fn wire_file_identity(file_path: &Path) -> (Option<String>, Option<String>) {
    let agent = file_path
        .parent()
        .and_then(Path::file_name)
        .and_then(|name| name.to_str())
        .map(str::to_string);
    let session = file_path
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .and_then(Path::file_name)
        .and_then(|name| name.to_str())
        .map(str::to_string);
    (session, agent)
}

/// 同步 Kimi Code 使用数据（从 wire.jsonl 会话日志）
pub fn sync_kimi_usage(db: &Database) -> Result<SessionSyncResult, AppError> {
    let sessions_dir = get_kimi_dir().join("sessions");

    let files = collect_kimi_wire_files(&sessions_dir);

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
        match sync_single_kimi_file(db, file_path) {
            Ok((imported, skipped)) => {
                result.imported += imported;
                result.skipped += skipped;
            }
            Err(e) => {
                let msg = format!("Kimi 会话文件解析失败 {}: {e}", file_path.display());
                log::warn!("[KIMI-SYNC] {msg}");
                result.errors.push(msg);
            }
        }
    }

    if result.imported > 0 {
        log::info!(
            "[KIMI-SYNC] 同步完成: 导入 {} 条, 跳过 {} 条, 扫描 {} 个文件",
            result.imported,
            result.skipped,
            result.files_scanned
        );
    }

    Ok(result)
}

/// 收集 sessions 目录下所有 agents/*/wire.jsonl 文件
/// 目录层级固定为 <wd>/<session>/agents/<agent>/wire.jsonl（4 层）
fn collect_kimi_wire_files(sessions_dir: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    collect_wire_files_recursive(sessions_dir, &mut files, 0, 4);
    files
}

fn collect_wire_files_recursive(dir: &Path, files: &mut Vec<PathBuf>, depth: u32, max_depth: u32) {
    let entries = match fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };

    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() && depth < max_depth {
            collect_wire_files_recursive(&path, files, depth + 1, max_depth);
        } else if path.file_name().and_then(|name| name.to_str()) == Some("wire.jsonl") {
            files.push(path);
        }
    }
}

/// 同步单个 Kimi wire.jsonl 文件，返回 (imported, skipped)
fn sync_single_kimi_file(db: &Database, file_path: &Path) -> Result<(u32, u32), AppError> {
    let file_path_str = file_path.to_string_lossy().to_string();

    // 获取文件元数据
    let metadata = fs::metadata(file_path)
        .map_err(|e| AppError::Config(format!("无法读取文件元数据: {e}")))?;
    let file_modified = metadata_modified_nanos(&metadata);

    // 检查同步状态
    let (last_modified, last_offset) = get_sync_state(db, &file_path_str)?;

    // 文件未变化则跳过
    if file_modified <= last_modified {
        return Ok((0, 0));
    }

    let (session_id, agent_name) = wire_file_identity(file_path);

    let file =
        fs::File::open(file_path).map_err(|e| AppError::Config(format!("无法打开文件: {e}")))?;
    let reader = BufReader::new(file);

    let mut line_offset: i64 = 0;
    let mut event_index: u32 = 0;
    let mut imported: u32 = 0;
    let mut skipped: u32 = 0;

    for line_result in reader.lines() {
        line_offset += 1;

        let line = match line_result {
            Ok(l) => l,
            Err(_) => continue, // 容忍不完整的最后一行
        };

        // 快速过滤：在 JSON 反序列化前跳过无关行
        if !line.contains("\"usage.record\"") {
            continue;
        }

        let value: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };

        let tokens = match parse_turn_usage(&value) {
            Some(t) => t,
            None => continue, // 含 usageScope="session" 的汇总记录
        };

        if tokens.is_zero() {
            continue;
        }

        // 所有非零 turn 事件都占据稳定序号，保证增量同步时 request_id 不变
        event_index += 1;

        // 跳过已处理的行
        if line_offset <= last_offset {
            continue;
        }

        let model = value
            .get("model")
            .and_then(|v| v.as_str())
            .map(normalize_kimi_model)
            .unwrap_or_else(|| "unknown".to_string());

        let session_label = session_id.as_deref().unwrap_or("unknown");
        let agent_label = agent_name.as_deref().unwrap_or("main");
        let request_id =
            format!("{KIMI_TURN_REQUEST_ID_PREFIX}:{session_label}:{agent_label}:{event_index}");

        // time 为毫秒时间戳
        let created_at = value.get("time").and_then(|v| v.as_i64());

        match insert_kimi_session_entry(
            db,
            &request_id,
            &tokens,
            &model,
            session_id.as_deref(),
            created_at,
        ) {
            Ok(true) => imported += 1,
            Ok(false) => skipped += 1,
            Err(e) => {
                log::warn!("[KIMI-SYNC] 插入失败 ({}): {e}", request_id);
                skipped += 1;
            }
        }
    }

    // 更新同步状态
    update_sync_state(db, &file_path_str, file_modified, line_offset)?;

    Ok((imported, skipped))
}

/// 插入单条 Kimi 会话记录到 proxy_request_logs
fn insert_kimi_session_entry(
    db: &Database,
    request_id: &str,
    tokens: &TurnTokens,
    model: &str,
    session_id: Option<&str>,
    created_at_ms: Option<i64>,
) -> Result<bool, AppError> {
    let conn = lock_conn!(db.conn);

    let created_at = created_at_ms.map(|ms| ms / 1000).unwrap_or_else(|| {
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0)
    });

    let input_tokens = tokens.total_input() as u32;
    let dedup_key = DedupKey {
        app_type: "kimicode",
        model,
        input_tokens,
        output_tokens: tokens.output as u32,
        cache_read_tokens: tokens.cache_read as u32,
        cache_creation_tokens: tokens.cache_creation as u32,
        created_at,
    };
    if should_skip_session_insert(&conn, request_id, &dedup_key)? {
        return Ok(false);
    }

    // 计算费用
    let usage = TokenUsage {
        input_tokens,
        output_tokens: tokens.output as u32,
        cache_read_tokens: tokens.cache_read as u32,
        cache_creation_tokens: tokens.cache_creation as u32,
        model: Some(model.to_string()),
        message_id: None,
    };

    let pricing = find_kimi_pricing(&conn, model);
    let multiplier = Decimal::from(1);
    let (input_cost, output_cost, cache_read_cost, cache_creation_cost, total_cost) = match pricing
    {
        Some(p) => {
            let cost = CostCalculator::calculate_for_app("kimicode", &usage, &p, multiplier);
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
                "_kimi_session",     // provider_id
                "kimicode",          // app_type
                model,
                model,               // request_model = model
                input_tokens,
                tokens.output,
                tokens.cache_read,
                tokens.cache_creation,
                input_cost,
                output_cost,
                cache_read_cost,
                cache_creation_cost,
                total_cost,
                0i64,                // latency_ms
                Option::<i64>::None, // first_token_ms
                200i64,              // status_code
                Option::<String>::None, // error_message
                session_id.map(|s| s.to_string()),
                Some("kimi_session"), // provider_type
                1i64,                // is_streaming
                "1.0",               // cost_multiplier
                created_at,
                "kimi_session",      // data_source
            ],
        )
        .map_err(|e| AppError::Database(format!("插入 Kimi 会话日志失败: {e}")))?;

    if inserted_rows > 0 {
        crate::usage_events::notify_log_recorded();
    }

    Ok(true)
}

/// 查找 Kimi 模型定价（带归一化）
fn find_kimi_pricing(conn: &rusqlite::Connection, model_id: &str) -> Option<ModelPricing> {
    find_model_pricing(conn, &normalize_kimi_model(model_id))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn write_wire(path: &Path, values: &[serde_json::Value]) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        let contents = values
            .iter()
            .map(serde_json::Value::to_string)
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        fs::write(path, contents).unwrap();
    }

    fn turn_record(input_other: u64, cache_read: u64, cache_creation: u64, output: u64) -> serde_json::Value {
        serde_json::json!({
            "type": "usage.record",
            "model": "kimi-code/k3",
            "usage": {
                "inputOther": input_other,
                "output": output,
                "inputCacheRead": cache_read,
                "inputCacheCreation": cache_creation
            },
            "usageScope": "turn",
            "time": 1784462210300i64
        })
    }

    #[test]
    fn test_parse_turn_usage_valid() {
        let record = turn_record(35376, 11264, 128, 234);
        let tokens = parse_turn_usage(&record).unwrap();
        assert_eq!(tokens.input_other, 35376);
        assert_eq!(tokens.cache_read, 11264);
        assert_eq!(tokens.cache_creation, 128);
        assert_eq!(tokens.output, 234);
        assert_eq!(tokens.total_input(), 35376 + 11264 + 128);
        assert!(!tokens.is_zero());
    }

    #[test]
    fn test_parse_turn_usage_skips_session_scope() {
        let mut record = turn_record(100, 50, 0, 10);
        record["usageScope"] = serde_json::json!("session");
        assert!(parse_turn_usage(&record).is_none());
    }

    #[test]
    fn test_parse_turn_usage_rejects_non_usage_record() {
        let record = serde_json::json!({
            "type": "context.append_message",
            "message": {"role": "user"}
        });
        assert!(parse_turn_usage(&record).is_none());
    }

    #[test]
    fn test_parse_turn_usage_missing_usage_object() {
        let record = serde_json::json!({
            "type": "usage.record",
            "usageScope": "turn",
            "time": 1784462210300i64
        });
        assert!(parse_turn_usage(&record).is_none());
    }

    #[test]
    fn test_parse_turn_usage_missing_fields_default_zero() {
        let record = serde_json::json!({
            "type": "usage.record",
            "usageScope": "turn",
            "usage": { "output": 42 }
        });
        let tokens = parse_turn_usage(&record).unwrap();
        assert_eq!(tokens.input_other, 0);
        assert_eq!(tokens.output, 42);
    }

    #[test]
    fn test_zero_turn_tokens() {
        assert!(TurnTokens::default().is_zero());
    }

    #[test]
    fn test_normalize_kimi_model() {
        assert_eq!(normalize_kimi_model("kimi-code/k3"), "k3");
        assert_eq!(
            normalize_kimi_model("kimi-code/kimi-for-coding"),
            "kimi-for-coding"
        );
        assert_eq!(normalize_kimi_model("LOCAL-CPA/Grok-4.5"), "grok-4.5");
        assert_eq!(normalize_kimi_model("kimi-for-coding"), "kimi-for-coding");
    }

    #[test]
    fn test_wire_file_identity() {
        let path = Path::new("/home/u/.kimi-code/sessions/wd_proj_ab12/session_xyz/agents/agent-0/wire.jsonl");
        let (session, agent) = wire_file_identity(path);
        assert_eq!(session.as_deref(), Some("session_xyz"));
        assert_eq!(agent.as_deref(), Some("agent-0"));
    }

    #[test]
    fn test_collect_kimi_wire_files_nonexistent() {
        let files = collect_kimi_wire_files(Path::new("/nonexistent/path"));
        assert!(files.is_empty());
    }

    #[test]
    fn test_collect_kimi_wire_files_finds_nested_agents() {
        let temp = tempdir().unwrap();
        let root = temp.path().join("sessions");
        write_wire(
            &root.join("wd_a/session_1/agents/main/wire.jsonl"),
            &[],
        );
        write_wire(
            &root.join("wd_a/session_1/agents/agent-0/wire.jsonl"),
            &[],
        );
        write_wire(
            &root.join("wd_b/session_2/agents/main/wire.jsonl"),
            &[],
        );
        // 非 wire.jsonl 文件不应被收集
        write_wire(&root.join("wd_b/session_2/state.json"), &[]);

        let mut files = collect_kimi_wire_files(&root);
        files.sort();
        assert_eq!(files.len(), 3);
        assert!(files.iter().all(|f| f.file_name().unwrap() == "wire.jsonl"));
    }

    #[test]
    fn test_sync_single_kimi_file_imports_turns_skips_session_scope() -> Result<(), AppError> {
        let db = Database::memory()?;
        let temp = tempdir().unwrap();
        let wire = temp
            .path()
            .join("wd_a/session_1/agents/main/wire.jsonl");
        write_wire(
            &wire,
            &[
                turn_record(1000, 500, 0, 100),
                serde_json::json!({
                    "type": "usage.record",
                    "model": "kimi-code/k3",
                    "usage": {"inputOther": 1500, "output": 200, "inputCacheRead": 600, "inputCacheCreation": 0},
                    "usageScope": "session",
                    "time": 1784462215000i64
                }),
                turn_record(0, 0, 0, 0), // 零增量事件应跳过
                turn_record(2000, 800, 64, 200),
            ],
        );

        assert_eq!(sync_single_kimi_file(&db, &wire)?, (2, 0));

        let conn = lock_conn!(db.conn);
        let usage: (i64, i64, i64, i64) = conn.query_row(
            "SELECT input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
             FROM proxy_request_logs
             WHERE request_id = 'kimi_session:turn-v1:session_1:main:2'",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )?;
        // input_tokens 采用 OpenAI 风格语义（含缓存）
        assert_eq!(usage, (2000 + 800 + 64, 200, 800, 64));

        let meta: (String, String, i64, String) = conn.query_row(
            "SELECT app_type, model, created_at, data_source
             FROM proxy_request_logs
             WHERE request_id = 'kimi_session:turn-v1:session_1:main:1'",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )?;
        assert_eq!(meta.0, "kimicode");
        assert_eq!(meta.1, "k3");
        assert_eq!(meta.2, 1784462210300i64 / 1000);
        assert_eq!(meta.3, "kimi_session");

        Ok(())
    }

    #[test]
    fn test_sync_single_kimi_file_is_incremental() -> Result<(), AppError> {
        let db = Database::memory()?;
        let temp = tempdir().unwrap();
        let wire = temp
            .path()
            .join("wd_a/session_1/agents/main/wire.jsonl");
        write_wire(&wire, &[turn_record(100, 50, 0, 10)]);

        assert_eq!(sync_single_kimi_file(&db, &wire)?, (1, 0));
        // 第二次同步：文件未变化
        assert_eq!(sync_single_kimi_file(&db, &wire)?, (0, 0));

        // 追加一条新记录后应只导入新增部分
        let mut contents = fs::read_to_string(&wire).unwrap();
        contents.push_str(&turn_record(200, 100, 0, 20).to_string());
        contents.push('\n');
        fs::write(&wire, contents).unwrap();

        assert_eq!(sync_single_kimi_file(&db, &wire)?, (1, 0));

        let conn = lock_conn!(db.conn);
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM proxy_request_logs WHERE data_source = 'kimi_session'",
            [],
            |row| row.get(0),
        )?;
        assert_eq!(count, 2);

        Ok(())
    }

    #[test]
    fn test_agents_under_same_session_use_distinct_request_ids() -> Result<(), AppError> {
        let db = Database::memory()?;
        let temp = tempdir().unwrap();
        let main_wire = temp
            .path()
            .join("wd_a/session_1/agents/main/wire.jsonl");
        let sub_wire = temp
            .path()
            .join("wd_a/session_1/agents/agent-0/wire.jsonl");
        write_wire(&main_wire, &[turn_record(100, 50, 0, 10)]);
        write_wire(&sub_wire, &[turn_record(200, 100, 0, 20)]);

        assert_eq!(sync_single_kimi_file(&db, &main_wire)?, (1, 0));
        assert_eq!(sync_single_kimi_file(&db, &sub_wire)?, (1, 0));

        let conn = lock_conn!(db.conn);
        let request_ids = conn
            .prepare(
                "SELECT request_id FROM proxy_request_logs
                 WHERE data_source = 'kimi_session' ORDER BY request_id",
            )?
            .query_map([], |row| row.get::<_, String>(0))?
            .collect::<Result<Vec<_>, _>>()?;
        assert_eq!(
            request_ids,
            vec![
                "kimi_session:turn-v1:session_1:agent-0:1",
                "kimi_session:turn-v1:session_1:main:1"
            ]
        );

        Ok(())
    }

    #[test]
    fn test_insert_kimi_session_skips_duplicate_request_id() -> Result<(), AppError> {
        let db = Database::memory()?;
        let tokens = TurnTokens {
            input_other: 100,
            cache_read: 50,
            cache_creation: 0,
            output: 10,
        };
        let inserted = insert_kimi_session_entry(
            &db,
            "kimi_session:turn-v1:session_1:main:1",
            &tokens,
            "k3",
            Some("session_1"),
            Some(1784462210300),
        )?;
        assert!(inserted);
        let inserted = insert_kimi_session_entry(
            &db,
            "kimi_session:turn-v1:session_1:main:1",
            &tokens,
            "k3",
            Some("session_1"),
            Some(1784462210300),
        )?;
        assert!(!inserted);

        Ok(())
    }
}
