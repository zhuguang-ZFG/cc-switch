//! Kimi Code 会话日志使用追踪
//!
//! 从 ~/.kimi-code/sessions/<wd>/<session>/agents/<agent>/wire.jsonl 提取
//! `usage.record` 事件中的精确 token 用量（含主代理 main 与子代理 agent-N）。
//!
//! ## 数据流
//! ```text
//! wire.jsonl → usage.record(turn/session 两种 scope) 增量解析 → 费用计算 → proxy_request_logs 表
//! ```
//!
//! ## 解析的事件类型
//! - `usage.record` (usageScope="turn") → 单次 LLM 调用的增量 token 用量
//! - `usage.record` (usageScope="session" 或缺省) → full compaction 等非 turn
//!   调用的真实增量。官方每次 LLM 调用只写一条 usage.record，消费方
//!   （vis-server context-projector.ts）两种 scope 都计入总量，不存在重复计数；
//!   缺 usageScope 字段按官方兜底 `?? 'session'` 视为 session。
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
const KIMI_SESSION_REQUEST_ID_PREFIX: &str = "kimi_session:op-v1";

/// usageScope：官方仅 'turn' | 'session' 两种，缺字段按官方兜底视为 session
/// （vis-server context-projector.ts `rec.usageScope ?? 'session'`）
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum UsageScope {
    Turn,
    Session,
}

/// 单条 usage.record 的 token 增量（turn 或 session scope）
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct UsageTokens {
    input_other: u64,
    cache_read: u64,
    cache_creation: u64,
    output: u64,
}

