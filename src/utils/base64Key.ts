// API Key 的 Base64 解码工具
//
// 场景：中转站/群组常以 Base64 形式分发 API Key（例如 `echo "sk-..." | base64`）。
// 输入框旁提供一键解码，避免用户手动开终端解码。

/** 解码结果为可信 API Key 的最小长度（低于此值多为误判） */
const MIN_DECODED_LENGTH = 8;

/** 标准 Base64 / Base64URL 字符集（允许省略末尾填充） */
const BASE64_PATTERN = /^[A-Za-z0-9+/]+={0,2}$|^[A-Za-z0-9_-]+={0,2}$/;

/**
 * 尝试把输入按 Base64（含 URL-safe 变体）解码成一个可信的 API Key。
 *
 * 返回解码后的 key；输入不是合法 Base64、解码结果包含不可打印字符、
 * 或解码结果与输入相同时返回 null（此时 UI 不显示解码按钮）。
 */
export function tryDecodeBase64Key(input: string): string | null {
  const trimmed = input.trim();
  if (trimmed.length < MIN_DECODED_LENGTH) return null;
  if (!BASE64_PATTERN.test(trimmed)) return null;

  // Base64URL → 标准 Base64，并补齐填充
  const normalized = trimmed.replace(/-/g, "+").replace(/_/g, "/");
  const remainder = normalized.length % 4;
  if (remainder === 1) return null; // 非法长度
  const padded =
    remainder === 0 ? normalized : normalized + "=".repeat(4 - remainder);

  let bytes: Uint8Array;
  try {
    const binary = atob(padded);
    bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  } catch {
    return null;
  }

  let decoded: string;
  try {
    decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }

  // 允许 `echo` 带出的末尾换行/空白
  decoded = decoded.trim();
  if (decoded.length < MIN_DECODED_LENGTH) return null;
  if (decoded === trimmed) return null;

  // API Key 只应包含可打印 ASCII 字符；出现其他字符说明原文不是 Base64 编码的 key
  if (!/^[\x21-\x7e]+$/.test(decoded)) return null;

  return decoded;
}
