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
import { t } from "../lib/i18n";

interface ConfirmProps {
  message: string;
  meta: Record<string, any>;
  onConfirm: () => void;
  onCancel: () => void;
}

/** The facts of one decision, shared by the inline card and the dialog. */
function ConfirmBody({ meta }: { meta: Record<string, any> }) {
  const args = sanitizeArgs(meta?.args);
  const tool = meta?.tool ?? "—";
  const target: string | undefined = meta?.target;
  const scope: string | undefined = meta?.scope;
  const preview = meta?.preview ?? {};
  const previewItems: string[] = Array.isArray(preview?.items) ? preview.items : [];
  return (
    <>
      <dl className="kv">
        {target && (
          <>
            <dt>{t("Alvo")}</dt>
            <dd>{target}</dd>
          </>
        )}
        {scope && (
          <>
            <dt>{t("Alcance")}</dt>
            <dd>{scope}</dd>
          </>
        )}
        <dt>{t("Ferramenta")}</dt>
        <dd className="mono">{tool}</dd>
      </dl>

      {/* The size of the decision, when the target alone does not show it. */}
      {(preview?.note || previewItems.length > 0) && (
        <div style={{ marginTop: 16 }}>
          <div className="cc-tile-label">{t("O que vai ser afetado")}</div>
          <div className="perm-args">
            {preview?.note && <div><strong>{preview.note}</strong></div>}
            {previewItems.map((item, index) => (
              <div key={`${item}-${index}`}>{item}</div>
            ))}
          </div>
        </div>
      )}

      {args.length > 0 && (
        <details className="confirm-args" style={{ marginTop: 16 }}>
          <summary className="cc-tile-label">{t("Argumentos que vais autorizar")}</summary>
          <div className="perm-args">
            {args.map(({ key, value }) => (
              <div key={key}><strong>{key}</strong>: {value}</div>
            ))}
          </div>
        </details>
      )}

      <p className="dim" style={{ marginTop: 16, fontSize: 12 }}>{t("Autorizar aplica-se apenas a esta execução.")}</p>
    </>
  );
}

/**
 * The approval as a card in the conversation, where the request was made.
 * Used while the chat is open; the dialog below covers every other page, so a
 * request is never left waiting where the person cannot see it.
 */
export function ConfirmCard({ message, meta, onConfirm, onCancel }: ConfirmProps) {
  const ref = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    ref.current?.querySelector<HTMLButtonElement>(".confirm-card__deny")?.focus();
  }, []);
  return (
    <div ref={ref} className="confirm-card" role="alertdialog" aria-labelledby="confirm-card-title">
      <div className="confirm-card__eyebrow">{t("Autorização necessária")}</div>
      <h3 id="confirm-card-title" className="confirm-card__title">{meta?.action ?? message}</h3>
      <ConfirmBody meta={meta} />
      <div className="confirm-card__actions">
        <Button className="confirm-card__deny" onClick={onCancel}>{t("Recusar")}</Button>
        <Button variant="primary" onClick={onConfirm}>{t("Autorizar")}</Button>
      </div>
    </div>
  );
}

export default function ConfirmModal({ message, meta, onConfirm, onCancel }: ConfirmProps) {
  return (
    <Modal
      open
      onClose={onCancel}
      eyebrow={t("Autorização necessária")}
      title={meta?.action ?? message}
      width="narrow"
      footer={
        <>
          <Button onClick={onCancel}>{t("Recusar")}</Button>
          <Button variant="primary" onClick={onConfirm}>{t("Autorizar")}</Button>
        </>
      }
    >
      <ConfirmBody meta={meta} />
    </Modal>
  );
}
