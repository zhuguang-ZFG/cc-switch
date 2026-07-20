import React, { useMemo, useState } from "react";
import { Eye, EyeOff, Binary } from "lucide-react";
import { useTranslation } from "react-i18next";
import { tryDecodeBase64Key } from "@/utils/base64Key";

interface ApiKeyInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  label?: string;
  id?: string;
}

const ApiKeyInput: React.FC<ApiKeyInputProps> = ({
  value,
  onChange,
  placeholder,
  disabled = false,
  required = false,
  label = "API Key",
  id = "apiKey",
}) => {
  const { t } = useTranslation();
  const [showKey, setShowKey] = useState(false);

  const toggleShowKey = () => {
    setShowKey(!showKey);
  };

  // 输入内容可按 Base64 解码为合法 Key 时，显示一键解码按钮
  const decodedKey = useMemo(() => tryDecodeBase64Key(value), [value]);

  const handleDecode = () => {
    if (decodedKey) {
      onChange(decodedKey);
    }
  };

  const inputClass = `w-full px-3 py-2 ${decodedKey ? "pr-16" : "pr-10"} border rounded-lg text-sm transition-colors ${
    disabled
      ? "bg-muted border-border-default text-muted-foreground cursor-not-allowed"
      : "border-border-default bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-blue-500/20 dark:focus:ring-blue-400/20"
  }`;

  return (
    <div className="space-y-2">
      <label htmlFor={id} className="block text-sm font-medium text-foreground">
        {label} {required && "*"}
      </label>
      <div className="relative">
        <input
          type={showKey ? "text" : "password"}
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder ?? t("apiKeyInput.placeholder")}
          disabled={disabled}
          required={required}
          autoComplete="off"
          className={inputClass}
        />
        {!disabled && value && (
          <div className="absolute inset-y-0 right-0 flex items-center gap-2 pr-3">
            {decodedKey && (
              <button
                type="button"
                onClick={handleDecode}
                className="text-muted-foreground hover:text-foreground transition-colors"
                title={t("apiKeyInput.decodeBase64")}
                aria-label={t("apiKeyInput.decodeBase64")}
              >
                <Binary size={16} />
              </button>
            )}
            <button
              type="button"
              onClick={toggleShowKey}
              className="text-muted-foreground hover:text-foreground transition-colors"
              aria-label={showKey ? t("apiKeyInput.hide") : t("apiKeyInput.show")}
            >
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default ApiKeyInput;
