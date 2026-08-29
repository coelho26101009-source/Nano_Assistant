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
  available: "Disponível",
  confirm: "Pede confirmação",
  unsupported: "Indisponível",
};

/** Badge tones, from the existing Ember palette -- no new colours. "confirm"
 *  uses the informational tone rather than a warning red: asking first is
 *  normal behaviour, not a problem. */
const STATUS_TONE: Record<CapabilityRow["status"], "accent" | "info" | "neutral"> = {
  available: "accent",
  confirm: "info",
  unsupported: "neutral",
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
          <summary>Detalhes técnicos</summary>
          <dl className="kv">
            <dt>Ferramenta</dt><dd className="mono">{row.tool}</dd>
            <dt>Capability</dt><dd className="mono">{row.capability}</dd>
            <dt>Risco</dt><dd>{row.risk}</dd>
          </dl>
        </details>
      )}
    </li>
  );
}

export default function CapabilitiesPage({ enabled }: { enabled: boolean }) {
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
        <h2 className="page-title">Capacidades</h2>
        <Skeleton height={80} /><div style={{ height: 10 }} /><Skeleton height={80} />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="page__inner">
        <EmptyState
          title="Não foi possível ler as capacidades"
          hint="O motor do Nano não respondeu. Nada é mostrado por omissão para não inventar uma lista."
        />
      </div>
    );
  }

  return (
    <div className="page__inner">
      <h2 className="page-title">Capacidades</h2>
      <p className="dim" style={{ marginBottom: 14, maxWidth: "72ch" }}>
        Tudo o que o Nano sabe fazer neste computador, lido diretamente do motor.
        As acções sensíveis pedem sempre confirmação — e as que o Nano não faz
        estão listadas também, porque nenhuma autorização as torna possíveis.
      </p>

      <div className="cap-filters" role="group" aria-label="Filtrar capacidades">
        {([
          { value: "all", label: `Todas (${data.totals.capabilities})` },
          { value: "available", label: `Disponíveis (${data.totals.available})` },
          { value: "confirm", label: `Pedem confirmação (${data.totals.confirm})` },
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
            <p className="dim" style={{ fontSize: 12, marginBottom: 10 }}>{group.hint}</p>
            <ul className="cap-list">
              {group.capabilities.map((row) => <CapabilityItem key={row.tool} row={row} />)}
            </ul>
          </Panel>
        ))}

        {filter === "all" && data.unsupported.length > 0 && (
          <Panel
            title="O que o Nano não faz"
            action={<Badge tone="neutral">{data.unsupported.length}</Badge>}
          >
            <p className="dim" style={{ fontSize: 12, marginBottom: 10 }}>
              Estas capacidades não existem por decisão de arquitetura. Não são
              permissões por conceder.
            </p>
            <ul className="cap-list">
              {data.unsupported.map((row) => <CapabilityItem key={row.tool} row={row} />)}
            </ul>
          </Panel>
        )}
      </div>
    </div>
  );
}
