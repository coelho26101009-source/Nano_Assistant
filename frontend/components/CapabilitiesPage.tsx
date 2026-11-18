/**
 * Ferramentas → Capacidades. What Nano can do, in the user's terms.
 *
 * The page answers one question — "what can this thing actually do for me?" —
 * and every row on it is read from the live executor registry through
 * `get_capability_catalogue`. There is no hand-maintained list here, so a
 * capability cannot appear on this page without existing, and cannot exist
 * without appearing.
 *
 * THREE STATUSES, AND WHY THE THIRD ONE IS SHOWN AT ALL.
 *
 *   Disponível   runs when asked
 *   Pede confirmação  asks first, every time
 *   Indisponível     does not exist, and no confirmation creates it
 *
 * The third is the lesson from the V2 retest: asked to run PowerShell, Nano
 * offered a confirmation prompt, which told the user their Yes was the only
 * thing missing. Listing what Nano genuinely cannot do, beside what it can, is
 * more useful than an honest silence — and it is the same declaration the
 * model is grounded on (`core/capabilities.py`), not a second copy.
 *
 * No schemas, no arguments, no tool JSON. The tool NAME is shown only inside
 * the per-row disclosure, for the user who wants to correlate an action with
 * an audit-log entry.
 */
import React, { useMemo, useState } from "react";

import { useFetch } from "../lib/backend";
import { Badge, EmptyState, Panel, Skeleton } from "./ui";
import { t } from "../lib/i18n";

export type CapabilityRow = {
  tool: string;
  description: string;
  capability: string | null;
  risk: string;
  status: "available" | "confirm" | "unsupported";
  alternatives?: string[];
};

export type CapabilityCategory = {
  id: string;
  label: string;
  hint: string;
  capabilities: CapabilityRow[];
};

export type CapabilityCatalogue = {
  categories: CapabilityCategory[];
  unsupported: CapabilityRow[];
  totals: { available: number; confirm: number; unsupported: number; capabilities: number };
};

const STATUS_LABEL: Record<CapabilityRow["status"], string> = {
  available: t("Disponível"),
  confirm: t("Pede confirmação"),
  unsupported: t("Indisponível"),
};

/** Badge tones, from the existing Ember palette -- no new colours. "confirm"
 *  uses the informational tone rather than a warning red: asking first is
 *  normal behaviour, not a problem. */
const STATUS_TONE: Record<CapabilityRow["status"], "accent" | "info" | "neutral"> = {
  available: "accent",
  confirm: "info",
  unsupported: "neutral",
};

/**
 * What a person would actually SAY to use each group, one click from the chat.
 * Each example is a request a narrow tool in that group fulfils; a group with
 * no entry here (a plugin's "Outras integrações") simply shows none.
 */
const EXAMPLES: Record<string, string[]> = {
  apps: [t("Abre o Spotify"), t("Que aplicações tenho abertas?")],
  windows: [t("Põe as janelas lado a lado"), t("Minimiza todas as janelas do Discord")],
  audio: [t("Baixa o volume para 30"), t("Pausa a música")],
  files: [t("Procura o meu currículo nos Documentos"), t("Arruma a pasta Transferências")],
  web: [t("Pesquisa o tempo em Lisboa"), t("Abre o YouTube")],
  display: [t("Baixa o brilho do monitor"), t("Tira uma captura de ecrã")],
  input: [t("Copia este texto: olá mundo")],
  system: [t("Quanto espaço livre tenho no disco?"), t("Estou ligado à internet?")],
  memory: [t("Lembra-te que prefiro respostas curtas"), t("O que sabes sobre mim?")],
  schedule: [t("Lembra-me de fazer uma pausa daqui a 1 hora")],
  devices: [t("Envia uma notificação para o meu telemóvel")],
};

