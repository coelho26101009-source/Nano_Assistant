/**
 * PC -> Modos: prepare the computer for an activity in one click.
 *
 * A mode is a list of ordinary PC Control actions (close apps, open apps and
 * sites, set the volume, open a Settings page). The page shows every step
 * BEFORE anything runs, and "Ativar" runs them one by one through the normal
 * permission pipeline: closing apps still asks each time and never forces.
 * The result lists what each step really did.
 */
import React, { useCallback, useEffect, useState } from "react";
import { call } from "../lib/backend";
import { Badge, Button, EmptyState, Field, Panel, Skeleton } from "./ui";
import { t } from "../lib/i18n";
import { tj } from "../lib/tj";

export type WorkModeStep = { tool: string; label: string; asks?: boolean };
export type WorkMode = {
  id: string; name: string; description?: string; error?: string; steps: WorkModeStep[];
};
export type WorkModeOutcome = {
  label: string; tool: string; ok: boolean; status: "ok" | "skipped" | "denied" | "failed"; message?: string;
};
export type WorkModeResult = {
  ok: boolean; id?: string; name?: string; message?: string; done?: number; total?: number; skipped?: number;
  steps: WorkModeOutcome[];
};

const OUTCOME_LABEL: Record<WorkModeOutcome["status"], string> = {
  ok: t("feito"), skipped: t("já estava"), denied: t("recusado"), failed: t("não feito"),
};

/* ── Protocols (core/protocols.py) ───────────────────────────────────── */

export type Protocol = {
  id?: string; key?: string; name: string; phrases: string[];
  close_apps: string[]; open_apps: string[]; open_urls: string[];
  volume: number | null; media: string; settings_section: string; lock: boolean; message: string;
  steps?: WorkModeStep[];
};

const EMPTY_PROTOCOL: Protocol = {
  name: "", phrases: [], close_apps: [], open_apps: [], open_urls: [], volume: null,
  media: "", settings_section: "", lock: false, message: "",
};

const list = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
const joined = (items?: string[]) => (items ?? []).join(", ");

