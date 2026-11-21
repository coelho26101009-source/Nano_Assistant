/**
 * The four Settings sections that did not exist before Settings V2.
 *
 * They live beside `SettingsPage` rather than inside it because that file was
 * already the largest component in the app, and because these four share one
 * property the older sections do not: each is mostly a HONEST DESCRIPTION of
 * something the architecture guarantees, with a small number of real controls
 * embedded in it.
 *
 * The distinction matters most in `PcControlSection`. Nano's protections — no
 * arbitrary shell, protected paths, target-bound grants, confirmation on
 * destructive actions — are not preferences, and rendering them as switches
 * would imply they can be turned off. They are shown as STATUS, with the same
 * visual weight a setting would get and none of the affordance.
 */
import React, { useState } from "react";

import type { SettingsPayload } from "../lib/backend";
import { useFetch } from "../lib/backend";
import type { CapabilityCatalogue } from "./CapabilitiesPage";
import { VERSION } from "../lib/version";
import DiagnosticsPanel from "./DiagnosticsPanel";
import UpdatesPanel from "./UpdatesPanel";
import NanoLogo, { NanoWordmark } from "./NanoLogo";
import { BRAND_NAME } from "../lib/brand";
import {
  Badge, Button, ConfirmDialog, MetricRow, Modal, Panel, StatusIndicator, Toggle,
} from "./ui";
import { t } from "../lib/i18n";
import { tj } from "../lib/tj";

/* ── PC Control ───────────────────────────────────────────────────────── */

/** Guarantees, not options. Each is a property of the pipeline, phrased as
 *  what it prevents rather than as what it is called internally. */
const GUARANTEES: { label: string; detail: string }[] = [
  {
    label: t("Sem shell, terminal ou PowerShell"),
    detail: t("O NANO não executa comandos arbitrários. Não existe ferramenta para isso e nenhuma confirmação a cria."),
  },
  {
    label: t("Locais protegidos"),
    detail: t("Ficheiros internos do Windows, Program Files, credenciais e perfis de browser nunca são escritos nem removidos."),
  },
  {
    label: t("Autorização ligada ao alvo"),
    detail: t("Permitir fechar uma janela não permite fechar outra. Cada decisão vale para a capability, o alvo e o âmbito daquele pedido."),
  },
  {
    label: t("Nada é apagado em definitivo"),
    detail: t("«Apagar» significa Reciclagem. É sempre recuperável."),
  },
  {
    label: t("Sem autorização permanente"),
    detail: t("Nenhuma capability pode ser autorizada para sempre — no máximo, para a tarefa em curso."),
  },
];

