/**
 * The conversation rail.
 *
 * Left column of the chat view: start a conversation, search the ones that
 * exist, reopen one, rename it, delete it.
 *
 * EVERY ROW IS A STORED THREAD. The list comes from `list_conversations`, which
 * reads the `conversations` table — real ids, real titles, real timestamps. It
 * is no longer reconstructed in the browser by splitting a flat log on silence,
 * which is why every row is now openable and writable instead of only the
 * newest one. Opening a row rebuilds the model's context from that thread, so
 * "continue where we left off" is literally what happens.
 *
 * The row actions live behind a per-row menu rather than as always-visible
 * buttons: a rail is a list you scan, and three controls per line turns
 * scanning into reading.
 */
import React from "react";

import NanoLogo from "./NanoLogo";
import { Button, ConfirmDialog, Popover, Skeleton } from "./ui";
import { Thread, groupThreads, matchesThread, threadStamp } from "../lib/conversations";

const Glyph = ({ d, size = 16 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

const SEARCH = "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.35-4.35";
const PLUS = "M12 5v14M5 12h14";
const DOC = "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6ZM14 2v6h6M9 13h6M9 17h4";
const BRAIN = "M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3";
const DOTS = "M12 5h.01M12 12h.01M12 19h.01";

function RowMenu({
  thread, onRename, onDelete,
}: {
  thread: Thread;
  onRename: (thread: Thread) => void;
  onDelete: (thread: Thread) => void;
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <Popover
      open={open}
      onClose={() => setOpen(false)}
      label={`Ações de ${thread.title}`}
      trigger={(props) => (
        <button
          {...props}
          type="button"
          className="chat-item__menu"
          title="Mudar o nome ou apagar"
          aria-label={`Ações de ${thread.title}`}
          onClick={(event) => { event.stopPropagation(); setOpen((v) => !v); }}
        >
          <Glyph d={DOTS} size={15} />
        </button>
      )}
    >
      {/* Popover already renders role="menu" and the label; these are its items. */}
      <button type="button" className="popover__item" role="menuitem"
              onClick={() => { setOpen(false); onRename(thread); }}>
        <span className="popover__item-body">
          <span className="popover__item-label">Mudar o nome</span>
        </span>
      </button>
      <button type="button" className="popover__item popover__item--danger" role="menuitem"
              onClick={() => { setOpen(false); onDelete(thread); }}>
        <span className="popover__item-body">
          <span className="popover__item-label">Apagar conversa</span>
          <span className="popover__item-hint">As mensagens são apagadas deste computador.</span>
        </span>
      </button>
    </Popover>
  );
}

export default function Rail({
  threads, activeId, query, onQuery, onNew, onOpen, onRename, onDelete,
  loading, messageCount, onOpenMemory, drawer, onCloseDrawer, unavailable,
}: {
  threads: Thread[];
  /** The thread the Brain is holding. It is also the one on screen. */
  activeId: string | null;
  query: string;
  onQuery: (value: string) => void;
  onNew: () => void;
  onOpen: (thread: Thread) => void;
  onRename: (thread: Thread, title: string) => void;
  onDelete: (thread: Thread) => void;
  loading: boolean;
  /** Real count from the conversations table, or null before it is known. */
  messageCount: number | null;
  onOpenMemory: () => void;
  /** "open" | "closed" at narrow widths, where the rail is an overlay. */
  drawer: "open" | "closed" | "docked";
  onCloseDrawer: () => void;
  /** True when the memory database could not be migrated. Say so; do not fake a list. */
  unavailable?: boolean;
}) {
  const [renaming, setRenaming] = React.useState<Thread | null>(null);
  const [draftTitle, setDraftTitle] = React.useState("");
  const [deleting, setDeleting] = React.useState<Thread | null>(null);

  const matching = React.useMemo(
    () => threads.filter((thread) => matchesThread(thread, query)),
    [threads, query],
  );
  const groups = React.useMemo(() => groupThreads(matching), [matching]);

  const startRename = React.useCallback((thread: Thread) => {
    setDraftTitle(thread.title);
    setRenaming(thread);
  }, []);

  return (
    <aside
      className="rail surface-panel"
      data-drawer={drawer === "docked" ? undefined : drawer}
      aria-label="Conversas"
    >
      <div className="rail__top">
        <Button variant="primary" block onClick={onNew} title="Nova conversa (Ctrl+N)">
          <Glyph d={PLUS} size={17} />
          Nova conversa
        </Button>

        <div className="rail__search-row">
          <span className="search">
            <span className="search__icon"><Glyph d={SEARCH} size={15} /></span>
            <label className="sr-only" htmlFor="rail-search">Pesquisar conversas</label>
            <input
              id="rail-search" className="input" type="search"
              placeholder="Pesquisar conversas…"
              value={query} onChange={(event) => onQuery(event.target.value)}
            />
          </span>
          {drawer === "open" && (
            <button type="button" className="icon-btn" onClick={onCloseDrawer}
                    aria-label="Fechar conversas" title="Fechar conversas">
              ✕
            </button>
          )}
        </div>
      </div>

      <div className="rail__scroll">
        {unavailable ? (
          <p className="rail__note">
            A base de dados de memória não pôde ser migrada, por isso não há lista de
            conversas. O chat continua a funcionar. Vê Memória para o detalhe.
          </p>
        ) : loading && !threads.length ? (
          <div className="stack stack--tight" style={{ padding: "0 8px" }}>
            <Skeleton height={42} /><Skeleton height={42} /><Skeleton height={42} />
          </div>
        ) : !threads.length ? (
          <p className="rail__note">
            Ainda não há conversas guardadas. A primeira mensagem que enviares começa uma.
          </p>
        ) : !matching.length ? (
          <p className="rail__note">Nenhuma conversa corresponde a “{query.trim()}”.</p>
        ) : (
          groups.map((group) => (
            <div className="rail-group" key={group.key}>
              <div className="rail-group__head">
                <span className="rail-group__label section-label">{group.label}</span>
                <span className="section-label" aria-hidden="true">{group.threads.length}</span>
              </div>
              {group.threads.map((thread) => {
                const isActive = thread.id === activeId;
                return (
                  <div
                    key={thread.id}
                    className="chat-item-row"
                    data-active={isActive ? "true" : undefined}
                  >
                    <button
                      type="button" className="chat-item"
                      aria-current={isActive ? "true" : undefined}
                      onClick={() => onOpen(thread)}
                      title={thread.title}
                    >
                      <span className="chat-item__icon">
                        {isActive ? <NanoLogo size={16} /> : <Glyph d={DOC} size={15} />}
                      </span>
                      <span className="chat-item__title">{thread.title}</span>
                      <span className="chat-item__time">{threadStamp(thread)}</span>
                    </button>
                    <RowMenu thread={thread} onRename={startRename} onDelete={setDeleting} />
                  </div>
                );
              })}
            </div>
          ))
        )}
      </div>

      <div className="rail__footer">
        <Button block onClick={onOpenMemory} title="Abrir a memória do Nano">
          <Glyph d={BRAIN} size={16} />
          Memória
          {messageCount !== null && (
            <span className="dim" style={{ marginLeft: "auto", fontSize: 11 }}>
              {messageCount} msg
            </span>
          )}
        </Button>
      </div>

      <ConfirmDialog
        open={Boolean(renaming)}
        title="Mudar o nome da conversa"
        confirmLabel="Guardar"
        message={
          <>
            <label className="sr-only" htmlFor="rail-rename">Novo nome</label>
            <input
              id="rail-rename" className="input" value={draftTitle} autoFocus
              maxLength={120}
              onChange={(event) => setDraftTitle(event.target.value)}
              placeholder="Nome da conversa"
            />
            <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
              Depois de mudares o nome, o Nano deixa de o alterar sozinho.
            </p>
          </>
        }
        onConfirm={() => {
          if (renaming && draftTitle.trim()) onRename(renaming, draftTitle.trim());
          setRenaming(null);
        }}
        onCancel={() => setRenaming(null)}
      />

      <ConfirmDialog
        open={Boolean(deleting)} danger
        title="Apagar esta conversa?"
        confirmLabel="Apagar"
        message={
          <>
            <strong>{deleting?.title}</strong> e as suas {deleting?.messageCount ?? 0} mensagens
            são apagadas deste computador. Isto não pode ser desfeito.
            <br /><br />
            <span className="dim">
              As memórias de longo prazo que tenham nascido nesta conversa ficam
              guardadas — apagas cada uma em Memória › Memórias.
            </span>
          </>
        }
        onConfirm={() => { if (deleting) onDelete(deleting); setDeleting(null); }}
        onCancel={() => setDeleting(null)}
      />
    </aside>
  );
}
