//! Anthropic Messages SSE → OpenAI Chat Completions SSE conversion for Reasonix.

use crate::proxy::json_canonical::canonicalize_tool_arguments_str;
use crate::proxy::sse::{strip_sse_field, take_sse_block};
use bytes::Bytes;
use futures::stream::{Stream, StreamExt};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BlockKind {
    Text,
    Tool,
    Thinking,
}

#[derive(Debug)]
struct BlockState {
    kind: BlockKind,
    tool_index: usize,
    #[allow(dead_code)]
    call_id: String,
    #[allow(dead_code)]
    name: String,
    accum: String,
    start_input: String,
    done: bool,
}

struct AnthropicToOpenAiChatState {
    started: bool,
    completed: bool,
    response_id: String,
    model: String,
    created: i64,
    role_sent: bool,
    next_tool_index: usize,
    blocks: BTreeMap<u64, BlockState>,
    usage: Map<String, Value>,
    stop_reason: Option<String>,
}

impl Default for AnthropicToOpenAiChatState {
    fn default() -> Self {
        Self {
            started: false,
            completed: false,
            response_id: "chatcmpl-reasonix".to_string(),
            model: String::new(),
            created: chrono::Utc::now().timestamp(),
            role_sent: false,
            next_tool_index: 0,
            blocks: BTreeMap::new(),
            usage: Map::new(),
            stop_reason: None,
        }
    }
}

