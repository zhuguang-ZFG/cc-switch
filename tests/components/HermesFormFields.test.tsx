import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps, PropsWithChildren } from "react";
import { useForm } from "react-hook-form";
import { describe, expect, it, vi } from "vitest";
import { HermesFormFields } from "@/components/providers/forms/HermesFormFields";
import { Form } from "@/components/ui/form";

const endpointSpeedTestProps = vi.hoisted(() => vi.fn());

vi.mock("@/components/providers/forms/EndpointSpeedTest", () => ({
  default: (props: Record<string, unknown>) => {
    endpointSpeedTestProps(props);
    return <div data-testid="endpoint-speed-test" />;
  },
}));

type HermesFormFieldsProps = ComponentProps<typeof HermesFormFields>;

const FormShell = ({ children }: PropsWithChildren) => {
  const form = useForm();

  return <Form {...form}>{children}</Form>;
};

const renderHermesForm = (overrides: Partial<HermesFormFieldsProps> = {}) => {
  const props: HermesFormFieldsProps = {
    baseUrl: "https://api.example.com/v1",
    onBaseUrlChange: vi.fn(),
    apiKey: "sk-test",
    onApiKeyChange: vi.fn(),
    category: "custom",
    shouldShowApiKeyLink: false,
    websiteUrl: "",
    apiMode: "openai",
    onApiModeChange: vi.fn(),
    env: {},
    onEnvFieldChange: vi.fn(),
    models: [],
    onModelsChange: vi.fn(),
    rateLimitDelay: undefined,
    onRateLimitDelayChange: vi.fn(),
    autoSelect: true,
    onAutoSelectChange: vi.fn(),
    customUserAgent: "",
    onCustomUserAgentChange: vi.fn(),
    localProxyHeadersOverride: "",
    onLocalProxyHeadersOverrideChange: vi.fn(),
    localProxyBodyOverride: "",
    onLocalProxyBodyOverrideChange: vi.fn(),
    ...overrides,
  };

  return {
    props,
    ...render(
      <FormShell>
        <HermesFormFields {...props} />
      </FormShell>,
    ),
  };
};

const openSpeedTestModal = () => {
  fireEvent.click(screen.getByRole("button", { name: /管理和测速/ }));
};

const lastSpeedTestProps = () =>
  endpointSpeedTestProps.mock.calls.at(-1)?.[0] as Record<string, unknown>;

const expandProviderAdvanced = () => {
  fireEvent.click(
    screen.getByRole("button", { name: "kimicode.form.providerAdvanced" }),
  );
};

describe("HermesFormFields 端点测速接线", () => {
  it("编辑态向 EndpointSpeedTest 透传 providerId", () => {
    renderHermesForm({ providerId: "provider-1" });

    openSpeedTestModal();

    expect(endpointSpeedTestProps).toHaveBeenCalled();
    expect(lastSpeedTestProps()).toMatchObject({
      appId: "kimicode",
      providerId: "provider-1",
    });
  });

  it("新建态透传 onCustomEndpointsChange，避免自定义端点假保存", () => {
    const onCustomEndpointsChange = vi.fn();
    renderHermesForm({ onCustomEndpointsChange });

    openSpeedTestModal();

    const props = lastSpeedTestProps();
    expect(props.providerId).toBeUndefined();
    expect(props.onCustomEndpointsChange).toBe(onCustomEndpointsChange);
  });

  it("autoSelect 勾选状态由父表单控制并持久化", () => {
    const onAutoSelectChange = vi.fn();
    renderHermesForm({ autoSelect: false, onAutoSelectChange });

    openSpeedTestModal();

    const props = lastSpeedTestProps();
    expect(props.autoSelect).toBe(false);
    (props.onAutoSelectChange as (checked: boolean) => void)(true);
    expect(onAutoSelectChange).toHaveBeenCalledWith(true);
  });
});

describe("HermesFormFields 本地代理字段", () => {
  it("非官方供应商渲染自定义 UA 与请求覆盖字段", () => {
    renderHermesForm();

    expandProviderAdvanced();

    expect(screen.getByPlaceholderText("Mozilla/5.0 ...")).toBeInTheDocument();
    expect(screen.getByText("本地代理请求覆盖")).toBeInTheDocument();
  });

  it("官方供应商隐藏本地代理字段（与 meta 保存口径一致）", () => {
    renderHermesForm({ category: "official" });

    expandProviderAdvanced();

    expect(screen.queryByPlaceholderText("Mozilla/5.0 ...")).toBeNull();
    expect(screen.queryByText("本地代理请求覆盖")).toBeNull();
  });

  it("编辑自定义 UA 会触发 onCustomUserAgentChange", () => {
    const onCustomUserAgentChange = vi.fn();
    renderHermesForm({ onCustomUserAgentChange });

    expandProviderAdvanced();
    fireEvent.change(screen.getByPlaceholderText("Mozilla/5.0 ..."), {
      target: { value: "MyAgent/1.0" },
    });

    expect(onCustomUserAgentChange).toHaveBeenCalledWith("MyAgent/1.0");
  });
});

describe("HermesFormFields vertexai env 字段", () => {
  it("vertexai 类型显示 GCP env 输入并回写", () => {
    const onEnvFieldChange = vi.fn();
    renderHermesForm({
      apiMode: "vertexai",
      env: { GOOGLE_CLOUD_PROJECT: "proj-1" },
      onEnvFieldChange,
    });

    const project = screen.getByPlaceholderText("my-gcp-project");
    expect(project).toHaveValue("proj-1");
    fireEvent.change(screen.getByPlaceholderText("us-central1"), {
      target: { value: "asia-east1" },
    });
    expect(onEnvFieldChange).toHaveBeenCalledWith(
      "GOOGLE_CLOUD_LOCATION",
      "asia-east1",
    );
  });

  it("非 vertexai 类型不渲染 GCP env 输入", () => {
    renderHermesForm({ apiMode: "openai" });
    expect(screen.queryByPlaceholderText("my-gcp-project")).toBeNull();
  });
});
