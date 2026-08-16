import React, { useState } from "react";

interface PluginCodeModalProps {
  pluginName: string;
  code: string;
  tools: string[];
  filename: string;
  onClose: () => void;
}

export default function PluginCodeModal({
  pluginName,
  code,
  tools,
  filename,
  onClose
}: PluginCodeModalProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    if (navigator?.clipboard) {
      navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="plugin-modal-overlay" onClick={onClose}>
      <div className="plugin-modal-container" onClick={e => e.stopPropagation()}>
        <div className="plugin-modal-header">
          <div className="plugin-modal-title-area">
            <span className="plugin-modal-filename">{filename || `${pluginName}.py`}</span>
            {tools.length > 0 && (
              <span className="plugin-card-badge">
                {tools.length} {tools.length === 1 ? "ferramenta" : "ferramentas"}
              </span>
            )}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              type="button"
              className="topbar-btn"
              onClick={handleCopy}
              title="Copiar código do plugin"
            >
              {copied ? "Copiado" : "Copiar"}
            </button>
            <button
              type="button"
              className="plugin-modal-close-btn"
              onClick={onClose}
            >
              Fechar
            </button>
          </div>
        </div>

        <pre className="plugin-modal-code-body">
          <code>{code || "# Carregando código-fonte..."}</code>
        </pre>
      </div>
    </div>
  );
}