impl AnthropicToOpenAiChatState {
    fn map_stop_reason(reason: Option<&str>) -> Option<&'static str> {
        match reason {
            Some("tool_use") => Some("tool_calls"),
            Some("max_tokens") => Some("length"),
            Some("end_turn") | Some("stop_sequence") => Some("stop"),
            Some(_) => Some("stop"),
            None => None,
        }
    }

    fn openai_usage(&self) -> Option<Value> {
        if self.usage.is_empty() {
            return None;
        }
        let input = self
            .usage
            .get("input_tokens")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let output = self
            .usage
            .get("output_tokens")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        Some(json!({
            "prompt_tokens": input,
            "completion_tokens": output,
            "total_tokens": input + output,
        }))
    }

    fn emit_chunk(
        &mut self,
        delta: Value,
        finish_reason: Option<&str>,
        include_usage: bool,
    ) -> Bytes {
        if !self.role_sent {
            if let Some(obj) = delta.as_object() {
                if !obj.contains_key("role") {
                    // role is injected below
                }
            }
        }

        let mut delta_obj = match delta {
            Value::Object(map) => map,
            other => {
                let mut map = Map::new();
                map.insert("content".to_string(), other);
                map
            }
        };
        if !self.role_sent {
            delta_obj.insert("role".to_string(), json!("assistant"));
            self.role_sent = true;
        }

        let mut chunk = json!({
            "id": self.response_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": Value::Object(delta_obj),
                "finish_reason": finish_reason,
            }]
        });
        if include_usage {
            if let Some(usage) = self.openai_usage() {
                chunk["usage"] = usage;
            }
        }
        Bytes::from(format!(
            "data: {}\n\n",
            serde_json::to_string(&chunk).unwrap_or_else(|_| "{}".into())
        ))
    }

    fn merge_usage(&mut self, usage: &Value) {
        if let Some(obj) = usage.as_object() {
            for (key, value) in obj {
                if value.is_null() {
                    continue;
                }
                self.usage.insert(key.clone(), value.clone());
            }
        }
    }

    fn handle_message_start(&mut self, data: &Value) -> Vec<Bytes> {
        if let Some(message) = data.get("message") {
            if let Some(id) = message.get("id").and_then(|v| v.as_str()) {
                self.response_id = format!("chatcmpl-{id}");
            }
            if let Some(model) = message.get("model").and_then(|v| v.as_str()) {
                if !model.is_empty() {
                    self.model = model.to_string();
                }
            }
            if let Some(usage) = message.get("usage") {
                self.merge_usage(usage);
            }
        }
        self.started = true;
        vec![self.emit_chunk(json!({}), None, false)]
    }

    fn handle_content_block_start(&mut self, data: &Value) -> Vec<Bytes> {
        let Some(index) = data.get("index").and_then(|v| v.as_u64()) else {
            return Vec::new();
        };
        let block = data.get("content_block").unwrap_or(&Value::Null);
        let block_type = block.get("type").and_then(|t| t.as_str()).unwrap_or("");

        match block_type {
            "text" => {
                self.blocks.insert(
                    index,
                    BlockState {
                        kind: BlockKind::Text,
                        tool_index: 0,
                        call_id: String::new(),
                        name: String::new(),
                        accum: block
                            .get("text")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        start_input: String::new(),
                        done: false,
                    },
                );
            }
            "tool_use" => {
                let call_id = block.get("id").and_then(|v| v.as_str()).unwrap_or("");
                let name = block.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let start_input = block
                    .get("input")
                    .filter(|v| v.as_object().map(|o| !o.is_empty()).unwrap_or(false))
                    .map(|v| v.to_string())
                    .unwrap_or_default();
                let tool_index = self.next_tool_index;
                self.next_tool_index += 1;
                self.blocks.insert(
                    index,
                    BlockState {
                        kind: BlockKind::Tool,
                        tool_index,
                        call_id: call_id.to_string(),
                        name: name.to_string(),
                        accum: String::new(),
                        start_input,
                        done: false,
                    },
                );
                return vec![self.emit_chunk(
                    json!({
                        "tool_calls": [{
                            "index": tool_index,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": ""
                            }
                        }]
                    }),
                    None,
                    false,
                )];
            }
            "thinking" | "redacted_thinking" => {
                self.blocks.insert(
                    index,
                    BlockState {
                        kind: BlockKind::Thinking,
                        tool_index: 0,
                        call_id: String::new(),
                        name: String::new(),
                        accum: block
                            .get("thinking")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        start_input: String::new(),
                        done: false,
                    },
                );
            }
            _ => {}
        }
        Vec::new()
    }

    fn handle_content_block_delta(&mut self, data: &Value) -> Vec<Bytes> {
        let Some(index) = data.get("index").and_then(|v| v.as_u64()) else {
            return Vec::new();
        };
        let delta = data.get("delta").unwrap_or(&Value::Null);
        let delta_type = delta.get("type").and_then(|t| t.as_str()).unwrap_or("");

        let Some(block) = self.blocks.get_mut(&index) else {
            return Vec::new();
        };

        match delta_type {
            "text_delta" => {
                let text = delta.get("text").and_then(|t| t.as_str()).unwrap_or("");
                block.accum.push_str(text);
                let text = text.to_string();
                vec![self.emit_chunk(json!({ "content": text }), None, false)]
            }
            "thinking_delta" => {
                let text = delta.get("thinking").and_then(|t| t.as_str()).unwrap_or("");
                block.accum.push_str(text);
                let text = text.to_string();
                vec![self.emit_chunk(json!({ "reasoning_content": text }), None, false)]
            }
            "input_json_delta" => {
                let partial = delta
                    .get("partial_json")
                    .and_then(|t| t.as_str())
                    .unwrap_or("");
                block.accum.push_str(partial);
                let tool_index = block.tool_index;
                let partial = partial.to_string();
                vec![self.emit_chunk(
                    json!({
                        "tool_calls": [{
                            "index": tool_index,
                            "function": { "arguments": partial }
                        }]
                    }),
                    None,
                    false,
                )]
            }
            _ => Vec::new(),
        }
    }

    fn handle_content_block_stop(&mut self, data: &Value) -> Vec<Bytes> {
        let Some(index) = data.get("index").and_then(|v| v.as_u64()) else {
            return Vec::new();
        };
        let fallback_chunk = if let Some(block) = self.blocks.get_mut(&index) {
            block.done = true;
            if block.kind == BlockKind::Tool {
                let raw_input = if block.accum.trim().is_empty() {
                    block.start_input.clone()
                } else {
                    block.accum.clone()
                };
                if !raw_input.trim().is_empty() && block.accum.trim().is_empty() {
                    let tool_index = block.tool_index;
                    let arguments = canonicalize_tool_arguments_str(&raw_input);
                    Some((tool_index, arguments))
                } else {
                    None
                }
            } else {
                None
            }
        } else {
            None
        };
        if let Some((tool_index, arguments)) = fallback_chunk {
            return vec![self.emit_chunk(
                json!({
                    "tool_calls": [{
                        "index": tool_index,
                        "function": { "arguments": arguments }
                    }]
                }),
                None,
                false,
            )];
        }
        Vec::new()
    }

    fn handle_message_delta(&mut self, data: &Value) -> Vec<Bytes> {
        if let Some(reason) = data.pointer("/delta/stop_reason").and_then(|v| v.as_str()) {
            self.stop_reason = Some(reason.to_string());
        }
        if let Some(usage) = data.get("usage") {
            self.merge_usage(usage);
        }
        Vec::new()
    }

    fn finalize(&mut self) -> Vec<Bytes> {
        if self.completed {
            return Vec::new();
        }
        self.completed = true;
        let finish_reason = Self::map_stop_reason(self.stop_reason.as_deref());
        let mut events = Vec::new();
        events.push(self.emit_chunk(json!({}), finish_reason, true));
        events.push(Bytes::from("data: [DONE]\n\n"));
        events
    }

    fn failed_event(&mut self, message: String) -> Vec<Bytes> {
        if self.completed {
            return Vec::new();
        }
        self.completed = true;
        let chunk = json!({
            "id": self.response_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }],
            "error": { "message": message }
        });
        vec![
            Bytes::from(format!(
                "data: {}\n\n",
                serde_json::to_string(&chunk).unwrap_or_else(|_| "{}".into())
            )),
            Bytes::from("data: [DONE]\n\n"),
        ]
    }
}

