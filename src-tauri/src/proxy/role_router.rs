//! 角色路由模块
//!
//! 子代理 brief 末尾携带 `[[route:MODEL]]` 标记时，代理在转发前把请求模型
//! 改写为标记指定的模型（并剥掉标记文本）。这让 kimi-code 等不支持
//! 子代理独立模型的客户端，可以通过提示词标记实现角色级模型路由。
//!
//! 注意：改写只发生在代理层，上游是否有该模型取决于当前 provider
//! （配合 zg-newapi 聚合网关可覆盖全部标记模型）。

use serde_json::Value;

const MARKER_PREFIX: &str = "[[route:";
const MARKER_SUFFIX: &str = "]]";

fn is_allowed_route_model(model: &str) -> bool {
    if model.is_empty() || model.len() > 128 {
        return false;
    }
    let core = model
        .strip_suffix("[1M]")
        .or_else(|| model.strip_suffix("[1m]"))
        .unwrap_or(model);
    !core.is_empty()
        && core
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '/' | '-'))
}

/// 在文本中查找路由标记，返回 (模型名, 剥除标记后的文本)
fn extract_route_marker(text: &str) -> Option<(String, String)> {
    let start = text.find(MARKER_PREFIX)?;
    let rest = &text[start + MARKER_PREFIX.len()..];
    // `]]` 可能与模型名里的 `[1M]` 重叠；取第一个能通过白名单的候选。
    let mut search_from = 0;
    while let Some(rel) = rest[search_from..].find(MARKER_SUFFIX) {
        let end = search_from + rel;
        let model = rest[..end].trim();
        if is_allowed_route_model(model) {
            let mut cleaned = String::with_capacity(text.len());
            cleaned.push_str(&text[..start]);
            cleaned.push_str(&rest[end + MARKER_SUFFIX.len()..]);
            let cleaned = cleaned.replace("\n\n\n", "\n\n");
            return Some((model.to_string(), cleaned.trim_end().to_string()));
        }
        search_from = end + 1;
    }
    None
}

/// 在 messages 形态的数组（每项可选 "content" 为字符串或 parts 数组）中
/// 查找路由标记；找到则剥除并返回模型名。
fn scan_message_list(list: &mut [Value]) -> Option<String> {
    for message in list.iter_mut() {
        let Some(content) = message.get_mut("content") else {
            continue;
        };
        if let Some(text) = content.as_str() {
            if let Some((model, cleaned)) = extract_route_marker(text) {
                *content = Value::String(cleaned);
                return Some(model);
            }
        } else if let Some(parts) = content.as_array_mut() {
            let mut found: Option<(String, usize)> = None;
            for (idx, part) in parts.iter_mut().enumerate() {
                let Some(text) = part.get("text").and_then(|t| t.as_str()) else {
                    continue;
                };
                if let Some((model, cleaned)) = extract_route_marker(text) {
                    part["text"] = Value::String(cleaned);
                    found = Some((model, idx));
                    break;
                }
            }
            if let Some((model, idx)) = found {
                if parts[idx].get("text").and_then(|t| t.as_str()) == Some("") {
                    parts.remove(idx);
                }
                return Some(model);
            }
        }
    }
    None
}

/// 扫描请求体中的路由标记；找到则改写 body.model 并剥除标记。
/// 同时支持 chat completions（messages）和 Responses API（input）两种载荷。
/// 返回改写后的 body（未找到标记时原样返回）。
pub fn apply_role_route(mut body: Value) -> Value {
    // chat completions: messages 数组
    let mut routed = body
        .get_mut("messages")
        .and_then(|m| m.as_array_mut())
        .and_then(|list| scan_message_list(list));

    // Responses API: input 为字符串或消息项数组
    if routed.is_none() {
        routed = match body.get_mut("input") {
            Some(Value::String(text)) => match extract_route_marker(text) {
                Some((model, cleaned)) => {
                    *text = cleaned;
                    Some(model)
                }
                None => None,
            },
            Some(Value::Array(list)) => scan_message_list(list),
            _ => None,
        };
    }

    if let Some(model) = routed {
        log::info!("[role_router] request model rewritten by route marker -> {}", model);
        body["model"] = Value::String(model);
    }
    body
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rewrites_model_from_string_content() {
        let mut body = json!({
            "model": "default-model",
            "messages": [
                {"role": "user", "content": "You are a security reviewer.\n\n[[route:claude-opus-4-8]]"}
            ]
        });
        body = apply_role_route(body);
        assert_eq!(body["model"], "claude-opus-4-8");
        let content = body["messages"][0]["content"].as_str().unwrap();
        assert!(!content.contains("[[route:"));
        assert!(content.contains("security reviewer"));
    }

    #[test]
    fn rewrites_model_from_array_content() {
        let mut body = json!({
            "model": "default-model",
            "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "task body"},
                    {"type": "text", "text": "[[route:kimi-for-coding]]"}
                ]}
            ]
        });
        body = apply_role_route(body);
        assert_eq!(body["model"], "kimi-for-coding");
        let parts = body["messages"][0]["content"].as_array().unwrap();
        assert_eq!(parts.len(), 1);
        assert_eq!(parts[0]["text"], "task body");
    }

    #[test]
    fn leaves_body_untouched_without_marker() {
        let body = json!({
            "model": "default-model",
            "messages": [{"role": "user", "content": "hello [[route:]"}]
        });
        let out = apply_role_route(body.clone());
        assert_eq!(out, body);
    }

    #[test]
    fn rewrites_model_with_one_m_suffix() {
        let body = json!({
            "model": "default-model",
            "messages": [
                {"role": "user", "content": "review\n\n[[route:claude-opus-5[1M]]]"}
            ]
        });
        let out = apply_role_route(body);
        assert_eq!(out["model"], "claude-opus-5[1M]");
        let content = out["messages"][0]["content"].as_str().unwrap();
        assert!(!content.contains("[[route:"));
    }

    #[test]
    fn rejects_invalid_model_chars() {
        let body = json!({
            "model": "default-model",
            "messages": [{"role": "user", "content": "[[route:evil\"; DROP]]"}]
        });
        let out = apply_role_route(body.clone());
        assert_eq!(out["model"], "default-model");
    }

    #[test]
    fn no_messages_field_is_noop() {
        let body = json!({"model": "m", "input": "[[route:x]]"});
        let out = apply_role_route(body.clone());
        assert_eq!(out["model"], "x");
    }

    #[test]
    fn rewrites_model_from_responses_input_string() {
        let body = json!({
            "model": "cc-switch-proxy-default",
            "input": "say ok\n\n[[route:glm-5.2]]"
        });
        let out = apply_role_route(body);
        assert_eq!(out["model"], "glm-5.2");
        assert_eq!(out["input"], "say ok");
    }

    #[test]
    fn rewrites_model_from_responses_input_items() {
        let body = json!({
            "model": "cc-switch-proxy-default",
            "input": [
                {"role": "user", "content": [
                    {"type": "input_text", "text": "brief [[route:claude-opus-4-8]]"}
                ]}
            ]
        });
        let out = apply_role_route(body);
        assert_eq!(out["model"], "claude-opus-4-8");
        assert_eq!(out["input"][0]["content"][0]["text"], "brief");
    }
}
