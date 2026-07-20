import { describe, it, expect } from "vitest";
import { tryDecodeBase64Key } from "../../src/utils/base64Key";

const b64 = (s: string) => Buffer.from(s, "utf-8").toString("base64");

describe("tryDecodeBase64Key", () => {
  it("解码标准 Base64 编码的 key", () => {
    const key = "sk-ant-api03-abcdefghijklmnop";
    expect(tryDecodeBase64Key(b64(key))).toBe(key);
  });

  it("解码 Base64URL 变体（-/_ 且无填充）", () => {
    const key = "sk-or-v1-????>>>~~subject"; // 原文含 ?>~ 会产生 +/ 字节
    const urlSafe = b64(key)
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    expect(tryDecodeBase64Key(urlSafe)).toBe(key);
  });

  it("容忍输入首尾空白与解码后的尾部换行（echo | base64 场景）", () => {
    const key = "sk-proj-1234567890abcdef";
    expect(tryDecodeBase64Key(`  ${b64(key + "\n")}  `)).toBe(key);
  });

  it("普通明文 key 不产生解码建议", () => {
    // 含 Base64 字符集之外的字符（点号），直接排除
    expect(tryDecodeBase64Key("sk-ant.api03.hello")).toBeNull();
  });

  it("解码结果含不可打印字符时拒绝", () => {
    // "deadbeef" 是合法 Base64 字符集，但解码是二进制垃圾
    expect(tryDecodeBase64Key("deadbeef")).toBeNull();
  });

  it("解码结果为多字节 UTF-8 文本时拒绝（key 只应是 ASCII）", () => {
    expect(tryDecodeBase64Key(b64("你好世界你好世界"))).toBeNull();
  });

  it("过短输入与非法长度拒绝", () => {
    expect(tryDecodeBase64Key("c2s=")).toBeNull();
    expect(tryDecodeBase64Key("abcdefghi")).toBeNull(); // len%4===1
  });

  it("空输入返回 null", () => {
    expect(tryDecodeBase64Key("")).toBeNull();
    expect(tryDecodeBase64Key("   ")).toBeNull();
  });
});
