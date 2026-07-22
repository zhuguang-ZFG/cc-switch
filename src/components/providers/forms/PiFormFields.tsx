import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FormLabel } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { ApiKeySection, ModelInputWithFetch } from "./shared";
import {
  fetchModelsForConfig,
  showFetchModelsError,
  type FetchedModel,
} from "@/lib/api/model-fetch";
import type { ProviderCategory } from "@/types";

interface PiFormFieldsProps {
  baseUrl: string;
  onBaseUrlChange: (value: string) => void;
  apiKey: string;
  onApiKeyChange: (value: string) => void;
  category?: ProviderCategory;
  shouldShowApiKeyLink: boolean;
  websiteUrl: string;
  models: string[];
  onModelsChange: (models: string[]) => void;
  defaultModel: string;
  onDefaultModelChange: (model: string) => void;
}

export function PiFormFields({
  baseUrl,
  onBaseUrlChange,
  apiKey,
  onApiKeyChange,
  category,
  shouldShowApiKeyLink,
  websiteUrl,
  models,
  onModelsChange,
  defaultModel,
  onDefaultModelChange,
}: PiFormFieldsProps) {
  const { t } = useTranslation();
  const [fetchedModels, setFetchedModels] = useState<FetchedModel[]>([]);
  const [isFetching, setIsFetching] = useState(false);
  const fetchSeq = useRef(0);

  const handleFetchModels = useCallback(async () => {
    const seq = ++fetchSeq.current;
    setIsFetching(true);
    try {
      const result = await fetchModelsForConfig({
        appType: "pi",
        baseUrl,
        apiKey,
      });
      if (seq !== fetchSeq.current) return;
      setFetchedModels(result);
      if (result.length === 0) {
        toast.message(
          t("pi.form.noModelsFetched", {
            defaultValue: "未拉取到模型，请检查端点与密钥",
          }),
        );
      }
    } catch (error) {
      if (seq !== fetchSeq.current) return;
      showFetchModelsError(error, t);
    } finally {
      if (seq === fetchSeq.current) setIsFetching(false);
    }
  }, [apiKey, baseUrl, t]);

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <FormLabel htmlFor="pi-baseurl">
          {t("pi.form.baseUrl", { defaultValue: "API 端点" })}
        </FormLabel>
        <Input
          id="pi-baseurl"
          value={baseUrl}
          onChange={(e) => onBaseUrlChange(e.target.value)}
          placeholder="https://api.example.com/v1"
        />
        <p className="text-xs text-muted-foreground">
          {t("pi.form.baseUrlHint", {
            defaultValue:
              "OpenAI-compatible base URL。代理接管时将指向本地 /pi/v1。",
          })}
        </p>
      </div>

      <ApiKeySection
        apiKey={apiKey}
        onApiKeyChange={onApiKeyChange}
        category={category}
        shouldShowApiKeyLink={shouldShowApiKeyLink}
        websiteUrl={websiteUrl}
      />

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <FormLabel>
            {t("pi.form.models", { defaultValue: "模型" })}
          </FormLabel>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleFetchModels}
            disabled={isFetching || !baseUrl.trim()}
          >
            {isFetching ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              t("pi.form.fetchModels", { defaultValue: "拉取模型" })
            )}
          </Button>
        </div>
        <div className="space-y-2">
          {models.map((model, index) => (
            <div key={`pi-model-${index}`} className="flex items-center gap-2">
              <ModelInputWithFetch
                value={model}
                onChange={(value) => {
                  const next = [...models];
                  next[index] = value;
                  onModelsChange(next);
                }}
                fetchedModels={fetchedModels}
                placeholder="model-id"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => {
                  const next = models.filter((_, i) => i !== index);
                  onModelsChange(next.length ? next : [""]);
                }}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onModelsChange([...models, ""])}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            {t("pi.form.addModel", { defaultValue: "添加模型" })}
          </Button>
        </div>
      </div>

      <div className="space-y-2">
        <FormLabel htmlFor="pi-default-model">
          {t("pi.form.defaultModel", { defaultValue: "默认模型" })}
        </FormLabel>
        <Input
          id="pi-default-model"
          value={defaultModel}
          onChange={(e) => onDefaultModelChange(e.target.value)}
          placeholder={models.find((m) => m.trim()) || "model-id"}
        />
      </div>
    </div>
  );
}