function ProtocolEditor({ initial, onSaved, onCancel }: {
  initial: Protocol; onSaved: () => void; onCancel: () => void;
}) {
  const [draft, setDraft] = useState(() => ({
    ...initial,
    phrasesText: joined(initial.phrases), openText: joined(initial.open_apps),
    closeText: joined(initial.close_apps), urlsText: joined(initial.open_urls),
    volumeText: initial.volume === null || initial.volume === undefined ? "" : String(initial.volume),
  }));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const set = (patch: Partial<typeof draft>) => setDraft((prev) => ({ ...prev, ...patch }));

  const save = async () => {
    setSaving(true);
    setError("");
    const volume = draft.volumeText.trim() === "" ? null : Number(draft.volumeText);
    const payload = {
      id: draft.id, name: draft.name, phrases: list(draft.phrasesText),
      open_apps: list(draft.openText), close_apps: list(draft.closeText), open_urls: list(draft.urlsText),
      volume: volume !== null && Number.isFinite(volume) ? Math.round(volume) : null,
      media: draft.media, settings_section: draft.settings_section, lock: draft.lock, message: draft.message,
    };
    const result = await call<{ ok: boolean; detail?: string }>("save_protocol", payload);
    setSaving(false);
    if (result?.ok) onSaved();
    else setError(result?.detail || t("Não consegui guardar o protocolo."));
  };

  return (
    <div className="card protocol-editor" style={{ padding: 16, marginBottom: 16 }}>
      <Field label={t("Nome")}>
        <input className="input" maxLength={60} value={draft.name} onChange={(e) => set({ name: e.target.value })}
               placeholder={t("Ex.: Modo estudo")} />
      </Field>
      <Field label={t("Frases que o ativam")} hint={t("Separadas por vírgulas. Dizes ou escreves a frase sozinha, por exemplo “modo estudo” ou “Ei NANO, boa noite”.")}>
        <input className="input" value={draft.phrasesText} onChange={(e) => set({ phrasesText: e.target.value })}
               placeholder={t("modo estudo, vou estudar")} />
      </Field>
      <Field label={t("Abrir aplicações")} hint={t("Nomes das aplicações, separados por vírgulas.")}>
        <input className="input" value={draft.openText} onChange={(e) => set({ openText: e.target.value })}
               placeholder={t("Spotify, Visual Studio Code")} />
      </Field>
      <Field label={t("Fechar aplicações")} hint={t("Pede sempre confirmação e nunca força o fecho.")}>
        <input className="input" value={draft.closeText} onChange={(e) => set({ closeText: e.target.value })}
               placeholder={t("discord, steam")} />
      </Field>
      <Field label={t("Abrir sites")} hint={t("Endereços http ou https, separados por vírgulas.")}>
        <input className="input" value={draft.urlsText} onChange={(e) => set({ urlsText: e.target.value })}
               placeholder={t("https://notion.so")} />
      </Field>
      <div className="inline" style={{ gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
        <Field label={t("Volume (0-100)")} hint={t("Vazio: não mexe.")}>
          <input className="input" type="number" min={0} max={100} value={draft.volumeText}
                 onChange={(e) => set({ volumeText: e.target.value })} style={{ maxWidth: 120 }} />
        </Field>
        <Field label={t("Música")}>
          <select className="select" value={draft.media} onChange={(e) => set({ media: e.target.value })}>
            <option value="">{t("Não mexer")}</option>
            <option value="play_pause">{t("Pausar ou retomar a música")}</option>
            <option value="stop">{t("Parar a música")}</option>
            <option value="next">{t("Faixa seguinte")}</option>
          </select>
        </Field>
      </div>
      <label className="inline" style={{ gap: 8, margin: "6px 0" }}>
        <input type="checkbox" checked={draft.settings_section === "notifications"}
               onChange={(e) => set({ settings_section: e.target.checked ? "notifications" : "" })} />
        {t("Abrir as definições de notificações (Não incomodar)")}
      </label>
      <label className="inline" style={{ gap: 8, margin: "6px 0" }}>
        <input type="checkbox" checked={draft.lock} onChange={(e) => set({ lock: e.target.checked })} />
        {t("Bloquear o PC no fim (pede confirmação)")}
      </label>
      <Field label={t("O que o NANO diz no fim")}>
        <input className="input" maxLength={160} value={draft.message} onChange={(e) => set({ message: e.target.value })}
               placeholder={t("Modo estudo ativo. Bom estudo!")} />
      </Field>
      {error && <p className="field__error" role="alert">{error}</p>}
      <div className="inline" style={{ marginTop: 8 }}>
        <Button variant="primary" size="sm" disabled={saving} onClick={() => { void save(); }}>
          {saving ? t("A guardar…") : t("Guardar protocolo")}
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>{t("Cancelar")}</Button>
      </div>
    </div>
  );
}

function ProtocolsSection({ enabled, busy, onRun }: {
  enabled: boolean; busy: boolean; onRun: (protocol: Protocol) => void;
}) {
  const [items, setItems] = useState<Protocol[] | null>(null);
  const [templates, setTemplates] = useState<Protocol[]>([]);
  const [editing, setEditing] = useState<Protocol | null>(null);

  const refresh = useCallback(async () => {
    const value = await call<{ ok: boolean; protocols: Protocol[]; templates: Protocol[] }>("get_protocols");
    setItems(value?.protocols ?? []);
    setTemplates(value?.templates ?? []);
  }, []);
  useEffect(() => { if (enabled) void refresh(); }, [enabled, refresh]);

  const remove = async (protocol: Protocol) => {
    await call("delete_protocol", protocol.id);
    void refresh();
  };

  return (
    <section style={{ marginBottom: 28 }}>
      <div className="inline" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>{t("Os teus protocolos")}</h3>
        {!editing && <Button size="sm" variant="primary" onClick={() => setEditing({ ...EMPTY_PROTOCOL })}>{t("Novo protocolo")}</Button>}
      </div>
      <p className="dim" style={{ marginBottom: 12, maxWidth: "72ch" }}>
        {t("Um protocolo é uma rotina tua que o NANO faz quando dizes uma frase, como o JARVIS: “modo estudo”, “boa noite”, “modo jogo”. Funciona a escrever, por voz e no telemóvel. Cada passo passa pelas permissões normais.")}
      </p>
      {editing && (
        <ProtocolEditor initial={editing} onCancel={() => setEditing(null)}
                        onSaved={() => { setEditing(null); void refresh(); }} />
      )}
      {!editing && templates.length > 0 && (
        <div className="inline" style={{ gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          <span className="dim" style={{ fontSize: 12 }}>{t("Começar de um modelo:")}</span>
          {templates.map((template) => (
            <Button key={template.key ?? template.name} size="sm" variant="ghost"
                    onClick={() => setEditing({ ...template, id: undefined })}>{template.name}</Button>
          ))}
        </div>
      )}
      {items && items.length === 0 && !editing && (
        <p className="muted" style={{ fontSize: 13 }}>{t("Ainda não tens protocolos. Cria um ou começa de um modelo.")}</p>
      )}
      {items && items.length > 0 && (
        <div className="grid-auto">
          {items.map((protocol) => (
            <Panel key={protocol.id} title={protocol.name} action={
              <Button variant="primary" size="sm" disabled={busy || !enabled}
                      aria-label={t("Executar {0}", { 0: protocol.name })}
                      onClick={() => onRun(protocol)}>{t("Executar")}</Button>
            }>
              <p className="dim" style={{ fontSize: 12, marginBottom: 8 }}>
                {t("Diz: {0}", { 0: protocol.phrases.map((phrase) => `“${phrase}”`).join(" · ") })}
              </p>
              <ol className="work-mode__steps">
                {(protocol.steps ?? []).map((step, index) => (
                  <li key={`${step.tool}-${index}`}>
                    {step.label}
                    {step.asks && <span className="dim"> · {t("pede confirmação")}</span>}
                  </li>
                ))}
                {protocol.message && <li>{t("Diz: {0}", { 0: `“${protocol.message}”` })}</li>}
              </ol>
              <div className="inline" style={{ marginTop: 10 }}>
                <Button size="sm" variant="ghost" onClick={() => setEditing(protocol)}>{t("Editar")}</Button>
                <Button size="sm" variant="ghost" onClick={() => { void remove(protocol); }}>{t("Apagar")}</Button>
              </div>
            </Panel>
          ))}
        </div>
      )}
    </section>
  );
}

export default function WorkModesPage({
  enabled, running, result, onActivate, onRunProtocol,
}: {
  enabled: boolean;
  /** The mode currently running, if any. */
  running: string | null;
  result: WorkModeResult | null;
  onActivate: (modeId: string) => void;
  /** Run one of the person's protocols (its result arrives like a mode's). */
  onRunProtocol?: (protocolId: string) => void;
}) {
  const [modes, setModes] = useState<WorkMode[] | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    const value = await call<{ ok: boolean; modes: WorkMode[] }>("get_work_modes");
    setModes(value?.modes ?? []);
    setLoading(false);
  }, []);

  useEffect(() => { if (enabled) refresh(); }, [enabled, refresh]);

  return (
    <div className="page__inner page__inner--wide">
      {onRunProtocol && (
        <ProtocolsSection enabled={enabled} busy={running !== null}
                          onRun={(protocol) => { if (protocol.id) onRunProtocol(protocol.id); }} />
      )}
      <h3 style={{ margin: "0 0 8px" }}>{t("Modos de trabalho")}</h3>
      <p className="dim" style={{ marginBottom: 16, maxWidth: "72ch" }}>
        {tj("Um modo prepara o PC para uma atividade. Vês todos os passos antes de ativar, e cada um passa pelas permissões normais: fechar aplicações pede sempre confirmação e nunca força o fecho. Os modos são ficheiros em {0}.", { 0: <code>config/modes</code> })}
      </p>

      {result && (
        <div className="card work-mode__result" role="status" aria-live="polite">
          <div className="inline" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <strong>{result.name ?? t("Modo")}</strong>
            <Badge tone={result.ok ? "info" : "neutral"}>{t("{0} de {1} passos", { 0: result.done ?? 0, 1: (result.total ?? 0) - (result.skipped ?? 0) })}</Badge>
          </div>
          {result.message && <p className="muted" style={{ marginBottom: 8 }}>{result.message}</p>}
          <ul className="work-mode__outcomes">
            {result.steps.map((step, index) => (
              <li key={`${step.tool}-${index}`} className={`work-mode__outcome work-mode__outcome--${step.status}`}>
                <span className="work-mode__outcome-state">{OUTCOME_LABEL[step.status] ?? step.status}</span>
                <span className="work-mode__outcome-label">{step.label}</span>
                {step.message && step.status !== "ok" && <span className="dim">· {step.message}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {loading && !modes ? (
        <div className="grid-auto"><Skeleton height={180} /><Skeleton height={180} /></div>
      ) : !modes?.length ? (
        <EmptyState title={t("Sem modos")} hint={t("Cria um ficheiro .yaml em config/modes para adicionar um modo.")} />
      ) : (
        <div className="grid-auto">
          {modes.map((mode) => (
            <Panel key={mode.id} title={mode.name} action={
              <Button variant="primary" size="sm"
                      disabled={!!mode.error || running !== null || !enabled}
                      aria-label={t("Ativar {0}", { 0: mode.name })}
                      onClick={() => onActivate(mode.id)}>
                {running === mode.id ? t("A ativar…") : t("Ativar")}
              </Button>
            }>
              {mode.description && <p className="muted" style={{ marginBottom: 10 }}>{mode.description}</p>}
              {mode.error ? (
                <p className="field__error">{mode.error}</p>
              ) : (
                <ol className="work-mode__steps" aria-label={t("Passos do {0}", { 0: mode.name })}>
                  {mode.steps.map((step, index) => (
                    <li key={`${step.tool}-${index}`}>
                      {step.label}
                      {step.asks && <span className="dim"> · {t("pede confirmação")}</span>}
                    </li>
                  ))}
                </ol>
              )}
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}
