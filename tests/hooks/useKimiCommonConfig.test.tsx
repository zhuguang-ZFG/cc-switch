import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useKimiCommonConfig } from "@/components/providers/forms/hooks/useKimiCommonConfig";

const getCommonConfigSnippetMock = vi.fn();
const setCommonConfigSnippetMock = vi.fn();

vi.mock("@/lib/api", () => ({
  configApi: {
    getCommonConfigSnippet: (...args: unknown[]) =>
      getCommonConfigSnippetMock(...args),
    setCommonConfigSnippet: (...args: unknown[]) =>
      setCommonConfigSnippetMock(...args),
  },
}));

describe("useKimiCommonConfig", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCommonConfigSnippetMock.mockResolvedValue("");
    setCommonConfigSnippetMock.mockResolvedValue(undefined);
  });

  it("loads the saved kimicode snippet on mount", async () => {
    getCommonConfigSnippetMock.mockResolvedValue("[thinking]\nenabled = true\n");

    const { result } = renderHook(() => useKimiCommonConfig({ enabled: true }));

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(getCommonConfigSnippetMock).toHaveBeenCalledWith("kimicode");
    expect(result.current.commonConfigSnippet).toBe(
      "[thinking]\nenabled = true\n",
    );
  });

  it("persists a valid TOML snippet", async () => {
    const { result } = renderHook(() => useKimiCommonConfig({ enabled: true }));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let saved = false;
    await act(async () => {
      saved = await result.current.handleCommonConfigSnippetChange(
        "[thinking]\nenabled = true\n",
      );
    });

    expect(saved).toBe(true);
    expect(setCommonConfigSnippetMock).toHaveBeenCalledWith(
      "kimicode",
      "[thinking]\nenabled = true\n",
    );
    expect(result.current.commonConfigError).toBe("");
  });

  it("does not persist an invalid TOML snippet", async () => {
    const { result } = renderHook(() => useKimiCommonConfig({ enabled: true }));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let saved = true;
    await act(async () => {
      saved = await result.current.handleCommonConfigSnippetChange(
        "[broken",
      );
    });

    expect(saved).toBe(false);
    expect(setCommonConfigSnippetMock).not.toHaveBeenCalled();
    expect(result.current.commonConfigError).not.toBe("");
  });

  it("clears the snippet when saving an empty value", async () => {
    const { result } = renderHook(() => useKimiCommonConfig({ enabled: true }));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let saved = false;
    await act(async () => {
      saved = await result.current.handleCommonConfigSnippetChange("   ");
    });

    expect(saved).toBe(true);
    expect(setCommonConfigSnippetMock).toHaveBeenCalledWith("kimicode", "");
    expect(result.current.commonConfigSnippet).toBe("");
  });

  it("persists the normalized TOML text (curly quotes) that validation sees", async () => {
    const { result } = renderHook(() => useKimiCommonConfig({ enabled: true }));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let saved = false;
    await act(async () => {
      saved = await result.current.handleCommonConfigSnippetChange(
        "default_model = “kimi-for-coding”\n",
      );
    });

    expect(saved).toBe(true);
    expect(setCommonConfigSnippetMock).toHaveBeenCalledWith(
      "kimicode",
      'default_model = "kimi-for-coding"\n',
    );
    expect(result.current.commonConfigSnippet).toBe(
      'default_model = "kimi-for-coding"\n',
    );
    expect(result.current.commonConfigError).toBe("");
  });

  it("surfaces a backend save failure", async () => {
    setCommonConfigSnippetMock.mockRejectedValue(new Error("db locked"));

    const { result } = renderHook(() => useKimiCommonConfig({ enabled: true }));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let saved = true;
    await act(async () => {
      saved = await result.current.handleCommonConfigSnippetChange(
        "[thinking]\nenabled = true\n",
      );
    });

    expect(saved).toBe(false);
    expect(result.current.commonConfigError).not.toBe("");
  });

  it("resyncs local state from the backend after a partial save failure", async () => {
    // 孤儿接管场景：后端已把新片段落库，仅 backup 写入失败并返回错误。
    // 失败后必须重拉片段，否则重开编辑器会用旧值覆盖已保存内容。
    setCommonConfigSnippetMock.mockRejectedValue(new Error("backup missing"));
    getCommonConfigSnippetMock.mockResolvedValue("[thinking]\neffort = \"max\"\n");

    const { result } = renderHook(() => useKimiCommonConfig({ enabled: true }));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let saved = true;
    await act(async () => {
      saved = await result.current.handleCommonConfigSnippetChange(
        "[thinking]\neffort = \"max\"\n",
      );
    });

    expect(saved).toBe(false);
    expect(getCommonConfigSnippetMock).toHaveBeenCalledTimes(2);
    expect(result.current.commonConfigSnippet).toBe(
      "[thinking]\neffort = \"max\"\n",
    );
  });
});
