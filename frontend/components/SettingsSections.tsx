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
import {
  Badge, Button, ConfirmDialog, MetricRow, Panel, StatusIndicator, Toggle,
} from "./ui";

/* ── PC Control ───────────────────────────────────────────────────────── */

/** Guarantees, not options. Each is a property of the pipeline, phrased as
 *  what it prevents rather than as what it is called internally. */
const GUARANTEES: { label: string; detail: string }[] = [
  {
    label: "Sem shell, terminal ou PowerShell",
    detail: "O Nano não executa comandos arbitrários. Não existe ferramenta para isso e nenhuma confirmação a cria.",
  },
  {
    label: "Locais protegidos",
    detail: "Ficheiros internos do Windows, Program Files, credenciais e perfis de browser nunca são escritos nem removidos.",
  },
  {
    label: "Autorização ligada ao alvo",
    detail: "Permitir fechar uma janela não permite fechar outra. Cada decisão vale para a capability, o alvo e o âmbito daquele pedido.",
  },
  {
    label: "Nada é apagado em definitivo",
    detail: "«Apagar» significa Reciclagem. É sempre recuperável.",
  },
  {
    label: "Sem autorização permanente",
    detail: "Nenhuma capability pode ser autorizada para sempre — no máximo, para a tarefa em curso.",
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
      <Panel title="Controlo do computador" action={<Badge tone="accent">Ativo</Badge>}>
        <p className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
          O Nano pode abrir aplicações, arrumar janelas, mexer no volume, procurar
          ficheiros e abrir páginas das Definições do Windows. Cada acção passa
          por uma ferramenta específica, com argumentos verificados — nunca por
          uma linha de comandos.
        </p>
        {catalogue && (
          <>
            <div style={{ height: 10 }} />
            <MetricRow label="Capacidades disponíveis" value={String(catalogue.totals.available)} />
            <MetricRow label="Pedem sempre confirmação" value={String(catalogue.totals.confirm)} />
          </>
        )}
        <div className="inline" style={{ marginTop: 12 }}>
          <Button size="sm" onClick={onOpenCapabilities}>Ver todas as capacidades</Button>
          <Button size="sm" onClick={onOpenPermissions}>Ver permissões deste PC</Button>
        </div>
      </Panel>

      <Panel title="Quando o Nano pergunta">
        <p className="muted" style={{ fontSize: 13, lineHeight: 1.7, marginBottom: 10 }}>
          Ações reversíveis e de leitura acontecem de imediato. Tudo o que fecha,
          escreve, apaga, captura o ecrã ou mexe na sessão pede confirmação —
          com a acção, o alvo e o alcance à vista antes de decidires.
        </p>
        <MetricRow label="Modo de autonomia" value={settings.security.autonomyMode} />
        <MetricRow
          label="Autorização permanente"
          value={<StatusIndicator state="DISABLED" label="desativada por desenho" />}
        />
      </Panel>

      <Panel title="Garantias de segurança">
        <p className="dim" style={{ fontSize: 12, marginBottom: 12, lineHeight: 1.6 }}>
          Isto não são preferências. Fazem parte da arquitetura do Nano e não podem
          ser desligadas — nem por ti, nem pelo modelo.
        </p>
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
      <Panel title="O que o Nano guarda">
        <Toggle
          label="Recordar factos sobre mim"
          hint="Preferências duradouras que o Nano aprende durante as conversas. Desligado, o Nano deixa de guardar e deixa de consultar."
          checked={Boolean(memory?.factsEnabled)}
          onChange={(value) => onUpdate("memory_facts_enabled", value)}
        />
        {/* TWO SWITCHES, BECAUSE THEY ARE TWO QUESTIONS.
            The first is whether Nano may carry anything at all from one
            conversation to the next. The second is whether it may decide by
            itself what to carry -- with it off, "lembra-te que..." still works,
            because that is the user asking rather than Nano guessing. */}
        <Toggle
          label="Memória entre conversas"
          hint="Deixa o Nano usar numa conversa o que aprendeu noutra. Desligado, cada conversa fica isolada e o Second Brain deixa de contribuir."
          checked={Boolean(memory?.longTermEnabled)}
          onChange={(value) => onUpdate("memory_long_term_enabled", value)}
        />
        <Toggle
          label="Sugerir memórias sozinho"
          hint="O Nano propõe guardar algo que reparou. As sugestões não são usadas nas respostas enquanto não as aprovares em Memória. Dizer “lembra-te que…” funciona sempre."
          checked={Boolean(memory?.captureEnabled)}
          disabled={!memory?.longTermEnabled}
          disabledReason="Precisa da memória entre conversas ligada."
          onChange={(value) => onUpdate("memory_auto_capture", value)}
        />
        {/* Document retrieval is not installed. It is HIDDEN rather than shown
            as a dead toggle -- an option that cannot do anything is worse than
            no option. */}
        {memory?.ragSupported && (
          <Toggle
            label="Consultar documentos indexados"
            hint="Usa excertos dos documentos indexados para responder."
            checked={Boolean(memory?.ragEnabled)}
            onChange={(value) => onUpdate("memory_rag_enabled", value)}
          />
        )}
      </Panel>

      <Panel title="Gerir a memória">
        <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
          O conteúdo guardado — ver, procurar, editar e apagar — vive na secção
          <strong> Memória</strong>, com as memórias, o Second Brain e o grafo.
          Aqui só se decide o comportamento.
        </p>
        {memory?.retrieval?.engine && (
          <p className="dim" style={{ fontSize: 11, marginBottom: 12, lineHeight: 1.6 }}>
            Pesquisa de memória: <strong>{memory.retrieval.engine}</strong>,
            {" "}{memory.retrieval.entries} entradas indexadas neste computador.
          </p>
        )}
        <div className="inline">
          <Button size="sm" onClick={onOpenMemory}>Abrir a Memória</Button>
          <Button size="sm" variant="danger" onClick={() => setConfirmForget(true)}>
            Esquecer tudo
          </Button>
        </div>
        <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
          «Esquecer tudo» apaga os factos guardados. Não apaga a conversa atual.
        </p>
      </Panel>

      {memory && !memory.ragSupported && (
        <Panel title="Documentos">
          <p className="dim" style={{ fontSize: 12, lineHeight: 1.6 }}>{memory.ragNote}</p>
        </Panel>
      )}

      <ConfirmDialog
        open={confirmForget} danger
        title="Esquecer tudo o que o Nano sabe sobre ti?"
        confirmLabel="Esquecer tudo"
        message="Todos os factos guardados são apagados. A conversa atual não é afetada."
        onConfirm={() => { setConfirmForget(false); onForgetAll(); }}
        onCancel={() => setConfirmForget(false)}
      />
    </div>
  );
}

