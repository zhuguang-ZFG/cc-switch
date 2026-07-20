import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { FormLabel } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Download, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { ApiKeySection } from "./shared";
import {
  fetchModelsForConfig,
  showFetchModelsError,
  type FetchedModel,
} from "@/lib/api/model-fetch";
import {
  REASONIX_PROVIDER_KINDS,
  type ReasonixProviderKind,
} from "@/config/reasonixProviderPresets";
import type { ProviderCategory } from "@/types";

interface ReasonixFormFieldsProps {
  kind: ReasonixProviderKind;
  onKindChange: (kind: ReasonixProviderKind) => void;
  baseUrl: string;
  onBaseUrlChange: (value: string) => void;
  chatUrl?: string;
  onChatUrlChange?: (value: string) => void;
  modelsUrl?: string;
  onModelsUrlChange?: (value: string) => void;
  apiKey: string;
  onApiKeyChange: (value: string) => void;
  category?: ProviderCategory;
  shouldShowApiKeyLink: boolean;
  websiteUrl: string;
  isPartner?: boolean;
  partnerPromotionKey?: string;
  models: string[];
  onModelsChange: (models: string[]) => void;
  defaultModel: string;
  onDefaultModelChange: (model: string) => void;
}

export function ReasonixFormFields({
  kind,
  onKindChange,
  baseUrl,
  onBaseUrlChange,
  chatUrl = "",
  onChatUrlChange,
  modelsUrl = "",
  onModelsUrlChange,
  apiKey,
  onApiKeyChange,
  category,
  shouldShowApiKeyLink,
  websiteUrl,
  isPartner,
  partnerPromotionKey,
  models,
  onModelsChange,
  defaultModel,
  onDefaultModelChange,
}: ReasonixFormFieldsProps) {
  const { t } = useTranslation();
  const [fetchedModels, setFetchedModels] = useState<FetchedModel[]>([]);
  const [isFetchingModels, setIsFetchingModels] = useState(false);

  const handleAddModel = () => {
    onModelsChange([...models, ""]);
  };

  const handleRemoveModel = (index: number) => {
    const next = models.filter((_, i) => i !== index);
    onModelsChange(next);
    if (defaultModel && !next.includes(defaultModel)) {
      onDefaultModelChange(next[0] ?? "");
    }
  };

  const handleModelChange = (index: number, value: string) => {
    const next = [...models];
    const previous = next[index];
    next[index] = value;
    onModelsChange(next);
    if (defaultModel === previous) {
      onDefaultModelChange(value.trim());
    }
  };

  const handleFetchModels = useCallback(() => {
    if (!baseUrl || !apiKey) {
      showFetchModelsError(null, t, {
        hasApiKey: !!apiKey,
        hasBaseUrl: !!baseUrl,
      });
      return;
    }
    setIsFetchingModels(true);
    const trimmedModelsUrl = modelsUrl.trim() || undefined;
    fetchModelsForConfig(baseUrl, apiKey, undefined, trimmedModelsUrl)
      .then((fetched) => {
        setFetchedModels(fetched);
        if (fetched.length === 0) {
          toast.info(t("providerForm.fetchModelsEmpty"));
        } else {
          toast.success(
            t("providerForm.fetchModelsSuccess", { count: fetched.length }),
          );
        }
      })
      .catch((err) => {
        console.warn("[ModelFetch] Failed:", err);
        showFetchModelsError(err, t);
      })
      .finally(() => setIsFetchingModels(false));
  }, [apiKey, baseUrl, modelsUrl, t]);

  const nonEmptyModels = models.filter((model) => model.trim());

  return (
    <>
      <div className="space-y-2">
        <FormLabel htmlFor="reasonix-kind">
          {t("reasonix.form.kind", { defaultValue: "API 协议" })}
        </FormLabel>
        <Select value={kind} onValueChange={(v) => onKindChange(v as ReasonixProviderKind)}>
          <SelectTrigger id="reasonix-kind">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {REASONIX_PROVIDER_KINDS.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {t(item.labelKey)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          {t("reasonix.form.kindHint", {
            defaultValue: "选择供应商 API 协议（OpenAI 或 Anthropic）。",
          })}
        </p>
      </div>

      <div className="space-y-2">
        <FormLabel htmlFor="reasonix-baseurl">
          {t("reasonix.form.baseUrl", { defaultValue: "API 端点" })}
        </FormLabel>
        <Input
          id="reasonix-baseurl"
          value={baseUrl}
          onChange={(e) => onBaseUrlChange(e.target.value)}
          placeholder="https://api.example.com/v1"
        />
        <p className="text-xs text-muted-foreground">
          {t("reasonix.form.baseUrlHint", {
            defaultValue: "Reasonix 供应商的 API 端点地址。",
          })}
        </p>
      </div>

      {onChatUrlChange && (
        <div className="space-y-2">
          <FormLabel htmlFor="reasonix-chat-url">
            {t("reasonix.form.chatUrl", {
              defaultValue: "Chat URL（可选）",
            })}
          </FormLabel>
          <Input
            id="reasonix-chat-url"
            value={chatUrl}
            onChange={(e) => onChatUrlChange(e.target.value)}
            placeholder="https://api.example.com/v1/chat/completions"
          />
          <p className="text-xs text-muted-foreground">
            {t("reasonix.form.chatUrlHint", {
              defaultValue:
                "可选：完全覆盖 Chat Completions 请求 URL；留空则使用 base_url + /chat/completions。",
            })}
          </p>
        </div>
      )}

      {onModelsUrlChange && (
        <div className="space-y-2">
          <FormLabel htmlFor="reasonix-models-url">
            {t("reasonix.form.modelsUrl", {
              defaultValue: "Models URL（可选）",
            })}
          </FormLabel>
          <Input
            id="reasonix-models-url"
            value={modelsUrl}
            onChange={(e) => onModelsUrlChange(e.target.value)}
            placeholder="https://api.example.com/v1/models"
          />
          <p className="text-xs text-muted-foreground">
            {t("reasonix.form.modelsUrlHint", {
              defaultValue:
                "可选：覆盖 /models 探测 URL；留空则自动推导。",
            })}
          </p>
        </div>
      )}

      <ApiKeySection
        value={apiKey}
        onChange={onApiKeyChange}
        category={category === "official" ? undefined : category}
        shouldShowLink={shouldShowApiKeyLink}
        websiteUrl={websiteUrl}
        isPartner={isPartner}
        partnerPromotionKey={partnerPromotionKey}
      />

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <FormLabel>
            {t("reasonix.form.models", { defaultValue: "模型列表" })}
          </FormLabel>
          <div className="flex gap-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleFetchModels}
              disabled={isFetchingModels}
              className="h-7 gap-1"
            >
              {isFetchingModels ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Download className="h-3.5 w-3.5" />
              )}
              {t("providerForm.fetchModels")}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleAddModel}
              className="h-7 gap-1"
            >
              <Plus className="h-3.5 w-3.5" />
              {t("reasonix.form.addModel", { defaultValue: "添加模型" })}
            </Button>
          </div>
        </div>

        {models.length === 0 ? (
          <p className="text-sm text-muted-foreground py-2">
            {t("reasonix.form.noModels", {
              defaultValue: "暂无模型。请至少添加一个模型 ID。",
            })}
          </p>
        ) : (
          <div className="space-y-2">
            {models.map((model, index) => (
              <div key={`${index}-${model}`} className="flex items-center gap-2">
                <Input
                  value={model}
                  onChange={(e) => handleModelChange(index, e.target.value)}
                  placeholder={t("reasonix.form.modelIdPlaceholder", {
                    defaultValue: "deepseek-v4-flash",
                  })}
                  className="flex-1"
                  list={
                    fetchedModels.length > 0 ? "reasonix-fetched-models" : undefined
                  }
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => handleRemoveModel(index)}
                  className="h-9 w-9 text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
            {fetchedModels.length > 0 && (
              <datalist id="reasonix-fetched-models">
                {fetchedModels.map((model) => (
                  <option key={model.id} value={model.id} />
                ))}
              </datalist>
            )}
          </div>
        )}

        <p className="text-xs text-muted-foreground">
          {t("reasonix.form.modelsHint", {
            defaultValue: "模型 ID 列表，写入 Reasonix live 配置的 models 字段。",
          })}
        </p>
      </div>

      <div className="space-y-2">
        <FormLabel htmlFor="reasonix-default">
          {t("reasonix.form.default", { defaultValue: "默认模型" })}
        </FormLabel>
        {nonEmptyModels.length > 0 ? (
          <Select
            value={defaultModel || nonEmptyModels[0]}
            onValueChange={onDefaultModelChange}
          >
            <SelectTrigger id="reasonix-default">
              <SelectValue
                placeholder={t("reasonix.form.defaultPlaceholder", {
                  defaultValue: "选择默认模型",
                })}
              />
            </SelectTrigger>
            <SelectContent>
              {nonEmptyModels.map((model) => (
                <SelectItem key={model} value={model}>
                  {model}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <Input
            id="reasonix-default"
            value={defaultModel}
            onChange={(e) => onDefaultModelChange(e.target.value)}
            placeholder={t("reasonix.form.defaultPlaceholder", {
              defaultValue: "选择或输入默认模型",
            })}
          />
        )}
        <p className="text-xs text-muted-foreground">
          {t("reasonix.form.defaultHint", {
            defaultValue: "切换到此供应商时写入 default_model；留空则取 models 第一项。",
          })}
        </p>
      </div>
    </>
  );
}
