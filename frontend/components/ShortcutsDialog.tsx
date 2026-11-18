/**
 * Every keyboard shortcut, in one list. Opened with "?" (outside a text
 * field), Ctrl+/, or from the command palette. The key handler lives in
 * pages/index.tsx; tests/test_shortcuts.py checks that every Ctrl shortcut
 * listed here has a matching branch there.
 */
import React from "react";
import { Modal } from "./ui";
import { t } from "../lib/i18n";

export type Shortcut = { keys: string[]; label: string };

export const SHORTCUTS: Shortcut[] = [
  { keys: ["Ctrl", "Shift", "O"], label: t("Nova conversa") },
  { keys: ["Ctrl", "K"], label: t("Paleta de comandos") },
  { keys: ["Ctrl", "B"], label: t("Mostrar/ocultar barra lateral") },
  { keys: ["Ctrl", "M"], label: t("Falar com o NANO") },
  { keys: ["Ctrl", "Shift", "M"], label: t("Modo de voz") },
  { keys: ["Ctrl", ","], label: t("Definições") },
  { keys: ["Enter"], label: t("Enviar mensagem") },
  { keys: ["Shift", "Enter"], label: t("Nova linha") },
  { keys: ["Esc"], label: t("Fechar janelas e menus") },
  { keys: ["?"], label: t("Mostrar os atalhos") },
];

export default function ShortcutsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title={t("Atalhos de teclado")} width="narrow">
      <dl className="shortcuts">
        {SHORTCUTS.map((shortcut) => (
          <React.Fragment key={shortcut.label}>
            <dt>{shortcut.label}</dt>
            <dd>
              {shortcut.keys.map((key, index) => (
                <React.Fragment key={key}>
                  {index > 0 && <span className="shortcuts__plus" aria-hidden="true">+</span>}
                  <kbd>{key}</kbd>
                </React.Fragment>
              ))}
            </dd>
          </React.Fragment>
        ))}
      </dl>
    </Modal>
  );
}