/* ── Sobre ────────────────────────────────────────────────────────────── */

export function AboutSection({ settings }: { settings: SettingsPayload }) {
  const { data: dataLocation } = useFetch<any>("get_data_location", true);
  const runtime = settings.runtime ?? {};

  return (
    <div className="stack">
      <Panel title="Nano">
        <div className="about-hero">
          <div className="about-hero__mark" aria-hidden="true">
            <svg viewBox="0 0 24 32" width="34" height="45" aria-hidden="true">
              <path
                d="M12 1c3.6 5.2 9 8.6 9 15.2C21 24 17 31 12 31S3 24 3 16.2C3 9.6 8.4 6.2 12 1Z"
                fill="var(--accent)" opacity="0.92"
              />
              <path
                d="M12 12c1.7 2.6 4 4.3 4 7.4 0 3.6-1.9 6.8-4 6.8s-4-3.2-4-6.8c0-3.1 2.3-4.8 4-7.4Z"
                fill="var(--bg-1)" opacity="0.85"
              />
            </svg>
          </div>
          <div>
            <h3 className="about-hero__name">{VERSION.name}</h3>
            <p className="about-hero__version">{VERSION.display}</p>
            <p className="dim" style={{ fontSize: 12, marginTop: 4 }}>
              Assistente executivo para Windows, com voz e controlo do computador.
            </p>
          </div>
        </div>
      </Panel>

      <Panel title="Versão">
        <MetricRow label="Produto" value={VERSION.product} />
        <MetricRow label="Canal" value={VERSION.channel} />
        <MetricRow label="Motor (Python)" value={runtime.version ?? "—"} />
        <p className="dim" style={{ fontSize: 11, marginTop: 8, lineHeight: 1.6 }}>
          A interface e o motor leem a mesma versão do ficheiro <code>version.json</code>,
          para que nunca possam discordar sobre que versão está a correr.
        </p>
      </Panel>

      <Panel title="Ambiente">
        <MetricRow label="Python" value={runtime.python ?? "—"} />
        <MetricRow label="Plataforma" value={runtime.platform ?? "—"} />
        <MetricRow label="RAM total" value={runtime.ramTotalGb ? `${runtime.ramTotalGb} GB` : "—"} />
        <MetricRow label="Modelo local recomendado" value={runtime.recommendedLocalModel ?? "—"} />
        {dataLocation?.data_dir && (
          <MetricRow label="Dados" value={<code className="mono">{dataLocation.data_dir}</code>} />
        )}
      </Panel>

      <Panel title="Projeto">
        <MetricRow
          label="Código"
          value={<code className="mono">github.com/coelho26101009-source/Nano_Assistant</code>}
        />
        <p className="dim" style={{ fontSize: 11, marginTop: 8, lineHeight: 1.6 }}>
          A documentação de arquitetura e a política de segurança acompanham o
          código, em <code>docs/</code>.
        </p>
      </Panel>
    </div>
  );
}
