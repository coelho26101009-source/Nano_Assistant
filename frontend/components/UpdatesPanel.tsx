/**
 * Settings → About → Updates.
 *
 * Everything shown is the main process's own state (electron/lib/updater.js):
 * what it last found, how far a download got, whether an update is waiting to
 * install, and whether the last one really installed. The page can ask for a
 * check, switch automatic updates on or off and say "restart and update";
 * which installer runs is decided by the signed update feed alone.
 */
import React, { useState } from "react";
import {
  checkForUpdates, openUpdatePage, restartToUpdate, setAutoUpdate, useUpdateState,
} from "../lib/desktop";
import type { UpdateState } from "../lib/desktop";
import { Button, Panel, StatusIndicator, Toggle } from "./ui";
import { intlLocale, t } from "../lib/i18n";

/** A failure code from the updater, as one plain sentence. */
function errorText(code: string | null): string {
  switch (code) {
    case "offline":
    case "timeout":
      return t("Sem ligação à internet. O NANO tenta outra vez mais tarde.");
    case "no_disk_space":
      return t("Não há espaço livre suficiente para descarregar a atualização.");
    case "install_failed":
      return t("A atualização não ficou instalada. Os teus dados estão seguros. Tenta outra vez ou descarrega o instalador na página de versões.");
    case "feed_bad_signature":
    case "feed_unknown_key":
    case "feed_wrong_product":
    case "feed_bad_url":
    case "bad_redirect":
    case "download_bad_hash":
    case "download_too_large":
      return t("A atualização encontrada não passou na verificação de segurança e foi ignorada.");
    case "download_incomplete":
    case "download_stalled":
    case "download_failed":
      return t("A transferência da atualização falhou. O NANO tenta outra vez mais tarde.");
    default:
      return t("Não foi possível procurar atualizações. O NANO tenta outra vez mais tarde.");
  }
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString(intlLocale(), { dateStyle: "short", timeStyle: "short" });
  } catch {
    return "";
  }
}

function statusLine(state: UpdateState): { text: string; tone: "READY" | "WORKING" | "ERROR" | "DISABLED" } {
  const version = state.available?.version ?? "";
  switch (state.status) {
    case "checking":
      return { text: t("A procurar atualizações…"), tone: "WORKING" };
    case "downloading":
      return { text: t("A descarregar a versão {v}… {p}%", { v: version, p: Math.round(state.progress * 100) }), tone: "WORKING" };
    case "ready":
      return { text: t("A versão {v} está pronta. Fica instalada quando fechares o NANO.", { v: version }), tone: "READY" };
    case "available":
      return { text: t("Há uma versão nova: {v}.", { v: version }), tone: "READY" };
    case "error":
      return { text: errorText(state.error), tone: "ERROR" };
    case "up-to-date":
      return { text: t("Tens a versão mais recente."), tone: "READY" };
    default:
      return { text: state.enabled ? t("O NANO procura atualizações pouco depois de abrir.") : t("As atualizações automáticas estão desligadas."), tone: "DISABLED" };
  }
}

export default function UpdatesPanel() {
  const { state, setState, loading } = useUpdateState();
  const [busy, setBusy] = useState(false);

  if (loading) return null;

  if (!state) {
    return (
      <Panel title={t("Atualizações")}>
        <p className="muted" style={{ fontSize: 12 }}>{t("As atualizações automáticas existem na aplicação do PC. Aqui, atualiza o NANO com a versão nova da página de versões.")}</p>
      </Panel>
    );
  }

  const line = statusLine(state);
  const run = async (action: () => Promise<UpdateState | boolean | null>) => {
    setBusy(true);
    try {
      const result = await action();
      if (result && typeof result === "object") setState(result);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title={t("Atualizações")}>
      {state.lastResult?.ok && (
        <p className="muted" style={{ fontSize: 12, marginBottom: 8 }}>{t("Atualizado para a versão {v}.", { v: state.lastResult.version })}</p>
      )}
      <StatusIndicator state={line.tone} label={line.text} />
      {state.lastCheck && state.status !== "checking" && (
        <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>{t("Última verificação: {when}", { when: formatTime(state.lastCheck) })}</p>
      )}
      {state.mode === "source" && state.status === "available" && (
        <p className="dim" style={{ fontSize: 11, marginTop: 6, lineHeight: 1.6 }}>{t("Estás a usar o NANO a partir da pasta do projeto, por isso a versão nova não é instalada sozinha. Com o instalador do NANO, as atualizações instalam-se sozinhas.")}</p>
      )}

      <div className="inline" style={{ marginTop: 10, gap: 8, flexWrap: "wrap" }}>
        {state.status === "ready" ? (
          <Button size="sm" variant="primary" disabled={busy} onClick={() => run(restartToUpdate)}>{t("Reiniciar e atualizar")}</Button>
        ) : (
          <Button size="sm" disabled={busy || state.status === "checking" || state.status === "downloading"} onClick={() => run(checkForUpdates)}>{t("Procurar agora")}</Button>
        )}
        {(state.status === "available" || state.status === "error") && (
          <Button size="sm" variant="ghost" onClick={() => openUpdatePage()}>{t("Abrir a página de versões")}</Button>
        )}
      </div>

      <div style={{ height: 12 }} />
      <Toggle
        label={t("Atualizar automaticamente")}
        hint={t("O NANO procura uma versão nova no GitHub a cada 6 horas, descarrega-a em segundo plano e instala-a quando o fechares. Só aceita versões assinadas pelo projeto.")}
        checked={state.enabled}
        disabled={busy}
        onChange={(value) => run(async () => {
          const enabled = await setAutoUpdate(value);
          return enabled === null ? null : { ...state, enabled: Boolean(enabled) };
        })}
      />
    </Panel>
  );
}
