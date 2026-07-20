//! OpenAI Chat Completions ↔ Anthropic Messages transforms for Reasonix proxy.

use crate::proxy::{error::ProxyError, json_canonical::canonical_json_string};
use serde_json::{json, Value};

use super::transform::clean_schema;
use super::transform_codex_anthropic::{
    drop_empty_messages, drop_incomplete_tool_turns, ensure_leading_user_message,
    trim_trailing_assistant_text,
};

fn is_meaningful_text(text: &str) -> bool {
    !text.trim().is_empty()
}

fn push_block(messages: &mut Vec<Value>, role: &str, block: Value) {
    if let Some(last) = messages.last_mut() {
        if last.get("role").and_then(|r| r.as_str()) == Some(role) {
            if let Some(arr) = last.get_mut("content").and_then(|c| c.as_array_mut()) {
                arr.push(block);
                return;
            }
        }
    }
    messages.push(json!({
        "role": role,
        "content": [block]
    }));
}

fn push_tool_result_block(messages: &mut Vec<Value>, block: Value) {
    if let Some(last) = messages.last_mut() {
        if last.get("role").and_then(Value::as_str) == Some("user") {
            if let Some(content) = last.get_mut("content").and_then(Value::as_array_mut) {
                let insert_at = content
                    .iter()
                    .position(|item| item.get("type").and_then(Value::as_str) != Some("tool_result"))
                    .unwrap_or(content.len());
                content.insert(insert_at, block);
                return;
            }
        }
    }
    messages.push(json!({
        "role": "user",
        "content": [block]
    }));
}

fn image_block_from_openai_image_url(part: &Value) -> Option<Value> {
    let url = part
        .get("image_url")
        .and_then(|v| v.as_str().map(str::to_string).or_else(|| {
            v.get("url")
                .and_then(|u| u.as_str())
                .map(str::to_string)
        }))?;

    if let Some(rest) = url.strip_prefix("data:") {
        let (meta, data) = rest.split_once(',')?;
        let media_type = meta.split(';').next().unwrap_or("image/png");
        Some(json!({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": data
            }
        }))
    } else if url.starts_with("http://") || url.starts_with("https://") {
        Some(json!({
            "type": "image",
            "source": { "type": "url", "url": url }
        }))
    } else {
        None
    }
}

fn openai_content_part_to_anthropic_block(part: &Value) -> Option<Value> {
    let part_type = part.get("type").and_then(|t| t.as_str()).unwrap_or("");
    match part_type {
        "text" => part
            .get("text")
            .and_then(|t| t.as_str())
            .filter(|t| is_meaningful_text(t))
            .map(|text| json!({ "type": "text", "text": text })),
        "image_url" => image_block_from_openai_image_url(part),
        _ => None,
    }
}

fn openai_message_content_to_blocks(content: &Value) -> Vec<Value> {
    if let Some(text) = content.as_str() {
        if is_meaningful_text(text) {
            return vec![json!({ "type": "text", "text": text })];
        }
        return Vec::new();
    }
    if let Some(parts) = content.as_array() {
        return parts
            .iter()
            .filter_map(openai_content_part_to_anthropic_block)
            .collect();
    }
    Vec::new()
}

fn chat_tool_to_anthropic_tool(chat_tool: &Value) -> Option<Value> {
    let function = chat_tool.get("function")?;
    let name = function
        .get("name")
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())?;
    let mut tool = json!({
        "name": name,
        "input_schema": function
            .get("parameters")
            .cloned()
            .filter(|value| value.as_object().is_some_and(|object| !object.is_empty()))
            .map(clean_schema)
            .unwrap_or_else(|| json!({ "type": "object", "properties": {} }))
    });
    if let Some(description) = function.get("description").and_then(|value| value.as_str()) {
        tool["description"] = json!(description);
    }
    Some(tool)
}

fn map_chat_tool_choice_to_anthropic(tool_choice: &Value) -> Value {
    match tool_choice {
        Value::String(s) => match s.as_str() {
            "required" => json!({ "type": "any" }),
            "auto" => json!({ "type": "auto" }),
            "none" => json!({ "type": "none" }),
            _ => json!({ "type": "auto" }),
        },
        Value::Object(obj) => match obj.get("type").and_then(|t| t.as_str()) {
            Some("function") => {
                let name = obj
                    .get("function")
                    .and_then(|f| f.get("name"))
                    .or_else(|| obj.get("name"))
                    .and_then(|n| n.as_str())
                    .unwrap_or("");
                json!({ "type": "tool", "name": name })
            }
            _ => json!({ "type": "auto" }),
        },
        _ => json!({ "type": "auto" }),
    }
}

