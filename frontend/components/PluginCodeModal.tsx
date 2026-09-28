/**
 * Read-only view of one installed component's source (Ferramentas ›
 * Componentes).
 *
 * Built on the shared Modal. It used to be a hand-rolled overlay whose
 * `plugin-modal-*` classes were never defined in the stylesheet, so it
 * rendered as unstyled text at the foot of the page -- below the fold of a
 * window whose body does not scroll -- with no Escape, no focus trap and no
 * backdrop. The Modal primitive already provides all three.
 */
import React, { useState } from "react";

import Icon from "./Icon";
import { Button, Modal } from "./ui";

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
  onClose,
}: PluginCodeModalProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked; the code is still selectable */ }
  };

  return (
    <Modal
      open
      onClose={onClose}
      width="wide"
      title={filename || `${pluginName}.py`}
      footer={
        <>
          <Button onClick={handleCopy} title="Copiar código do plugin">
            <Icon name={copied ? "check" : "copy"} size={15} />
            {copied ? "Copiado" : "Copiar"}
          </Button>
          <Button variant="primary" onClick={onClose}>Fechar</Button>
        </>
      }
    >
      {tools.length > 0 && (
        <p className="muted code-view__meta">
          {tools.length} {tools.length === 1 ? "ferramenta" : "ferramentas"}: {tools.join(", ")}
        </p>
      )}
      <pre className="code-view"><code>{code || "# A carregar o código-fonte…"}</code></pre>
    </Modal>
  );
}