fn extract_anthropic_sse_error(value: &Value) -> String {
    let error = value.get("error").unwrap_or(value);
    error
        .as_str()
        .map(ToString::to_string)
        .or_else(|| {
            error
                .get("message")
                .and_then(|v| v.as_str())
                .map(ToString::to_string)
        })
        .unwrap_or_else(|| error.to_string())
}

fn process_anthropic_sse_block(state: &mut AnthropicToOpenAiChatState, block: &str) -> (Vec<Bytes>, bool) {
    if block.trim().is_empty() {
        return (Vec::new(), false);
    }
    let mut event_name: Option<String> = None;
    let mut data_parts: Vec<String> = Vec::new();
    for line in block.lines() {
        if let Some(event) = strip_sse_field(line, "event") {
            event_name = Some(event.trim().to_string());
        }
        if let Some(data) = strip_sse_field(line, "data") {
            data_parts.push(data.to_string());
        }
    }
    if data_parts.is_empty() {
        return (Vec::new(), false);
    }
    if data_parts.join("") == "[DONE]" {
        return (state.finalize(), true);
    }

    let Ok(parsed) = serde_json::from_str::<Value>(&data_parts.join("\n")) else {
        return (Vec::new(), false);
    };

    let msg_type = parsed
        .get("type")
        .and_then(Value::as_str)
        .map(str::to_string)
        .or(event_name)
        .unwrap_or_default();

    let events = match msg_type.as_str() {
        "message_start" => state.handle_message_start(&parsed),
        "content_block_start" => state.handle_content_block_start(&parsed),
        "content_block_delta" => state.handle_content_block_delta(&parsed),
        "content_block_stop" => state.handle_content_block_stop(&parsed),
        "message_delta" => state.handle_message_delta(&parsed),
        "message_stop" => state.finalize(),
        "error" => {
            let message = extract_anthropic_sse_error(&parsed);
            return (state.failed_event(message), true);
        }
        _ => Vec::new(),
    };
    (events, false)
}

fn json_document_candidate(input: &str) -> Option<&str> {
    let trimmed = input.trim_start_matches(|ch: char| ch.is_whitespace() || ch == '\u{feff}');
    matches!(trimmed.as_bytes().first(), Some(b'{') | Some(b'[')).then_some(trimmed)
}