fn convert_openai_messages(messages: &[Value]) -> Result<Vec<Value>, ProxyError> {
    let mut anthropic_messages = Vec::new();

    for message in messages {
        let role = message
            .get("role")
            .and_then(|r| r.as_str())
            .unwrap_or("user");

        match role {
            "system" => continue,
            "tool" => {
                let tool_call_id = message
                    .get("tool_call_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let content = message.get("content");
                let content_value = match content {
                    Some(Value::String(text)) => json!(text),
                    Some(value) => value.clone(),
                    None => json!(""),
                };
                let mut block = json!({
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content_value
                });
                if message.get("is_error").and_then(Value::as_bool) == Some(true) {
                    block["is_error"] = json!(true);
                }
                push_tool_result_block(&mut anthropic_messages, block);
            }
            "assistant" => {
                if let Some(content) = message.get("content") {
                    for block in openai_message_content_to_blocks(content) {
                        push_block(&mut anthropic_messages, "assistant", block);
                    }
                }
                if let Some(tool_calls) = message.get("tool_calls").and_then(|t| t.as_array()) {
                    for tc in tool_calls {
                        let id = tc.get("id").and_then(|v| v.as_str()).unwrap_or("");
                        let empty_obj = json!({});
                        let func = tc.get("function").unwrap_or(&empty_obj);
                        let name = func.get("name").and_then(|n| n.as_str()).unwrap_or("");
                        let args_str = func
                            .get("arguments")
                            .and_then(|a| a.as_str())
                            .unwrap_or("{}");
                        let input: Value = serde_json::from_str(args_str).unwrap_or(json!({}));
                        push_block(
                            &mut anthropic_messages,
                            "assistant",
                            json!({
                                "type": "tool_use",
                                "id": id,
                                "name": name,
                                "input": input
                            }),
                        );
                    }
                }
                if let Some(function_call) = message.get("function_call") {
                    let id = function_call
                        .get("id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    let name = function_call
                        .get("name")
                        .and_then(|n| n.as_str())
                        .unwrap_or("");
                    let input = match function_call.get("arguments") {
                        Some(Value::String(s)) => serde_json::from_str(s).unwrap_or(json!({})),
                        Some(v @ Value::Object(_)) | Some(v @ Value::Array(_)) => v.clone(),
                        _ => json!({}),
                    };
                    if !name.is_empty() {
                        push_block(
                            &mut anthropic_messages,
                            "assistant",
                            json!({
                                "type": "tool_use",
                                "id": id,
                                "name": name,
                                "input": input
                            }),
                        );
                    }
                }
            }
            _ => {
                if let Some(content) = message.get("content") {
                    for block in openai_message_content_to_blocks(content) {
                        push_block(&mut anthropic_messages, "user", block);
                    }
                }
            }
        }
    }

    Ok(anthropic_messages)
}

fn build_system_field(body: &Value) -> Option<Value> {
    let mut system_parts: Vec<Value> = Vec::new();

    if let Some(messages) = body.get("messages").and_then(|m| m.as_array()) {
        for message in messages {
            if message.get("role").and_then(|r| r.as_str()) != Some("system") {
                continue;
            }
            match message.get("content") {
                Some(Value::String(text)) if is_meaningful_text(text) => {
                    system_parts.push(json!({ "type": "text", "text": text.trim() }));
                }
                Some(Value::Array(parts)) => {
                    for part in parts {
                        if let Some(block) = openai_content_part_to_anthropic_block(part) {
                            system_parts.push(block);
                        }
                    }
                }
                _ => {}
            }
        }
    }

    if let Some(system) = body.get("system") {
        match system {
            Value::String(text) if is_meaningful_text(text) => {
                system_parts.push(json!({ "type": "text", "text": text.trim() }));
            }
            Value::Array(parts) => {
                for part in parts {
                    if let Some(text) = part.get("text").and_then(|t| t.as_str()) {
                        if is_meaningful_text(text) {
                            system_parts.push(json!({ "type": "text", "text": text.trim() }));
                        }
                    }
                }
            }
            _ => {}
        }
    }

    if system_parts.is_empty() {
        return None;
    }
    if system_parts.len() == 1 {
        if let Some(text) = system_parts[0].get("text").and_then(|t| t.as_str()) {
            return Some(json!(text));
        }
    }
    Some(Value::Array(system_parts))
}

/// OpenAI Chat Completions request → Anthropic Messages request.
pub fn openai_chat_request_to_anthropic(
    body: Value,
    default_max_tokens: u64,
) -> Result<Value, ProxyError> {
    let mut result = json!({});

    if let Some(model) = body.get("model") {
        result["model"] = model.clone();
    }

    if let Some(system) = build_system_field(&body) {
        result["system"] = system;
    }

    let mut messages = match body.get("messages").and_then(|m| m.as_array()) {
        Some(messages) => convert_openai_messages(messages)?,
        None => Vec::new(),
    };

    drop_incomplete_tool_turns(&mut messages);
    drop_empty_messages(&mut messages);
    ensure_leading_user_message(&mut messages);
    if messages.is_empty() {
        return Err(ProxyError::InvalidRequest(
            "cannot convert chat request: empty messages".to_string(),
        ));
    }
    trim_trailing_assistant_text(&mut messages);
    drop_empty_messages(&mut messages);
    if messages.is_empty() {
        return Err(ProxyError::InvalidRequest(
            "cannot convert chat request: empty messages".to_string(),
        ));
    }
    result["messages"] = json!(messages);

    let max_tokens = body
        .get("max_tokens")
        .or_else(|| body.get("max_completion_tokens"))
        .and_then(|v| v.as_u64())
        .filter(|v| *v > 0)
        .unwrap_or(default_max_tokens);
    result["max_tokens"] = json!(max_tokens);

    if let Some(v) = body.get("temperature") {
        result["temperature"] = v.clone();
    }
    if let Some(v) = body.get("top_p") {
        result["top_p"] = v.clone();
    }
    if let Some(v) = body.get("stream") {
        result["stream"] = v.clone();
    }
    if let Some(v) = body.get("stop") {
        result["stop_sequences"] = v.clone();
    }

    let anth_tools: Vec<Value> = body
        .get("tools")
        .and_then(|tools| tools.as_array())
        .into_iter()
        .flatten()
        .filter(|tool| tool.get("type").and_then(|t| t.as_str()) != Some("function")
            || tool.get("function").is_some())
        .filter_map(chat_tool_to_anthropic_tool)
        .collect();
    let has_tools = !anth_tools.is_empty();
    if has_tools {
        result["tools"] = json!(anth_tools);
        if let Some(tc) = body.get("tool_choice") {
            result["tool_choice"] = map_chat_tool_choice_to_anthropic(tc);
        }
    }

    Ok(result)
}

/// Anthropic Messages response → OpenAI Chat Completions response (non-streaming).
pub fn anthropic_message_response_to_openai_chat(body: Value) -> Result<Value, ProxyError> {
    if body.get("type").and_then(Value::as_str) == Some("error") || body.get("error").is_some() {
        let error = body.get("error").unwrap_or(&body);
        let message = error
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("Anthropic upstream returned an error");
        return Err(ProxyError::TransformError(message.to_string()));
    }

    let id = body
        .get("id")
        .and_then(|value| value.as_str())
        .unwrap_or("chatcmpl-reasonix");
    let model = body
        .get("model")
        .and_then(|value| value.as_str())
        .unwrap_or("");

    let mut text_parts = Vec::new();
    let mut tool_calls = Vec::new();
    let mut reasoning_content = String::new();

    if let Some(blocks) = body.get("content").and_then(|value| value.as_array()) {
        for block in blocks {
            match block.get("type").and_then(|value| value.as_str()).unwrap_or("") {
                "text" => {
                    if let Some(text) = block.get("text").and_then(|value| value.as_str()) {
                        text_parts.push(text.to_string());
                    }
                }
                "thinking" => {
                    if let Some(text) = block.get("thinking").and_then(|value| value.as_str()) {
                        reasoning_content.push_str(text);
                    }
                }
                "tool_use" => {
                    let call_id = block
                        .get("id")
                        .and_then(|value| value.as_str())
                        .unwrap_or("");
                    let name = block
                        .get("name")
                        .and_then(|value| value.as_str())
                        .unwrap_or("");
                    let input = block.get("input").cloned().unwrap_or(json!({}));
                    let arguments = match input {
                        Value::String(s) => s,
                        other => canonical_json_string(&other),
                    };
                    tool_calls.push(json!({
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": arguments,
                        }
                    }));
                }
                _ => {}
            }
        }
    }

    let stop_reason = body
        .get("stop_reason")
        .and_then(|value| value.as_str())
        .unwrap_or("end_turn");
    let finish_reason = match stop_reason {
        "tool_use" => "tool_calls",
        "max_tokens" => "length",
        _ => "stop",
    };

    let mut message = json!({
        "role": "assistant",
        "content": if text_parts.is_empty() {
            Value::Null
        } else {
            Value::String(text_parts.join(""))
        },
    });
    if !tool_calls.is_empty() {
        message["tool_calls"] = Value::Array(tool_calls);
    }
    if !reasoning_content.is_empty() {
        message["reasoning_content"] = Value::String(reasoning_content);
    }

    let usage = body.get("usage").map(|usage| {
        let input = usage
            .get("input_tokens")
            .and_then(|value| value.as_u64())
            .unwrap_or(0);
        let output = usage
            .get("output_tokens")
            .and_then(|value| value.as_u64())
            .unwrap_or(0);
        json!({
            "prompt_tokens": input,
            "completion_tokens": output,
            "total_tokens": input + output,
        })
    });

    let mut result = json!({
        "id": id,
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
    });
    if let Some(usage) = usage {
        result["usage"] = usage;
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn openai_chat_request_to_anthropic_system_and_user() {
        let input = json!({
            "model": "claude-sonnet-4-5",
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"}
            ]
        });

        let result = openai_chat_request_to_anthropic(input, 8192).unwrap();
        assert_eq!(result["system"], "You are helpful.");
        assert_eq!(result["messages"][0]["role"], "user");
        assert_eq!(result["messages"][0]["content"][0]["text"], "Hello");
        assert_eq!(result["max_tokens"], 1024);
    }

    #[test]
    fn openai_chat_request_to_anthropic_tool_round_trip_shape() {
        let input = json!({
            "model": "claude-sonnet-4-5",
            "messages": [
                {"role": "user", "content": "Weather?"},
                {
                    "role": "assistant",
                    "content": "Checking",
                    "tool_calls": [{
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": "{\"location\":\"Tokyo\"}"
                        }
                    }]
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_123",
                    "content": "Sunny"
                }
            ],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": { "location": { "type": "string" } }
                    }
                }
            }],
            "tool_choice": "auto"
        });

        let anthropic = openai_chat_request_to_anthropic(input, 8192).unwrap();
        assert_eq!(anthropic["messages"][1]["role"], "assistant");
        assert_eq!(
            anthropic["messages"][1]["content"][1]["type"],
            "tool_use"
        );
        assert_eq!(anthropic["messages"][1]["content"][1]["id"], "call_123");
        assert_eq!(anthropic["messages"][2]["content"][0]["type"], "tool_result");
        assert_eq!(
            anthropic["messages"][2]["content"][0]["tool_use_id"],
            "call_123"
        );
        assert_eq!(anthropic["tools"][0]["name"], "get_weather");
        assert_eq!(anthropic["tool_choice"]["type"], "auto");
    }

    #[test]
    fn anthropic_response_to_openai_chat_with_tool_use() {
        let anthropic = json!({
            "id": "msg_abc",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [
                {"type": "text", "text": "Let me check"},
                {
                    "type": "tool_use",
                    "id": "call_123",
                    "name": "get_weather",
                    "input": {"location": "Tokyo"}
                }
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5}
        });

        let chat = anthropic_message_response_to_openai_chat(anthropic).unwrap();
        assert_eq!(chat["choices"][0]["finish_reason"], "tool_calls");
        assert_eq!(chat["choices"][0]["message"]["content"], "Let me check");
        assert_eq!(chat["choices"][0]["message"]["tool_calls"][0]["id"], "call_123");
        assert_eq!(
            chat["choices"][0]["message"]["tool_calls"][0]["function"]["name"],
            "get_weather"
        );
        assert_eq!(chat["usage"]["prompt_tokens"], 10);
    }
}
