/** What NANO remembers about you: the PC's long-term memory, searchable, editable. */
import { useCallback, useEffect, useState } from "react";
import Icon from "../components/Icon";
import { t } from "../lib/i18n";
import type { NanoLink } from "../lib/link";

type MemoryRow = { id: string; text: string; kind: string; pinned: boolean };

const KIND_LABEL: Record<string, string> = {};

function kindLabel(kind: string): string {
  if (!KIND_LABEL.fact) {
    Object.assign(KIND_LABEL, {
      fact: t("Facto"), preference: t("Preferência"), person: t("Pessoa"), project: t("Projeto"),
      goal: t("Objetivo"), decision: t("Decisão"), hardware: t("Equipamento"), software: t("Programas"),
      other: t("Outro"),
    });
  }
  return KIND_LABEL[kind] ?? kind;
}

export default function MemoryScreen({ link, online, toast }: { link: NanoLink; online: boolean; toast: (text: string) => void }) {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<MemoryRow[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async (q: string) => {
    try {
      const result = await link.call<{ ok: boolean; memories: MemoryRow[] }>("memory.list", { query: q });
      setRows(result.memories ?? []);
    } catch {
      setRows((prev) => prev ?? []);
    }
  }, [link]);

  useEffect(() => {
    const timer = setTimeout(() => void load(query), query ? 300 : 0);
    return () => clearTimeout(timer);
  }, [query, load]);

  const add = async () => {
    setError("");
    try {
      const result = await link.call<{ ok: boolean; error?: string; detail?: string; message?: string }>("memory.add", { text });
      if (!result.ok) {
        setError(result.detail || result.message || t("O NANO não guardou isto."));
        return;
      }
      setAdding(false);
      setText("");
      toast(t("Guardado na memória."));
      await load(query);
    } catch {
      setError(t("Sem ligação ao PC."));
    }
  };

  const remove = async (row: MemoryRow) => {
    try {
      // The PC answers {ok: false} when it could not forget it; the row stays
      // and the person is told, instead of "Esquecido." over a memory that is
      // still there.
      const result = await link.call<{ ok?: boolean }>("memory.delete", { id: row.id });
      if (result && result.ok === false) {
        toast(t("O NANO não conseguiu esquecer isto."));
        return;
      }
      setRows((prev) => (prev ?? []).filter((r) => r.id !== row.id));
      toast(t("Esquecido."));
    } catch {
      toast(t("Sem ligação ao PC."));
    }
  };

  return (
    <div className="page__inner" style={{ paddingBottom: 96 }}>
      <div className="search">
        <Icon name="search" size={20} />
        <label htmlFor="memory-search" className="sr-only">{t("Procurar na memória")}</label>
        <input id="memory-search" className="input" type="search" value={query} placeholder={t("Procurar na memória")}
          onChange={(e) => setQuery(e.target.value)} />
      </div>
      {rows === null && <div className="empty"><div className="spinner" style={{ margin: "0 auto" }} /></div>}
      {rows !== null && rows.length === 0 && (
        <div className="empty">
          <h2>{query ? t("Nada encontrado") : t("Ainda não há memórias")}</h2>
          <p>{t("Diz ao NANO \"lembra-te que…\" ou carrega em Novo.")}</p>
        </div>
      )}
      {rows && rows.length > 0 && (
        <div className="list">
          {rows.map((row) => (
            <div className="row" key={row.id}>
              <div className="row__body">
                <div className="row__title selectable">{row.text}</div>
                <div className="row__meta"><span className="kind-chip">{kindLabel(row.kind)}</span></div>
              </div>
              <button className="icon-btn" disabled={!online} onClick={() => void remove(row)}
                aria-label={t("Esquecer {0}", { 0: row.text.slice(0, 40) })}><Icon name="trash" size={20} /></button>
            </div>
          ))}
        </div>
      )}
      <button className="fab" onClick={() => { setAdding(true); setError(""); }} disabled={!online}><Icon name="plus" />{t("Novo")}</button>
      {adding && (
        <>
          <div className="scrim" onClick={() => setAdding(false)} />
          <div className="sheet" role="dialog" aria-modal="true" aria-label={t("Nova memória")}>
            <div className="sheet__grab" />
            <h2>{t("Nova memória")}</h2>
            <div className="stack">
              <div className="field">
                <label htmlFor="memory-text">{t("O que o NANO deve lembrar")}</label>
                <textarea id="memory-text" className="textarea" rows={3} maxLength={1000} value={text} autoFocus
                  placeholder={t("Prefiro reuniões de manhã")} onChange={(e) => setText(e.target.value)} />
              </div>
              {error && <p className="error-text" role="alert">{error}</p>}
              <button className="btn btn--primary btn--block" disabled={!text.trim()} onClick={() => void add()}>{t("Guardar")}</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
