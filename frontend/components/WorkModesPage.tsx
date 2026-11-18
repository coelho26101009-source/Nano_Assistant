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
import { Badge, Button, EmptyState, Panel, Skeleton } from "./ui";

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
  ok: "feito", skipped: "já estava", denied: "recusado", failed: "não feito",
};

export default function WorkModesPage({
  enabled, running, result, onActivate,
}: {
  enabled: boolean;
  /** The mode currently running, if any. */
  running: string | null;
  result: WorkModeResult | null;
  onActivate: (modeId: string) => void;
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
      <p className="dim" style={{ marginBottom: 16, maxWidth: "72ch" }}>
        Um modo prepara o PC para uma atividade. Vês todos os passos antes de ativar, e cada um
        passa pelas permissões normais: fechar aplicações pede sempre confirmação e nunca força
        o fecho. Os modos são ficheiros em <code>config/modes</code>.
      </p>

      {result && (
        <div className="card work-mode__result" role="status" aria-live="polite">
          <div className="inline" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <strong>{result.name ?? "Modo"}</strong>
            <Badge tone={result.ok ? "info" : "neutral"}>{result.done ?? 0} de {(result.total ?? 0) - (result.skipped ?? 0)} passos</Badge>
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
        <EmptyState title="Sem modos" hint="Cria um ficheiro .yaml em config/modes para adicionar um modo." />
      ) : (
        <div className="grid-auto">
          {modes.map((mode) => (
            <Panel key={mode.id} title={mode.name} action={
              <Button variant="primary" size="sm"
                      disabled={!!mode.error || running !== null || !enabled}
                      aria-label={`Ativar ${mode.name}`}
                      onClick={() => onActivate(mode.id)}>
                {running === mode.id ? "A ativar…" : "Ativar"}
              </Button>
            }>
              {mode.description && <p className="muted" style={{ marginBottom: 10 }}>{mode.description}</p>}
              {mode.error ? (
                <p className="field__error">{mode.error}</p>
              ) : (
                <ol className="work-mode__steps" aria-label={`Passos do ${mode.name}`}>
                  {mode.steps.map((step, index) => (
                    <li key={`${step.tool}-${index}`}>
                      {step.label}
                      {step.asks && <span className="dim"> · pede confirmação</span>}
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