function CapabilityItem({ row }: { row: CapabilityRow }) {
  return (
    <li className="cap-item" data-status={row.status}>
      <div className="cap-item__head">
        <span className="cap-item__status" data-status={row.status} aria-hidden="true" />
        <p className="cap-item__desc">{row.description}</p>
        <Badge tone={STATUS_TONE[row.status]}>{STATUS_LABEL[row.status]}</Badge>
      </div>

      {row.alternatives && row.alternatives.length > 0 && (
        <ul className="cap-item__alts">
          {row.alternatives.map((alt) => <li key={alt}>{alt}</li>)}
        </ul>
      )}

      {/* The technical identity, behind a disclosure. It is what appears in the
          audit log and in a permission card, so it has to be findable — but it
          is not what the page is for. */}
      {row.capability && (
        <details className="cap-item__more">
          <summary>{t("Detalhes técnicos")}</summary>
          <dl className="kv">
            <dt>{t("Ferramenta")}</dt><dd className="mono">{row.tool}</dd>
            <dt>{t("Capability")}</dt><dd className="mono">{row.capability}</dd>
            <dt>{t("Risco")}</dt><dd>{row.risk}</dd>
          </dl>
        </details>
      )}
    </li>
  );
}

export default function CapabilitiesPage({ enabled, onTry }: {
  enabled: boolean;
  /** Send an example to the chat. */
  onTry?: (text: string) => void;
}) {
  const { data, loading } = useFetch<CapabilityCatalogue>("get_capability_catalogue", enabled);
  const [filter, setFilter] = useState<"all" | "available" | "confirm">("all");

  const categories = useMemo(() => {
    if (!data?.categories) return [];
    if (filter === "all") return data.categories;
    return data.categories
      .map((group) => ({ ...group, capabilities: group.capabilities.filter((c) => c.status === filter) }))
      .filter((group) => group.capabilities.length > 0);
  }, [data, filter]);

  if (loading && !data) {
    return (
      <div className="page__inner">
        <h2 className="page-title">{t("Capacidades")}</h2>
        <Skeleton height={80} /><div style={{ height: 10 }} /><Skeleton height={80} />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="page__inner">
        <EmptyState
          title={t("Não foi possível ler as capacidades")}
          hint={t("O motor do NANO não respondeu. Nada é mostrado por omissão para não inventar uma lista.")}
        />
      </div>
    );
  }

  return (
    <div className="page__inner">
      <h2 className="page-title">{t("Capacidades")}</h2>
      <p className="dim" style={{ marginBottom: 14, maxWidth: "72ch" }}>{t("Pede ao NANO por palavras tuas. Clica num exemplo para o experimentar. As ações sensíveis pedem sempre confirmação.")}</p>

      <div className="cap-filters" role="group" aria-label={t("Filtrar capacidades")}>
        {([
          { value: "all", label: t("Todas ({0})", { 0: data.totals.capabilities }) },
          { value: "available", label: t("Disponíveis ({0})", { 0: data.totals.available }) },
          { value: "confirm", label: t("Pedem confirmação ({0})", { 0: data.totals.confirm }) },
        ] as const).map((option) => (
          <button
            key={option.value}
            type="button"
            className="chip"
            aria-pressed={filter === option.value}
            onClick={() => setFilter(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="stack" style={{ marginTop: 14 }}>
        {categories.map((group) => (
          <Panel
            key={group.id}
            title={group.label}
            action={<Badge tone="neutral">{group.capabilities.length}</Badge>}
          >
            <p className="dim" style={{ fontSize: 13, marginBottom: 10 }}>{group.hint}</p>
            {onTry && (EXAMPLES[group.id] ?? []).length > 0 && (
              <div className="cap-examples" aria-label={t("Experimenta")}>
                {(EXAMPLES[group.id] ?? []).map((example) => (
                  <button key={example} type="button" className="suggestion" onClick={() => onTry(example)}>
                    {example}
                  </button>
                ))}
              </div>
            )}
            {/* The individual tools, for whoever wants them, behind a
                disclosure: the examples above are what the page is for. */}
            <details className="cap-group__tools">
              <summary>{t("Ver as {0} ações", { 0: group.capabilities.length })}</summary>
              <ul className="cap-list">
                {group.capabilities.map((row) => <CapabilityItem key={row.tool} row={row} />)}
              </ul>
            </details>
          </Panel>
        ))}

        {filter === "all" && data.unsupported.length > 0 && (
          <Panel
            title={t("O que o NANO não faz")}
            action={<Badge tone="neutral">{data.unsupported.length}</Badge>}
          >
            <p className="dim" style={{ fontSize: 12, marginBottom: 10 }}>{t("Estas capacidades não existem por decisão de arquitetura. Não são permissões por conceder.")}</p>
            <ul className="cap-list">
              {data.unsupported.map((row) => <CapabilityItem key={row.tool} row={row} />)}
            </ul>
          </Panel>
        )}
      </div>
    </div>
  );
}
