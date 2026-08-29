/**
 * The live approval dialog.
 *
 * This is the one the person actually sees when a tool needs authorization, so
 * it must show what they are actually approving. Approving "change Wi-Fi"
 * without seeing the payload is not informed consent.
 *
 * WHAT CHANGED IN PC CONTROL V2. The card used to lead with a sentence built
 * from the capability name and the raw permission target -- "O Nano pretende
 * executar 'pc.window.close' sobre 'window:786686'. Confirmas?" -- which names
 * a capability the person has never heard of and a handle that means nothing
 * to them. The only thing anybody can do with that is press Yes.
 *
 * It now leads with three things a person can judge:
 *
 *     ACTION   FECHAR JANELA
 *     TARGET   Discord — #chat-dos-adm
 *     SCOPE    Apenas esta janela
 *
 * plus, where the size of the decision is not visible from the target alone, a
 * PREVIEW: how many windows a batch close affects and what they are called, or
 * how many items are inside a folder about to be recycled. "Fecha tudo do
 * Discord" is a different decision at one window than at nine.
 *
 * The structured fields come from the backend (core/confirmation.py). They are
 * optional on purpose: an older backend, or a capability with no label, still
 * renders a usable card from `message` and the arguments.
 */
import React from "react";
import { Button, Modal, RiskBadge, sanitizeArgs } from "./ui";

interface ConfirmProps {
  message: string;
  meta: Record<string, any>;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmModal({ message, meta, onConfirm, onCancel }: ConfirmProps) {
  const args = sanitizeArgs(meta?.args);
  const tool = meta?.tool ?? "desconhecida";
  const action: string | undefined = meta?.action;
  const target: string | undefined = meta?.target;
  const scope: string | undefined = meta?.scope;
  const preview = meta?.preview ?? {};
  const previewItems: string[] = Array.isArray(preview?.items) ? preview.items : [];

  return (
    <Modal
      open
      onClose={onCancel}
      eyebrow="Autorização necessária"
      title={action ?? message}
      width="narrow"
      footer={
        <>
          <Button onClick={onCancel}>Recusar</Button>
          <Button variant="primary" onClick={onConfirm}>Autorizar</Button>
        </>
      }
    >
      <dl className="kv">
        {target && (
          <>
            <dt>Sobre</dt>
            <dd>{target}</dd>
          </>
        )}
        {scope && (
          <>
            <dt>Alcance</dt>
            <dd>{scope}</dd>
          </>
        )}
        <dt>Ferramenta</dt>
        <dd className="mono">{tool}</dd>
      </dl>

      {/* The size of the decision, when the target alone does not show it. */}
      {(preview?.note || previewItems.length > 0) && (
        <div style={{ marginTop: 16 }}>
          <div className="cc-tile-label">O que vai ser afetado</div>
          <div className="perm-args">
            {preview?.note && <div><strong>{preview.note}</strong></div>}
            {previewItems.map((item, index) => (
              <div key={`${item}-${index}`}>{item}</div>
            ))}
          </div>
        </div>
      )}

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