impl UsageTokens {
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

/// 从 usage.record 事件中提取 token 增量（turn 与 session 两种 scope 都计入，
/// 与官方消费方口径一致；缺 usageScope 字段按官方兜底视为 session）
fn parse_usage_record(record: &serde_json::Value) -> Option<(UsageScope, UsageTokens)> {
    if record.get("type").and_then(|v| v.as_str()) != Some("usage.record") {
        return None;
    }
    let scope = match record.get("usageScope").and_then(|v| v.as_str()) {
        Some("turn") => UsageScope::Turn,
        // 含缺字段：官方兜底 ?? 'session'
        _ => UsageScope::Session,
    };
    let usage = record.get("usage")?;
    if !usage.is_object() {
        return None;
    }
    let tokens = UsageTokens {
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
    };
    Some((scope, tokens))
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
/// 返回 (session_id, agent_name)。仅接受官方 v2 布局（父目录链上必须有
/// `agents`），否则将 bucket 哈希误当 session_id。
fn wire_file_identity(file_path: &Path) -> (Option<String>, Option<String>) {
    let agents_marker = file_path
        .parent()
        .and_then(Path::parent)
        .and_then(Path::file_name)
        .and_then(|name| name.to_str());
    if agents_marker != Some("agents") {
        return (None, None);
    }
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

/// 判断文件是否以换行结尾。无 `\n` 结尾说明最后一行可能尚未写完
///（写入方还在追加），该行的 offset 不能落库，否则补全后会被永久跳过。
/// 读取失败时按"未完结"处理：下轮同步重读一行即可自愈，没有数据丢失风险。
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

/// 同步单个 Kimi wire.jsonl 文件，返回 (imported, skipped)
///
/// 增量语义：
/// - 落库的 offset 不越过无法解析的部分尾行（无 `\n` 结尾），待其补全后
///   下轮同步重新处理；
/// - wire.jsonl 是 append-only：当前行数小于已落库 offset 说明文件被
///   截断/重写，此时重置 offset 从头同步，避免截断期间数据永久丢失
///   （重放行号与 request_id 稳定方案一致，已有条目靠去重跳过）。
fn sync_single_kimi_file(db: &Database, file_path: &Path) -> Result<(u32, u32), AppError> {
    let file_path_str = file_path.to_string_lossy().to_string();

    // 获取文件元数据
    let metadata = fs::metadata(file_path)
        .map_err(|e| AppError::Config(format!("无法读取文件元数据: {e}")))?;
    let file_modified = metadata_modified_nanos(&metadata);
    let file_len = metadata.len();

    // 检查同步状态
    let (last_modified, mut last_offset) = get_sync_state(db, &file_path_str)?;

    // 文件未变化则跳过
    if file_modified <= last_modified {
        return Ok((0, 0));
    }

    let (session_id, agent_name) = wire_file_identity(file_path);
    // Non-v2 layout (e.g. a hypothetical top-level <session>/wire.jsonl):
    // importing it would mislabel the bucket hash as session_id and collide
    // request_id dedup keys across files. Skip it entirely.
    if session_id.is_none() {
        log::debug!(
            "[KIMI-SYNC] Skipping non-standard wire.jsonl layout: {}",
            file_path.display()
        );
        return Ok((0, 0));
    }

    let file =
        fs::File::open(file_path).map_err(|e| AppError::Config(format!("无法打开文件: {e}")))?;

    // 第一遍只数行，用于截断检测（不解析 JSON，代价可忽略）。
    // 数行结束后共享句柄自然停在 EOF，正好供尾字节检查 seek 使用。
    let total_lines = BufReader::new(&file).lines().count() as i64;
    let partial_tail = !file_ends_with_newline(&file, file_len);
    if total_lines < last_offset {
        log::info!(
            "[KIMI-SYNC] 检测到 {} 被截断或重写（{total_lines} 行 < 已同步 offset {last_offset}），从头重新同步",
            file_path.display()
        );
        last_offset = 0;
    }

    // 第一遍通过 &File 读取会推进共享句柄偏移，第二遍重开以获得 offset-0 流。
    let file =
        fs::File::open(file_path).map_err(|e| AppError::Config(format!("无法打开文件: {e}")))?;
    let reader = BufReader::new(file);

    let mut line_offset: i64 = 0;
    let mut turn_index: u32 = 0;
    let mut session_index: u32 = 0;
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

        let (scope, tokens) = match parse_usage_record(&value) {
            Some(v) => v,
            None => continue,
        };

        if tokens.is_zero() {
            continue;
        }

        // 所有非零事件都按各自 scope 占据稳定序号，保证增量同步时 request_id 不变；
        // 两种 scope 各自独立编号 + 不同前缀，同文件混排也不会撞 request_id
        let (prefix, event_index) = match scope {
            UsageScope::Turn => {
                turn_index += 1;
                (KIMI_TURN_REQUEST_ID_PREFIX, turn_index)
            }
            UsageScope::Session => {
                session_index += 1;
                (KIMI_SESSION_REQUEST_ID_PREFIX, session_index)
            }
        };

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
        let request_id = format!("{prefix}:{session_label}:{agent_label}:{event_index}");

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

    // 更新同步状态：部分尾行不计入已同步 offset，待补全后下轮重读
    let committed_offset = if partial_tail {
        (line_offset - 1).max(0)
    } else {
        line_offset
    };
    update_sync_state(db, &file_path_str, file_modified, committed_offset)?;

    Ok((imported, skipped))
}

/// 插入单条 Kimi 会话记录到 proxy_request_logs
fn insert_kimi_session_entry(
    db: &Database,
    request_id: &str,
    tokens: &UsageTokens,
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

    Ok(inserted_rows > 0)
}

/// 查找 Kimi 模型定价（带归一化）
///
/// Order: exact/prefix match on the normalized wire id, then Coding Plan /
/// K2.7-family aliases used by Kimi Code CLI 0.27 (`kimi-for-coding`, `k3`).
/// Only Moonshot-family ids fall back to `kimi-k2.7-code` open-platform list
/// rates so third-party models routed through Kimi (e.g. `claude-opus-4-8`)
/// still resolve via the generic pricing table instead of being mispriced.
fn find_kimi_pricing(conn: &rusqlite::Connection, model_id: &str) -> Option<ModelPricing> {
    let normalized = normalize_kimi_model(model_id);
    if let Some(pricing) = find_model_pricing(conn, &normalized) {
        return Some(pricing);
    }

    if !is_moonshot_family_model(&normalized) {
        return None;
    }

    // Managed catalog aliases that historically lacked dedicated seed rows.
    const KIMI_FAMILY_FALLBACKS: &[&str] = &[
        "kimi-for-coding",
        "kimi-for-coding-highspeed",
        "k3",
        "kimi-k2.7-code",
        "kimi-k2.6",
    ];
    for candidate in KIMI_FAMILY_FALLBACKS {
        if normalized == *candidate {
            continue;
        }
        if let Some(pricing) = find_model_pricing(conn, candidate) {
            return Some(pricing);
        }
    }
    None
}

fn is_moonshot_family_model(normalized: &str) -> bool {
    // `kimi-for-coding*` is covered by `starts_with("kimi")`.
    normalized == "k3" || normalized.starts_with("kimi") || normalized.starts_with("moonshot")
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

    fn turn_record(
        input_other: u64,
        cache_read: u64,
        cache_creation: u64,
        output: u64,
    ) -> serde_json::Value {
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

    fn session_record(
        input_other: u64,
        cache_read: u64,
        cache_creation: u64,
        output: u64,
    ) -> serde_json::Value {
        serde_json::json!({
            "type": "usage.record",
            "model": "kimi-code/k3",
            "usage": {
                "inputOther": input_other,
                "output": output,
                "inputCacheRead": cache_read,
                "inputCacheCreation": cache_creation
            },
            "usageScope": "session",
            "time": 1784462215000i64
        })
    }

    #[test]
    fn test_parse_usage_record_turn_scope() {
        let record = turn_record(35376, 11264, 128, 234);
        let (scope, tokens) = parse_usage_record(&record).unwrap();
        assert_eq!(scope, UsageScope::Turn);
        assert_eq!(tokens.input_other, 35376);
        assert_eq!(tokens.cache_read, 11264);
        assert_eq!(tokens.cache_creation, 128);
        assert_eq!(tokens.output, 234);
        assert_eq!(tokens.total_input(), 35376 + 11264 + 128);
        assert!(!tokens.is_zero());
    }

    #[test]
    fn test_parse_usage_record_session_scope() {
        let record = session_record(100, 50, 0, 10);
        let (scope, tokens) = parse_usage_record(&record).unwrap();
        assert_eq!(scope, UsageScope::Session);
        assert_eq!(tokens.input_other, 100);
        assert_eq!(tokens.output, 10);
    }

    /// 缺 usageScope 字段的记录按官方兜底 `?? 'session'` 视为 session
    /// （vis-server context-projector.ts）
    #[test]
    fn test_parse_usage_record_missing_scope_defaults_to_session() {
        let mut record = session_record(1181, 0, 0, 8);
        record.as_object_mut().unwrap().remove("usageScope");
        let (scope, tokens) = parse_usage_record(&record).unwrap();
        assert_eq!(scope, UsageScope::Session);
        assert_eq!(tokens.input_other, 1181);
    }

    #[test]
    fn test_parse_usage_record_rejects_non_usage_record() {
        let record = serde_json::json!({
            "type": "context.append_message",
            "message": {"role": "user"}
        });
        assert!(parse_usage_record(&record).is_none());
    }

    #[test]
    fn test_parse_usage_record_missing_usage_object() {
        let record = serde_json::json!({
            "type": "usage.record",
            "usageScope": "turn",
            "time": 1784462210300i64
        });
        assert!(parse_usage_record(&record).is_none());
    }

    #[test]
    fn test_parse_usage_record_missing_fields_default_zero() {
        let record = serde_json::json!({
            "type": "usage.record",
            "usageScope": "turn",
            "usage": { "output": 42 }
        });
        let (scope, tokens) = parse_usage_record(&record).unwrap();
        assert_eq!(scope, UsageScope::Turn);
        assert_eq!(tokens.input_other, 0);
        assert_eq!(tokens.output, 42);
    }

    #[test]
    fn test_zero_usage_tokens() {
        assert!(UsageTokens::default().is_zero());
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
    fn test_moonshot_family_detection() {
        assert!(is_moonshot_family_model("k3"));
        assert!(is_moonshot_family_model("kimi-for-coding"));
        assert!(is_moonshot_family_model("kimi-k2.7-code"));
        assert!(!is_moonshot_family_model("claude-opus-4-8"));
        assert!(!is_moonshot_family_model("gpt-5.6-sol"));
    }

    #[test]
    fn test_wire_file_identity() {
        let path = Path::new(
            "/home/u/.kimi-code/sessions/wd_proj_ab12/session_xyz/agents/agent-0/wire.jsonl",
        );
        let (session, agent) = wire_file_identity(path);
        assert_eq!(session.as_deref(), Some("session_xyz"));
        assert_eq!(agent.as_deref(), Some("agent-0"));
    }

    #[test]
    fn test_wire_file_identity_rejects_non_agents_layout() {
        // A top-level <bucket>/<session>/wire.jsonl must not be mislabeled
        // with the bucket hash as session_id.
        let path = Path::new("/home/u/.kimi-code/sessions/wd_proj_ab12/session_xyz/wire.jsonl");
        let (session, agent) = wire_file_identity(path);
        assert_eq!(session, None);
        assert_eq!(agent, None);
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
        write_wire(&root.join("wd_a/session_1/agents/main/wire.jsonl"), &[]);
        write_wire(&root.join("wd_a/session_1/agents/agent-0/wire.jsonl"), &[]);
        write_wire(&root.join("wd_b/session_2/agents/main/wire.jsonl"), &[]);
        // 非 wire.jsonl 文件不应被收集
        write_wire(&root.join("wd_b/session_2/state.json"), &[]);

        let mut files = collect_kimi_wire_files(&root);
        files.sort();
        assert_eq!(files.len(), 3);
        assert!(files.iter().all(|f| f.file_name().unwrap() == "wire.jsonl"));
    }

    /// turn 与 session 两种 scope 的记录都导入（与官方消费方口径一致，
    /// session 是 full compaction 等非 turn 调用的真实增量，非汇总重复）
    #[test]
    fn test_sync_single_kimi_file_imports_turn_and_session_scopes() -> Result<(), AppError> {
        let db = Database::memory()?;
        let temp = tempdir().unwrap();
        let wire = temp.path().join("wd_a/session_1/agents/main/wire.jsonl");
        write_wire(
            &wire,
            &[
                turn_record(1000, 500, 0, 100),
                session_record(1500, 600, 0, 200),
                turn_record(0, 0, 0, 0), // 零增量事件应跳过
                turn_record(2000, 800, 64, 200),
            ],
        );

        assert_eq!(sync_single_kimi_file(&db, &wire)?, (3, 0));

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

        // session-scope 记录走独立的 op-v1 序列，序号从 1 起
        let session_usage: (i64, i64, i64) = conn.query_row(
            "SELECT input_tokens, output_tokens, created_at
             FROM proxy_request_logs
             WHERE request_id = 'kimi_session:op-v1:session_1:main:1'",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )?;
        assert_eq!(session_usage, (1500 + 600, 200, 1784462215000i64 / 1000));

        Ok(())
    }

    /// 缺 usageScope 字段的记录按官方兜底 `?? 'session'` 导入 op-v1 序列
    #[test]
    fn test_sync_single_kimi_file_missing_scope_imported_as_session() -> Result<(), AppError> {
        let db = Database::memory()?;
        let temp = tempdir().unwrap();
        let wire = temp.path().join("wd_a/session_1/agents/main/wire.jsonl");
        let mut scopeless = session_record(1181, 0, 0, 8);
        scopeless.as_object_mut().unwrap().remove("usageScope");
        write_wire(&wire, &[scopeless]);

        assert_eq!(sync_single_kimi_file(&db, &wire)?, (1, 0));

        let conn = lock_conn!(db.conn);
        let input: i64 = conn.query_row(
            "SELECT input_tokens FROM proxy_request_logs
             WHERE request_id = 'kimi_session:op-v1:session_1:main:1'",
            [],
            |row| row.get(0),
        )?;
        assert_eq!(input, 1181);

        Ok(())
    }

    /// turn/session 同文件混排：两种 scope 各自独立编号、不同前缀，
    /// request_id 互不冲突且各自序号稳定（增量重放幂等）
    #[test]
    fn test_sync_single_kimi_file_mixed_scopes_have_stable_distinct_ids() -> Result<(), AppError> {
        let db = Database::memory()?;
        let temp = tempdir().unwrap();
        let wire = temp.path().join("wd_a/session_1/agents/main/wire.jsonl");
        write_wire(
            &wire,
            &[
                turn_record(100, 0, 0, 10),
                session_record(1000, 0, 0, 50),
                turn_record(200, 0, 0, 20),
                session_record(2000, 0, 0, 60),
            ],
        );

        assert_eq!(sync_single_kimi_file(&db, &wire)?, (4, 0));

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
                "kimi_session:op-v1:session_1:main:1",
                "kimi_session:op-v1:session_1:main:2",
                "kimi_session:turn-v1:session_1:main:1",
                "kimi_session:turn-v1:session_1:main:2",
            ]
        );
        drop(conn);

        // 第二轮空转：序号稳定，重复同步不产生重复/冲突
        assert_eq!(sync_single_kimi_file(&db, &wire)?, (0, 0));

        Ok(())
    }

    #[test]
    fn test_sync_single_kimi_file_is_incremental() -> Result<(), AppError> {
        let db = Database::memory()?;
        let temp = tempdir().unwrap();
        let wire = temp.path().join("wd_a/session_1/agents/main/wire.jsonl");
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
        let main_wire = temp.path().join("wd_a/session_1/agents/main/wire.jsonl");
        let sub_wire = temp.path().join("wd_a/session_1/agents/agent-0/wire.jsonl");
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
        let tokens = UsageTokens {
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

    fn write_wire_raw(path: &Path, contents: &str) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, contents).unwrap();
    }

    fn sync_state_key(path: &Path) -> String {
        path.to_string_lossy().to_string()
    }

    /// 无 `\n` 结尾的部分尾行不计入落库 offset：写入方补全该行后，
    /// 下轮同步必须能读到它（修复前该行会被永久跳过）。
    #[test]
    fn test_sync_single_kimi_file_does_not_commit_partial_tail_line() -> Result<(), AppError> {
        let db = Database::memory()?;
        let temp = tempdir().unwrap();
        let wire = temp.path().join("wd_a/session_1/agents/main/wire.jsonl");
        let key = sync_state_key(&wire);

        let first = turn_record(100, 50, 0, 10).to_string();
        let second = turn_record(200, 100, 0, 20).to_string();
        // 第二行只写一半且无换行结尾：模拟写入进行中的部分尾行
        let partial = &second[..second.len() / 2];
        write_wire_raw(&wire, &format!("{first}\n{partial}"));

        assert_eq!(sync_single_kimi_file(&db, &wire)?, (1, 0));
        let (_, offset) = get_sync_state(&db, &key)?;
        assert_eq!(offset, 1, "部分尾行不得计入已同步 offset");

        // 写入方补全第二行并追加第三行
        std::thread::sleep(std::time::Duration::from_millis(20));
        let third = turn_record(300, 150, 0, 30).to_string();
        write_wire_raw(&wire, &format!("{first}\n{second}\n{third}\n"));

        assert_eq!(sync_single_kimi_file(&db, &wire)?, (2, 0));

        let conn = lock_conn!(db.conn);
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM proxy_request_logs WHERE data_source = 'kimi_session'",
            [],
            |row| row.get(0),
        )?;
        assert_eq!(count, 3);

        Ok(())
    }

    /// 永久畸形行（含 usage.record 标记但 JSON 损坏）被容错跳过，
    /// 且因为它有 `\n` 结尾，offset 照常越过它，不会每轮重复扫描。
    #[test]
    fn test_sync_single_kimi_file_tolerates_malformed_lines() -> Result<(), AppError> {
        let db = Database::memory()?;
        let temp = tempdir().unwrap();
        let wire = temp.path().join("wd_a/session_1/agents/main/wire.jsonl");
        let key = sync_state_key(&wire);

        let good1 = turn_record(100, 50, 0, 10).to_string();
        let good2 = turn_record(200, 100, 0, 20).to_string();
        let malformed = "{\"type\":\"usage.record\",\"usageScope\":\"turn\",BROKEN";
        write_wire_raw(&wire, &format!("{good1}\n{malformed}\n{good2}\n"));

        assert_eq!(sync_single_kimi_file(&db, &wire)?, (2, 0));
        let (_, offset) = get_sync_state(&db, &key)?;
        assert_eq!(offset, 3, "完整结尾的畸形行不应卡住 offset");
        // 第二轮空转，证明畸形行没有被反复重扫
        assert_eq!(sync_single_kimi_file(&db, &wire)?, (0, 0));

        Ok(())
    }

    /// 文件被截断/重写（行数缩到已落库 offset 以下）后重置从头同步：
    /// 已有条目靠 request_id 去重跳过，后续增长到全新序号的事件正常导入。
    #[test]
    fn test_sync_single_kimi_file_resets_offset_after_truncation_rewrite() -> Result<(), AppError> {
        let db = Database::memory()?;
        let temp = tempdir().unwrap();
        let wire = temp.path().join("wd_a/session_1/agents/main/wire.jsonl");
        let key = sync_state_key(&wire);

        let first = turn_record(100, 50, 0, 10);
        write_wire(
            &wire,
            &[
                first.clone(),
                turn_record(200, 100, 0, 20),
                turn_record(300, 150, 0, 30),
            ],
        );
        assert_eq!(sync_single_kimi_file(&db, &wire)?, (3, 0));
        let (_, offset) = get_sync_state(&db, &key)?;
        assert_eq!(offset, 3);

        // 截断重写：只剩 1 行（< offset 3）→ 重置，旧条目去重跳过
        std::thread::sleep(std::time::Duration::from_millis(20));
        write_wire(&wire, &[first.clone()]);
        assert_eq!(sync_single_kimi_file(&db, &wire)?, (0, 1));
        let (_, offset) = get_sync_state(&db, &key)?;
        assert_eq!(offset, 1, "截断后 offset 必须按新文件长度重建");

        // 继续增长到 5 个事件：行 1 被 offset 跳过（不计入 skipped），
        // 序号 2/3 与截断前条目撞 request_id 被去重跳过，
        // 序号 4/5 是全新事件，必须导入（未重置时它们会被旧 offset 挡住）
        std::thread::sleep(std::time::Duration::from_millis(20));
        write_wire(
            &wire,
            &[
                first,
                turn_record(200, 100, 0, 20),
                turn_record(300, 150, 0, 30),
                turn_record(400, 200, 0, 40),
                turn_record(500, 250, 0, 50),
            ],
        );
        assert_eq!(sync_single_kimi_file(&db, &wire)?, (2, 2));

        let conn = lock_conn!(db.conn);
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM proxy_request_logs WHERE data_source = 'kimi_session'",
            [],
            |row| row.get(0),
        )?;
        assert_eq!(count, 5);

        Ok(())
    }
}