/// Convert a complete non-streaming Anthropic message into OpenAI Chat SSE lifecycle.
pub fn chat_sse_events_from_anthropic_message(body: &Value) -> Vec<Bytes> {
    let mut state = AnthropicToOpenAiChatState::default();
    if body.get("type").and_then(Value::as_str) == Some("error") || body.get("error").is_some() {
        return state
            .failed_event(extract_anthropic_sse_error(body))
            .into_iter()
            .collect();
    }

    let mut message_start = body.clone();
    message_start["content"] = json!([]);
    let mut events = state.handle_message_start(&json!({
        "type": "message_start",
        "message": message_start
    }));

    if let Some(content) = body.get("content").and_then(Value::as_array) {
        for (index, block) in content.iter().enumerate() {
            let block_type = block.get("type").and_then(Value::as_str).unwrap_or("");
            let mut start_block = block.clone();
            match block_type {
                "text" => start_block["text"] = json!(""),
                "thinking" => start_block["thinking"] = json!(""),
                _ => {}
            }
            events.extend(state.handle_content_block_start(&json!({
                "type": "content_block_start",
                "index": index,
                "content_block": start_block
            })));

            match block_type {
                "text" => {
                    if let Some(text) = block.get("text").and_then(Value::as_str) {
                        for piece in split_stream_pieces(text) {
                            events.extend(state.handle_content_block_delta(&json!({
                                "type": "content_block_delta",
                                "index": index,
                                "delta": { "type": "text_delta", "text": piece }
                            })));
                        }
                    }
                }
                "thinking" => {
                    if let Some(thinking) = block.get("thinking").and_then(Value::as_str) {
                        for piece in split_stream_pieces(thinking) {
                            events.extend(state.handle_content_block_delta(&json!({
                                "type": "content_block_delta",
                                "index": index,
                                "delta": { "type": "thinking_delta", "thinking": piece }
                            })));
                        }
                    }
                }
                "tool_use" => {
                    if let Some(input) = block.get("input") {
                        let raw = input.to_string();
                        for piece in split_stream_pieces(&raw) {
                            events.extend(state.handle_content_block_delta(&json!({
                                "type": "content_block_delta",
                                "index": index,
                                "delta": { "type": "input_json_delta", "partial_json": piece }
                            })));
                        }
                    }
                }
                _ => {}
            }

            events.extend(state.handle_content_block_stop(&json!({
                "type": "content_block_stop",
                "index": index
            })));
        }
    }

    events.extend(state.handle_message_delta(&json!({
        "type": "message_delta",
        "delta": { "stop_reason": body.get("stop_reason").cloned().unwrap_or(Value::Null) },
        "usage": body.get("usage").cloned().unwrap_or(Value::Null)
    })));
    events.extend(state.finalize());
    events
}

fn split_stream_pieces(text: &str) -> Vec<String> {
    if text.chars().count() <= 1 {
        return vec![text.to_string()];
    }
    text.chars()
        .map(|ch| ch.to_string())
        .collect::<Vec<_>>()
}

