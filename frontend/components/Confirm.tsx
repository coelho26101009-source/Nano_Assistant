interface ConfirmProps {
  message:   string;
  meta:      Record<string, any>;
  onConfirm: () => void;
  onCancel:  () => void;
}

export default function ConfirmModal({ message, meta, onConfirm, onCancel }: ConfirmProps) {
  return (
    <div className="modal-overlay">
      <div className="modal-glass">
        <div className="modal-icon">⚠️</div>
        <h2 className="modal-title">Confirmação Necessária</h2>
        <p className="modal-message">{message}</p>
        {meta?.tool && (
          <div className="modal-meta">
            <span className="modal-meta-label">Ferramenta:</span>
            <code>{meta.tool}</code>
          </div>
        )}
        <div className="modal-actions">
          <button className="btn-cancel" onClick={onCancel}>
            Cancelar
          </button>
          <button className="btn-confirm" onClick={onConfirm}>
            Confirmar
          </button>
        </div>
      </div>
    </div>
  );
}
