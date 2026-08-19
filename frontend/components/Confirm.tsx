/**
 * The live approval dialog.
 *
 * This is the one the person actually sees when a tool needs authorization, so
 * it must show the arguments being approved. Approving "change Wi-Fi" without
 * seeing the payload is not informed consent.
 */
import React from "react";
import { Button, Modal, sanitizeArgs } from "./ui";

interface ConfirmProps {
  message: string;
  meta: Record<string, any>;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmModal({ message, meta, onConfirm, onCancel }: ConfirmProps) {
  const args = sanitizeArgs(meta?.args);
  const tool = meta?.tool ?? "desconhecida";

  return (
    <Modal
      open
      onClose={onCancel}
      eyebrow="Autorização necessária"
      title={message}
      width="narrow"
      footer={
        <>
          <Button onClick={onCancel}>Recusar</Button>
          <Button variant="primary" onClick={onConfirm}>Autorizar</Button>
        </>
      }
    >
      <dl className="kv">
        <dt>Ferramenta</dt>
        <dd className="mono">{tool}</dd>
      </dl>

      {args.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className="cc-tile-label">Argumentos que vais autorizar</div>
          <div className="perm-args">
            {args.map(({ key, value }) => (
              <div key={key}><strong>{key}</strong>: {value}</div>
            ))}
          </div>
        </div>
      )}

      <p className="dim" style={{ marginTop: 16, fontSize: 12 }}>
        Autorizar aplica-se apenas a esta execução.
      </p>
    </Modal>
  );
}