/// Convert upstream Anthropic Messages SSE into OpenAI Chat Completions SSE.
pub fn create_openai_chat_sse_stream_from_anthropic<E: std::error::Error + Send + 'static>(
    stream: impl Stream<Item = Result<Bytes, E>> + Send + 'static,
) -> impl Stream<Item = Result<Bytes, std::io::Error>> + Send {
    async_stream::stream! {
        let mut buffer = String::new();
        let mut utf8_remainder: Vec<u8> = Vec::new();
        let mut state = AnthropicToOpenAiChatState::default();
        let mut stream_failed = false;

        tokio::pin!(stream);

        while let Some(chunk) = stream.next().await {
            match chunk {
                Ok(bytes) => {
                    crate::proxy::sse::append_utf8_safe(&mut buffer, &mut utf8_remainder, &bytes);

                    if json_document_candidate(&buffer).is_none() {
                        while let Some(block) = take_sse_block(&mut buffer) {
                            let (events, failed) = process_anthropic_sse_block(&mut state, &block);
                            for event in events {
                                yield Ok(event);
                            }
                            if failed {
                                stream_failed = true;
                                break;
                            }
                        }
                    }

                    if stream_failed {
                        break;
                    }
                }
                Err(e) => {
                    for event in state.failed_event(format!("Stream error: {e}")) {
                        yield Ok(event);
                    }
                    stream_failed = true;
                    break;
                }
            }
        }

        if !stream_failed && !buffer.trim().is_empty() {
            if !state.started {
                if let Some(candidate) = json_document_candidate(&buffer) {
                    if let Ok(body) = serde_json::from_str::<Value>(candidate) {
                        for event in chat_sse_events_from_anthropic_message(&body) {
                            yield Ok(event);
                        }
                        state.completed = true;
                    }
                }
            }
            if !state.completed {
                let (events, failed) = process_anthropic_sse_block(&mut state, &buffer);
                for event in events {
                    yield Ok(event);
                }
                stream_failed = failed;
            }
        }

        if !stream_failed && !state.completed {
            if state.stop_reason.is_some() {
                for event in state.finalize() {
                    yield Ok(event);
                }
            } else if state.started {
                for event in state.finalize() {
                    yield Ok(event);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures::StreamExt;

    async fn run(input: &str) -> String {
        let stream = futures::stream::iter(vec![Ok::<Bytes, std::io::Error>(Bytes::from(
            input.to_string(),
        ))]);
        let mut out = String::new();
        let converted = create_openai_chat_sse_stream_from_anthropic(stream);
        tokio::pin!(converted);
        while let Some(chunk) = converted.next().await {
            out.push_str(&String::from_utf8_lossy(&chunk.unwrap()));
        }
        out
    }

    fn chunk_payloads(merged: &str) -> Vec<Value> {
        merged
            .split("\n\n")
            .filter_map(|block| {
                let data = block.strip_prefix("data: ")?;
                if data == "[DONE]" {
                    return None;
                }
                serde_json::from_str(data).ok()
            })
            .collect()
    }

    #[tokio::test]
    async fn text_delta_produces_multiple_chat_chunks() {
        let input = concat!(
            "event: message_start\n",
            "data: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\",\"model\":\"claude\",\"usage\":{\"input_tokens\":12,\"output_tokens\":0}}}\n\n",
            "event: content_block_start\n",
            "data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n",
            "event: content_block_delta\n",
            "data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"Hel\"}}\n\n",
            "event: content_block_delta\n",
            "data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"lo\"}}\n\n",
            "event: content_block_stop\n",
            "data: {\"type\":\"content_block_stop\",\"index\":0}\n\n",
            "event: message_delta\n",
            "data: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":2}}\n\n",
            "event: message_stop\n",
            "data: {\"type\":\"message_stop\"}\n\n"
        );
        let merged = run(input).await;
        let payloads = chunk_payloads(&merged);
        let content_chunks: Vec<_> = payloads
            .iter()
            .filter_map(|chunk| {
                chunk
                    .pointer("/choices/0/delta/content")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
            .collect();
        assert!(content_chunks.len() >= 2, "expected multiple content deltas");
        assert_eq!(content_chunks.join(""), "Hello");
        assert!(merged.contains("data: [DONE]"));
        assert_eq!(
            payloads
                .last()
                .and_then(|chunk| chunk.pointer("/choices/0/finish_reason"))
                .and_then(Value::as_str),
            Some("stop")
        );
    }

    #[tokio::test]
    async fn json_message_becomes_chat_sse_lifecycle() {
        let body = json!({
            "id": "msg_json",
            "type": "message",
            "role": "assistant",
            "model": "claude",
            "content": [{"type": "text", "text": "Hi"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 1}
        });
        let events = chat_sse_events_from_anthropic_message(&body);
        let merged = events
            .iter()
            .map(|bytes| String::from_utf8_lossy(bytes))
            .collect::<String>();
        assert!(merged.contains("chat.completion.chunk"));
        assert!(merged.contains("\"content\":\"H\""));
        assert!(merged.contains("data: [DONE]"));
    }
}
