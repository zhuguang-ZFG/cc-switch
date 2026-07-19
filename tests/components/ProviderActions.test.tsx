import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProviderActions } from "@/components/providers/ProviderActions";

type ProviderActionsProps = Parameters<typeof ProviderActions>[0];

function createProps(
  overrides: Partial<ProviderActionsProps> = {},
): ProviderActionsProps {
  return {
    appId: "kimicode",
    isCurrent: false,
    isInConfig: false,
    onSwitch: vi.fn(),
    onEdit: vi.fn(),
    onDuplicate: vi.fn(),
    onDelete: vi.fn(),
    onRemoveFromConfig: vi.fn(),
    ...overrides,
  };
}

// 测试环境 i18n 资源为空：无 defaultValue 的 t() 返回 key 本身，
// 有 defaultValue 的返回 defaultValue（见 tests/setupTests.ts）。
describe("ProviderActions kimicode proxy takeover", () => {
  it("uses switch semantics for the main button under takeover (not in-config)", () => {
    const props = createProps({ isProxyTakeover: true });
    render(<ProviderActions {...props} />);

    const button = screen.getByRole("button", { name: "provider.enable" });
    expect(button).toBeEnabled();

    fireEvent.click(button);
    expect(props.onSwitch).toHaveBeenCalledTimes(1);
    expect(props.onRemoveFromConfig).not.toHaveBeenCalled();
    expect(props.onDelete).not.toHaveBeenCalled();
  });

  it("ignores isInConfig under takeover: in-config provider still shows enable", () => {
    const props = createProps({ isProxyTakeover: true, isInConfig: true });
    render(<ProviderActions {...props} />);

    const button = screen.getByRole("button", { name: "provider.enable" });
    fireEvent.click(button);
    expect(props.onSwitch).toHaveBeenCalledTimes(1);
    expect(props.onRemoveFromConfig).not.toHaveBeenCalled();
  });

  it("shows a disabled in-use button for the current provider under takeover", () => {
    const props = createProps({ isProxyTakeover: true, isCurrent: true });
    render(<ProviderActions {...props} />);

    const button = screen.getByRole("button", { name: "provider.inUse" });
    expect(button).toBeDisabled();
  });

  it("keeps the official-blocked disable state under takeover", () => {
    const props = createProps({
      isProxyTakeover: true,
      isOfficialBlockedByProxy: true,
    });
    render(<ProviderActions {...props} />);

    const button = screen.getByRole("button", { name: "provider.enable" });
    expect(button).toBeDisabled();
  });

  it("hides the additive set-as-default (Zap) button under takeover", () => {
    const props = createProps({
      isProxyTakeover: true,
      isInConfig: true,
      onSetAsDefault: vi.fn(),
    });
    render(<ProviderActions {...props} />);

    // Kimi 的 Zap 非默认态文案是 defaultValue「启用」（主按钮走 key，
    // 不会撞名）；接管下整个 Zap 不应渲染。
    expect(screen.queryByRole("button", { name: "启用" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "provider.enable" }),
    ).toBeInTheDocument();
  });

  it("renders the Zap button without takeover", () => {
    const props = createProps({
      isInConfig: true,
      onSetAsDefault: vi.fn(),
    });
    render(<ProviderActions {...props} />);

    const zap = screen.getByRole("button", { name: "启用" });
    fireEvent.click(zap);
    expect(props.onSetAsDefault).toHaveBeenCalledTimes(1);
  });

  it("lets read-only (managed) providers hot-switch under takeover", () => {
    const props = createProps({
      isProxyTakeover: true,
      isInConfig: true,
      isReadOnly: true,
    });
    render(<ProviderActions {...props} />);

    const button = screen.getByRole("button", { name: "provider.enable" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(props.onSwitch).toHaveBeenCalledTimes(1);
  });
});

describe("ProviderActions kimicode additive mode (no takeover)", () => {
  it("removes from config when clicking the main button on an in-config provider", () => {
    const props = createProps({ isInConfig: true });
    render(<ProviderActions {...props} />);

    fireEvent.click(screen.getByRole("button", { name: "移除" }));
    expect(props.onRemoveFromConfig).toHaveBeenCalledTimes(1);
    expect(props.onSwitch).not.toHaveBeenCalled();
  });

  it("adds to config when clicking the main button on a provider not in config", () => {
    const props = createProps({ isInConfig: false });
    render(<ProviderActions {...props} />);

    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    expect(props.onSwitch).toHaveBeenCalledTimes(1);
  });

  it("does not render the dead disabled remove button for read-only providers", () => {
    const props = createProps({ isInConfig: true, isReadOnly: true });
    render(<ProviderActions {...props} />);

    expect(screen.queryByRole("button", { name: "移除" })).toBeNull();
    expect(screen.queryByRole("button", { name: "添加" })).toBeNull();
  });
});

describe("ProviderActions kimicode failover mode", () => {
  it("shows add-to-queue when takeover + failover are on and provider is not in queue", () => {
    const props = createProps({
      isProxyTakeover: true,
      isAutoFailoverEnabled: true,
      isInFailoverQueue: false,
      onToggleFailover: vi.fn(),
    });
    render(<ProviderActions {...props} />);

    const button = screen.getByRole("button", { name: "加入" });
    fireEvent.click(button);
    expect(props.onToggleFailover).toHaveBeenCalledWith(true);
    expect(props.onSwitch).not.toHaveBeenCalled();
  });

  it("shows in-queue when takeover + failover are on and provider is already in the queue", () => {
    const props = createProps({
      isProxyTakeover: true,
      isAutoFailoverEnabled: true,
      isInFailoverQueue: true,
      onToggleFailover: vi.fn(),
    });
    render(<ProviderActions {...props} />);

    const button = screen.getByRole("button", { name: "已加入" });
    fireEvent.click(button);
    expect(props.onToggleFailover).toHaveBeenCalledWith(false);
    expect(props.onSwitch).not.toHaveBeenCalled();
  });

  it("queue semantics win over takeover switch semantics when both are on", () => {
    const props = createProps({
      isProxyTakeover: true,
      isAutoFailoverEnabled: true,
      isInFailoverQueue: false,
      onToggleFailover: vi.fn(),
    });
    render(<ProviderActions {...props} />);

    expect(screen.queryByRole("button", { name: "provider.enable" })).toBeNull();
    const button = screen.getByRole("button", { name: "加入" });
    fireEvent.click(button);
    expect(props.onToggleFailover).toHaveBeenCalledWith(true);
    expect(props.onSwitch).not.toHaveBeenCalled();
  });

  it("lets read-only (managed) providers join the failover queue under takeover", () => {
    const props = createProps({
      isProxyTakeover: true,
      isAutoFailoverEnabled: true,
      isInFailoverQueue: false,
      isReadOnly: true,
      onToggleFailover: vi.fn(),
    });
    render(<ProviderActions {...props} />);

    const button = screen.getByRole("button", { name: "加入" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(props.onToggleFailover).toHaveBeenCalledWith(true);
  });

  it("does not enter queue mode when failover flag is on but takeover is off", () => {
    // Regression: isKimiFailover must require takeover, otherwise additive
    // add/remove is stolen by the queue button.
    const props = createProps({
      isProxyTakeover: false,
      isAutoFailoverEnabled: true,
      isInConfig: false,
      onToggleFailover: vi.fn(),
    });
    render(<ProviderActions {...props} />);

    expect(screen.queryByRole("button", { name: "加入" })).toBeNull();
    const button = screen.getByRole("button", { name: "添加" });
    fireEvent.click(button);
    expect(props.onSwitch).toHaveBeenCalledTimes(1);
    expect(props.onToggleFailover).not.toHaveBeenCalled();
  });
});