export function PcControlSection({
  settings, enabled, onOpenPermissions, onOpenCapabilities,
}: {
  settings: SettingsPayload;
  enabled: boolean;
  onOpenPermissions: () => void;
  onOpenCapabilities: () => void;
}) {
  const { data: catalogue } = useFetch<CapabilityCatalogue>("get_capability_catalogue", enabled);

  return (
    <div className="stack">
      <Panel title={t("Controlo do computador")} action={<Badge tone="accent">{t("Ativo")}</Badge>}>
        <p className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>{t("O NANO pode abrir aplicações, arrumar janelas, mexer no volume, procurar ficheiros e abrir páginas das Definições do Windows. Cada ação passa por uma ferramenta específica, com argumentos verificados — nunca por uma linha de comandos.")}</p>
        {catalogue && (
          <>
            <div style={{ height: 10 }} />
            <MetricRow label={t("Capacidades disponíveis")} value={String(catalogue.totals.available)} />
            <MetricRow label={t("Pedem sempre confirmação")} value={String(catalogue.totals.confirm)} />
          </>
        )}
        <div className="inline" style={{ marginTop: 12 }}>
          <Button size="sm" onClick={onOpenCapabilities}>{t("Ver todas as capacidades")}</Button>
          <Button size="sm" onClick={onOpenPermissions}>{t("Ver permissões deste PC")}</Button>
        </div>
      </Panel>

      <Panel title={t("Quando o NANO pergunta")}>
        <p className="muted" style={{ fontSize: 13, lineHeight: 1.7, marginBottom: 10 }}>{t("Ações reversíveis e de leitura acontecem de imediato. Tudo o que fecha, escreve, apaga, captura o ecrã ou mexe na sessão pede confirmação — com a ação, o alvo e o alcance à vista antes de decidires.")}</p>
        <MetricRow label={t("Modo de autonomia")} value={settings.security.autonomyMode} />
        <MetricRow
          label={t("Autorização permanente")}
          value={<StatusIndicator state="DISABLED" label={t("desativada por desenho")} />}
        />
      </Panel>

      <Panel title={t("Garantias de segurança")}>
        <p className="dim" style={{ fontSize: 12, marginBottom: 12, lineHeight: 1.6 }}>{t("Isto não são preferências. Fazem parte da arquitetura do NANO e não podem ser desligadas — nem por ti, nem pelo modelo.")}</p>
        <ul className="guarantee-list">
          {GUARANTEES.map((item) => (
            <li key={item.label} className="guarantee">
              <span className="guarantee__mark" aria-hidden="true">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              </span>
              <span className="guarantee__body">
                <span className="guarantee__label">{item.label}</span>
                <span className="guarantee__detail">{item.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}

/* ── Memória ──────────────────────────────────────────────────────────── */

export function MemorySection({
  settings, onUpdate, onOpenMemory, onForgetAll,
}: {
  settings: SettingsPayload;
  onUpdate: (key: string, value: any) => void;
  onOpenMemory: () => void;
  onForgetAll: () => void;
}) {
  const [confirmForget, setConfirmForget] = useState(false);
  const memory = settings.memory;

  return (
    <div className="stack">
      <Panel title={t("O que o NANO guarda")}>
        <Toggle
          label={t("Recordar factos sobre mim")}
          hint={t("Preferências duradouras que o NANO aprende durante as conversas. Desligado, o NANO deixa de guardar e deixa de consultar.")}
          checked={Boolean(memory?.factsEnabled)}
          onChange={(value) => onUpdate("memory_facts_enabled", value)}
        />
        {/* TWO SWITCHES, BECAUSE THEY ARE TWO QUESTIONS.
            The first is whether Nano may carry anything at all from one
            conversation to the next. The second is whether it may decide by
            itself what to carry -- with it off, "lembra-te que..." still works,
            because that is the user asking rather than Nano guessing. */}
        <Toggle
          label={t("Memória entre conversas")}
          hint={t("Deixa o NANO usar numa conversa o que aprendeu noutra. Desligado, cada conversa fica isolada e o Second Brain deixa de contribuir.")}
          checked={Boolean(memory?.longTermEnabled)}
          onChange={(value) => onUpdate("memory_long_term_enabled", value)}
        />
        <Toggle
          label={t("Sugerir memórias sozinho")}
          hint={t("O NANO propõe guardar algo que reparou. As sugestões não são usadas nas respostas enquanto não as aprovares em Memória. Dizer “lembra-te que…” funciona sempre.")}
          checked={Boolean(memory?.captureEnabled)}
          disabled={!memory?.longTermEnabled}
          disabledReason={t("Precisa da memória entre conversas ligada.")}
          onChange={(value) => onUpdate("memory_auto_capture", value)}
        />
        {/* Memory M3. Under the cross-conversation switch because it IS
            cross-conversation memory: with that one off, nothing is recalled. */}
        <Toggle
          label={t("Lembrar conversas antigas")}
          hint={t("Quando falas de algo que já foi conversado, o NANO lembra-se do resumo e das decisões dessa conversa e diz de onde e de quando vem. Vê, edita ou esquece cada uma em Memória → Conversas antigas.")}
          checked={memory?.recallEnabled !== false}
          disabled={!memory?.longTermEnabled}
          disabledReason={t("Precisa da memória entre conversas ligada.")}
          onChange={(value) => onUpdate("memory_recall_enabled", value)}
        />
        {/* Document retrieval is not installed. It is HIDDEN rather than shown
            as a dead toggle -- an option that cannot do anything is worse than
            no option. */}
        {memory?.ragSupported && (
          <Toggle
            label={t("Consultar documentos indexados")}
            hint={t("Usa excertos dos documentos indexados para responder.")}
            checked={Boolean(memory?.ragEnabled)}
            onChange={(value) => onUpdate("memory_rag_enabled", value)}
          />
        )}
      </Panel>

      <Panel title={t("Gerir a memória")}>
        <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>{tj("O conteúdo guardado — ver, procurar, editar e apagar — vive na secção {0}, com as memórias, o Second Brain e o grafo. Aqui só se decide o comportamento.", { 0: <strong>{t("Memória")}</strong> })}</p>
        {memory?.retrieval?.engine && (
          <p className="dim" style={{ fontSize: 11, marginBottom: 12, lineHeight: 1.6 }}>{tj("Pesquisa de memória: {0}, {1} entradas indexadas neste computador.", { 0: <strong>{memory.retrieval.engine}</strong>, 1: memory.retrieval.entries })}</p>
        )}
        <div className="inline">
          <Button size="sm" onClick={onOpenMemory}>{t("Abrir a Memória")}</Button>
          <Button size="sm" variant="danger" onClick={() => setConfirmForget(true)}>{t("Esquecer tudo")}</Button>
        </div>
        <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>{t("«Esquecer tudo» apaga os factos guardados. Não apaga a conversa atual.")}</p>
      </Panel>

      {memory && !memory.ragSupported && (
        <Panel title={t("Documentos")}>
          <p className="dim" style={{ fontSize: 12, lineHeight: 1.6 }}>{memory.ragNote}</p>
        </Panel>
      )}

      <ConfirmDialog
        open={confirmForget} danger
        title={t("Esquecer tudo o que o NANO sabe sobre ti?")}
        confirmLabel={t("Esquecer tudo")}
        message={t("Todos os factos guardados são apagados. A conversa atual não é afetada.")}
        onConfirm={() => { setConfirmForget(false); onForgetAll(); }}
        onCancel={() => setConfirmForget(false)}
      />
    </div>
  );
}

/* ── Apagar todos os dados ────────────────────────────────────────────── */

/** What the user types. The backend checks the same phrase (core/main.py
 *  ERASE_ALL_CONFIRMATION), so this is not the only gate. */
export const ERASE_ALL_PHRASE = t("APAGAR TUDO");

/**
 * The one-click erase, with the decision laid out before it is made.
 *
 * "One click" is the ENTRY point, not the confirmation. An erase that cannot be
 * undone gets a typed phrase rather than a second button: a second button is
 * pressed by the same reflex as the first, typing two words is not. The dialog
 * says both what goes and what stays, because "apagar tudo" that silently keeps
 * the API keys -- or silently deletes them -- would each be a surprise.
 */
export function EraseAllDataPanel({ onErase }: {
  onErase: (confirmation: string) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const matches = typed.trim() === ERASE_ALL_PHRASE;

  const close = () => { if (!busy) { setOpen(false); setTyped(""); } };
  const confirm = async () => {
    if (!matches || busy) return;
    setBusy(true);
    try {
      const ok = await onErase(typed.trim());
      if (ok) { setOpen(false); setTyped(""); }
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Panel title={t("Apagar todos os dados")}>
        <Button variant="danger" onClick={() => setOpen(true)}>{t("Apagar todos os dados")}</Button>
        <p className="dim" style={{ fontSize: 11, marginTop: 8, lineHeight: 1.6 }}>{t("Apaga de uma vez as conversas, as memórias, o Second Brain e o histórico de tarefas deste computador. As definições e as chaves de API ficam.")}</p>
      </Panel>

      <Modal
        open={open} onClose={close} width="narrow"
        eyebrow={t("Não pode ser desfeito")}
        title={t("Apagar todos os dados?")}
        footer={
          <>
            <Button onClick={close} disabled={busy}>{t("Cancelar")}</Button>
            <Button variant="danger" onClick={confirm} disabled={!matches || busy}>
              {busy ? t("A apagar…") : t("Apagar tudo")}
            </Button>
          </>
        }
      >
        <div className="cc-tile-label">{t("É apagado")}</div>
        <ul className="muted erase-list">
          <li>{t("Todas as conversas e mensagens")}</li>
          <li>{t("Todas as memórias e factos guardados")}</li>
          <li>{t("O Second Brain, incluindo os nós criados à mão")}</li>
          <li>{t("Lembretes e eventos do calendário")}</li>
          <li>{t("O histórico de tarefas terminadas")}</li>
        </ul>
        <div className="cc-tile-label" style={{ marginTop: 12 }}>{t("Fica")}</div>
        <ul className="muted erase-list">
          <li>{t("Definições, chaves de API e contas ligadas")}</li>
          <li>{t("Permissões e o registo de autorizações")}</li>
          <li>{tj("O ficheiro de registos ({0})", { 0: <code>logs/nano.log</code> })}</li>
        </ul>
        <form
          className="field" style={{ marginTop: 16 }}
          onSubmit={(event) => { event.preventDefault(); confirm(); }}
        >
          <label className="field__label" htmlFor="erase-all-confirm">{tj("Escreve {0} para confirmar", { 0: <strong>{ERASE_ALL_PHRASE}</strong> })}</label>
          <input
            id="erase-all-confirm" className="input" type="text"
            value={typed} onChange={(event) => setTyped(event.target.value)}
            autoComplete="off" spellCheck={false} disabled={busy}
          />
        </form>
      </Modal>
    </>
  );
}

/* ── Sobre ────────────────────────────────────────────────────────────── */

export function AboutSection({ settings, onOpenFirstRun }: {
  settings: SettingsPayload; onOpenFirstRun: () => void;
}) {
  const { data: dataLocation } = useFetch<any>("get_data_location", true);
  const runtime = settings.runtime ?? {};

  return (
    <div className="stack">
      <Panel title={BRAND_NAME}>
        <div className="about-hero">
          {/* The symbol and the lettering, in the theme's own colours. The
              product name comes from lib/brand, not from version.json, whose
              `name` field ("Nano Assistant") is shared with the backend. */}
          <div className="about-hero__mark" aria-hidden="true">
            <NanoLogo size={40} />
          </div>
          <div>
            <h3 className="about-hero__name"><NanoWordmark height={20} /></h3>
            <p className="about-hero__version">{VERSION.display}</p>
            <p className="dim" style={{ fontSize: 12, marginTop: 4 }}>{t("Assistente executivo para Windows, com voz e controlo do computador.")}</p>
          </div>
        </div>
      </Panel>

      <Panel title={t("Versão")}>
        <MetricRow label={t("Produto")} value={VERSION.product} />
        <MetricRow label={t("Canal")} value={VERSION.channel} />
        <MetricRow label={t("Motor (Python)")} value={runtime.version ?? "—"} />
        <Button size="sm" onClick={onOpenFirstRun}>{t("Abrir guia inicial")}</Button>
        <p className="dim" style={{ fontSize: 11, marginTop: 8, lineHeight: 1.6 }}>{tj("A interface e o motor leem a mesma versão do ficheiro {0}, para que nunca possam discordar sobre que versão está a correr.", { 0: <code>version.json</code> })}</p>
      </Panel>

      <UpdatesPanel />

      <Panel title={t("Ambiente")}>
        <MetricRow label={t("Python")} value={runtime.python ?? "—"} />
        <MetricRow label={t("Plataforma")} value={runtime.platform ?? "—"} />
        <MetricRow label={t("RAM total")} value={runtime.ramTotalGb ? `${runtime.ramTotalGb} GB` : "—"} />
        <MetricRow label={t("Modelo local recomendado")} value={runtime.recommendedLocalModel ?? "—"} />
        {dataLocation?.data_dir && (
          <MetricRow label={t("Dados")} value={<code className="mono">{dataLocation.data_dir}</code>} />
        )}
      </Panel>

      <Panel title={t("Projeto")}>
        <MetricRow
          label={t("Código")}
          value={<code className="mono">github.com/coelho26101009-source/Nano_Assistant</code>}
        />
        <p className="dim" style={{ fontSize: 11, marginTop: 8, lineHeight: 1.6 }}>{tj("A documentação de arquitetura e a política de segurança acompanham o código, em {0}.", { 0: <code>docs/</code> })}
        </p>
      </Panel>
      <DiagnosticsPanel />
    </div>
  );
}
