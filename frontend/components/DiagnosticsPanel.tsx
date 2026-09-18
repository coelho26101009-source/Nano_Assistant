import React, { useState } from "react";
import { desktop, type DesktopStatus } from "../lib/desktop";
import { VERSION } from "../lib/version";
import { Button, Panel } from "./ui";

const ERROR_CODES = ["backend_start_failed", "backend_exited", "frontend_load_failed", "renderer_gone"];
const version = (value: unknown) => typeof value === "string" && value.length <= 64 && /^\d+\.\d+\.\d+(?:\.\d+)?(?:[-+][a-zA-Z0-9.-]+)?$/.test(value) ? value : null;

/** Explicit whitelist. Never serialize DesktopStatus itself: it also contains
 * a user path and shortcut error text, neither of which belongs in a report. */
export function safeDiagnostics(status: DesktopStatus | null) {
  return {
    schema: 1,
    version: VERSION.product,
    desktopVersion: version(status?.version),
    platform: ["win32", "linux", "darwin"].includes(status?.platform ?? "") ? status?.platform : null,
    arch: ["x64", "arm64", "ia32"].includes(status?.arch ?? "") ? status?.arch : null,
    packaged: typeof status?.packaged === "boolean" ? status.packaged : null,
    versions: { electron: version(status?.versions?.electron), chrome: version(status?.versions?.chrome) },
    backend: {
      running: typeof status?.backend?.running === "boolean" ? status.backend.running : null,
      lastExitCode: Number.isSafeInteger(status?.backend?.lastExitCode) ? status?.backend?.lastExitCode : null,
    },
    frontendReady: typeof status?.frontendReady === "boolean" ? status.frontendReady : null,
    lastErrorCode: ERROR_CODES.includes(status?.lastErrorCode ?? "") ? status?.lastErrorCode : null,
  };
}

export default function DiagnosticsPanel() {
  const [report, setReport] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const copy = async () => {
    setBusy(true);
    setMessage("");
    let status: DesktopStatus | null = null;
    try { status = await desktop()?.getDesktopStatus() ?? null; } catch { /* Unknown is reported honestly. */ }
    const text = JSON.stringify(safeDiagnostics(status), null, 2);
    setReport(text);
    try {
      await navigator.clipboard.writeText(text);
      setMessage("Diagnóstico copiado. Partilha-o apenas se quiseres.");
    } catch {
      setMessage("Não foi possível copiar automaticamente. Seleciona o relatório abaixo e copia-o.");
    } finally { setBusy(false); }
  };
  return (
    <Panel title="Diagnóstico local">
      <p className="muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
        Versão, sistema e estado do motor. Sem chaves, caminhos pessoais ou conteúdo das conversas. Nada é enviado automaticamente.
      </p>
      <Button size="sm" disabled={busy} onClick={copy}>{busy ? "A preparar…" : "Copiar diagnóstico"}</Button>
      <p role="status" className="dim" style={{ fontSize: 12 }}>{message}</p>
      {report && <textarea className="input diagnostics-report" aria-label="Relatório de diagnóstico"
        readOnly value={report} rows={10} onFocus={(event) => event.currentTarget.select()} />}
    </Panel>
  );
}
