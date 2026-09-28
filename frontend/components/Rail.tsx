/**
 * The sidebar: brand, new conversation, search, the sections, the
 * conversation list, and settings.
 *
 * Laid out the way Chatbot UI lays out its sidebar -- a primary "new" button,
 * a search field, then the list grouped by day -- with NANO's sections between
 * the search and the list, and Definições pinned to the foot. It has three
 * shapes, chosen by the shell from the window width and the user's choice:
 *
 *   expanded   docked, full width, everything visible
 *   collapsed  docked as a narrow column of icons: the N, new conversation,
 *              search, the sections and settings -- no list
 *   drawer     the expanded sidebar laid over the page at narrow widths
 *
 * It is ONE element in all three, so collapsing or opening it never remounts
 * the list: a search query or a selection in progress survives the change.
 *
 * EVERY ROW IS A STORED THREAD. The list comes from `list_conversations`, which
 * reads the `conversations` table — real ids, real titles, real timestamps.
 * Opening a row rebuilds the model's context from that thread, so "continue
 * where we left off" is literally what happens.
 *
 * The row actions live behind a per-row menu rather than as always-visible
 * buttons: a list you scan should not turn into a list you read. The menu is
 * quiet, not invisible — see `.chat-item__menu` in globals.css for why that
 * distinction cost a round of human testing.
 *
 * SELECTION MODE is the second interaction this list supports. It is a MODE
 * rather than a permanent checkbox column: deleting several conversations is a
 * rare, deliberate act, and paying for it with a checkbox on every row of every
 * scan is the wrong trade. Entering the mode is one click in the list header;
 * leaving it is Cancel or Escape.
 */
import React from "react";

import Icon, { type IconName } from "./Icon";
import NanoLogo, { NanoLockup } from "./NanoLogo";
import { Button, ConfirmDialog, Popover, Skeleton } from "./ui";
import { Thread, groupThreads, matchesThread, threadStamp } from "../lib/conversations";
import {
  NavCounts, SECTIONS, SectionEntry, SectionId, ViewId, sectionBadge, sectionOf,
} from "../lib/navigation";

export type RailMode = "expanded" | "collapsed" | "drawer";

const SECTION_ICON: Record<SectionId, IconName> = {
  chat: "chat",
  tools: "tools",
  pc: "monitor",
  memory: "memory",
  settings: "settings",
};

/** One section in the sidebar. The label stays in the DOM when collapsed, so
 *  the icon-only control still has a name to announce. */
function SectionButton({
  entry, active, badge, collapsed, onView,
}: {
  entry: SectionEntry;
  active: boolean;
  badge: number;
  collapsed: boolean;
  onView: (view: ViewId) => void;
}) {
  const hint = entry.views[0].hint;
  return (
    <button
      type="button" className="rail-nav__item" data-section={entry.section}
      aria-current={active ? "page" : undefined}
      onClick={() => onView(entry.views[0].id)}
      title={collapsed ? `${entry.label} — ${hint}` : hint}
    >
      <span className="rail-nav__icon"><Icon name={SECTION_ICON[entry.section]} size={18} /></span>
      <span className={collapsed ? "sr-only" : "rail-nav__label"}>{entry.label}</span>
      {badge > 0 && (
        <span className="rail-nav__badge" aria-label={`${badge} por rever`}>
          {badge > 99 ? "99+" : badge}
        </span>
      )}
    </button>
  );
}

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
          <Icon name="dots" size={16} strokeWidth={2.6} />
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
  mode, onToggle, view, onView, counts, version,
  threads, activeId, query, onQuery, onNew, onOpen, onRename, onDelete, onDeleteMany,
  loading, unavailable,
}: {
  mode: RailMode;
  /** Collapse or expand when docked; open or close when it is a drawer. */
  onToggle: () => void;
  view: ViewId;
  onView: (view: ViewId) => void;
  counts: NavCounts;
  /** The product version, shown small in the footer. */
  version?: string;
  threads: Thread[];
  /** The thread the Brain is holding. It is also the one on screen. */
  activeId: string | null;
  query: string;
  onQuery: (value: string) => void;
  onNew: () => void;
  onOpen: (thread: Thread) => void;
  onRename: (thread: Thread, title: string) => void;
  onDelete: (thread: Thread) => void;
  /** Bulk delete. ONE backend call for the whole selection, never a loop here. */
  onDeleteMany: (ids: string[]) => void;
  loading: boolean;
  /** True when the memory database could not be migrated. Say so; do not fake a list. */
  unavailable?: boolean;
}) {
  const collapsed = mode === "collapsed";
  const [renaming, setRenaming] = React.useState<Thread | null>(null);
  const [draftTitle, setDraftTitle] = React.useState("");
  const [deleting, setDeleting] = React.useState<Thread | null>(null);
  const [selecting, setSelecting] = React.useState(false);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [confirmBulk, setConfirmBulk] = React.useState(false);

  /* The collapsed column's search button expands the sidebar and then puts
     the caret in the field. The field does not exist until the expanded
     layout has rendered, so the focus waits for it rather than racing it. */
  const searchRef = React.useRef<HTMLInputElement>(null);
  const [focusSearch, setFocusSearch] = React.useState(false);
  React.useEffect(() => {
    if (!focusSearch || collapsed) return;
    searchRef.current?.focus();
    setFocusSearch(false);
  }, [focusSearch, collapsed]);

  const matching = React.useMemo(
    () => threads.filter((thread) => matchesThread(thread, query)),
    [threads, query],
  );
  const groups = React.useMemo(() => groupThreads(matching), [matching]);

  const startRename = React.useCallback((thread: Thread) => {
    setDraftTitle(thread.title);
    setRenaming(thread);
  }, []);

  const exitSelection = React.useCallback(() => {
    setSelecting(false);
    setSelected(new Set());
    setConfirmBulk(false);
  }, []);

  /* ESCAPE LEAVES THE MODE, but only while no dialog is open on top of it.
     The confirmation owns Escape while it is up: closing both with one press
     would cancel the delete AND the selection the user spent time building. */
  React.useEffect(() => {
    if (!selecting || confirmBulk || renaming || deleting) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") exitSelection();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selecting, confirmBulk, renaming, deleting, exitSelection]);

  /* A selection can only ever name threads that still exist. Without this, a
     thread deleted from its own row menu (or by another window) would stay in
     the set and be re-sent to the backend as part of the next bulk delete. */
  React.useEffect(() => {
    setSelected((current) => {
      if (!current.size) return current;
      const alive = new Set(threads.map((thread) => thread.id));
      const next = new Set([...current].filter((id) => alive.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [threads]);

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // "Select all" means everything the user can currently SEE. Selecting rows
  // hidden behind a search filter would delete conversations that were never
  // on screen.
  const allVisibleSelected = matching.length > 0
    && matching.every((thread) => selected.has(thread.id));

  /* THE SELECTION IS RESOLVED AGAINST EVERY THREAD, NOT THE FILTERED VIEW.
     It used to be `matching.filter(...)`, which made the confirmation lie: the
     title and the button counted `selected.size` while the ids that actually
     reached the backend, and the message total beside them, counted only the
     rows the current search happened to be showing. Select four, type a query
     that matches one, and the dialog offered "Apagar 4 conversas" while
     deleting one.

     Resolving against `threads` makes all three numbers the same number by
     construction. A search filter is a lens on the list; it is not a second,
     invisible selection. Acquiring a selection is still bounded by what is on
     screen -- see `allVisibleSelected` -- so nothing can be selected that the
     user never saw. */
  const selectedThreads = threads.filter((thread) => selected.has(thread.id));
  const selectedMessages = selectedThreads.reduce(
    (total, thread) => total + (thread.messageCount ?? 0), 0);

  const current = sectionOf(view);
  const sections = SECTIONS.filter((entry) => entry.section !== "settings");
  const settings = SECTIONS.find((entry) => entry.section === "settings")!;

  const toggleLabel = mode === "drawer"
    ? "Fechar barra lateral"
    : collapsed ? "Abrir barra lateral" : "Recolher barra lateral";

  return (
    <aside className="rail" data-mode={mode} aria-label="Barra lateral">
      <div className="rail__brand">
        {collapsed ? <NanoLogo size={26} title="NANO" /> : <NanoLockup size={26} />}
        <button
          type="button" className="icon-btn rail__toggle" onClick={onToggle}
          aria-expanded={!collapsed}
          aria-label={toggleLabel} title={`${toggleLabel} (Ctrl+B)`}
        >
          <Icon name="panel" />
        </button>
      </div>

      <div className="rail__actions">
        {/* STARTING A CONVERSATION LEAVES SELECTION MODE.
            Everything else already treats the mode as modal: Escape leaves it,
            and while it is on a row click selects instead of opening. Creating
            a conversation was the one route that slipped through, so the user
            landed in a fresh chat with the list still in a deletion mode they
            had stopped thinking about — and the next click on a conversation
            silently ticked it instead of opening it. */}
        <Button
          variant="primary" block={!collapsed} icon={collapsed}
          className="rail__new" title="Nova conversa (Ctrl+N)"
          onClick={() => { exitSelection(); onNew(); }}
        >
          <Icon name="plus" size={18} strokeWidth={2} />
          <span className={collapsed ? "sr-only" : undefined}>Nova conversa</span>
        </Button>

        {collapsed ? (
          /* Styled like the section icons beside it, but NOT one of them:
             search is an action, and `.rail-nav__item` means "a destination". */
          <button
            type="button" className="rail__search-button"
            onClick={() => { setFocusSearch(true); onToggle(); }}
            aria-label="Pesquisar conversas" title="Pesquisar conversas"
          >
            <Icon name="search" size={18} />
          </button>
        ) : (
          <span className="search rail__search">
            <span className="search__icon"><Icon name="search" size={15} /></span>
            <label className="sr-only" htmlFor="rail-search">Pesquisar conversas</label>
            <input
              id="rail-search" ref={searchRef} className="input" type="search"
              placeholder="Pesquisar conversas…"
              value={query} onChange={(event) => onQuery(event.target.value)}
            />
          </span>
        )}
      </div>

      <nav className="rail-nav" aria-label="Secções">
        {sections.map((entry) => (
          <SectionButton
            key={entry.section} entry={entry} active={current === entry.section}
            badge={sectionBadge(entry, counts)} collapsed={collapsed} onView={onView}
          />
        ))}
      </nav>

      {collapsed ? (
        <div className="rail__fill" />
      ) : (
        <div className="rail__scroll">
          <div className="rail-list__head">
            {/* SELECTION. One quiet entry point when idle; a real toolbar once
                the mode is on. The toolbar replaces the entry point rather than
                sitting beside it, so the header never holds two competing
                affordances. */}
            {!unavailable && threads.length > 0 && selecting ? (
              <div className="rail__select-bar" role="group" aria-label="Seleção de conversas">
                <span className="rail__select-count" aria-live="polite">
                  {selected.size === 1 ? "1 selecionada" : `${selected.size} selecionadas`}
                </span>
                <button
                  type="button" className="rail__select-action"
                  onClick={() => setSelected(allVisibleSelected
                    ? new Set()
                    : new Set(matching.map((thread) => thread.id)))}
                >
                  {allVisibleSelected ? "Limpar" : "Selecionar tudo"}
                </button>
                <button
                  type="button" className="rail__select-action rail__select-action--danger"
                  disabled={!selected.size}
                  onClick={() => setConfirmBulk(true)}
                  title={selected.size ? "Apagar as conversas selecionadas" : "Seleciona pelo menos uma conversa"}
                >
                  <Icon name="trash" size={14} />
                  Eliminar
                </button>
                <button type="button" className="rail__select-action" onClick={exitSelection}>
                  Cancelar
                </button>
              </div>
            ) : (
              <>
                <span className="rail-list__label">Conversas</span>
                {!unavailable && threads.length > 0 && (
                  <button
                    type="button" className="rail__select-action"
                    onClick={() => setSelecting(true)}
                    title="Selecionar várias conversas para apagar"
                  >
                    <Icon name="check" size={14} />
                    Selecionar
                  </button>
                )}
              </>
            )}
          </div>

          {unavailable ? (
            <p className="rail__note">
              A base de dados de memória não pôde ser migrada, por isso não há lista de
              conversas. O chat continua a funcionar. Vê Memória para o detalhe.
            </p>
          ) : loading && !threads.length ? (
            <div className="stack stack--tight" style={{ padding: "0 8px" }}>
              <Skeleton height={34} /><Skeleton height={34} /><Skeleton height={34} />
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
                <div className="rail-group__head">{group.label}</div>
                {group.threads.map((thread) => {
                  const isActive = thread.id === activeId;
                  const isChecked = selected.has(thread.id);
                  const stamp = threadStamp(thread);
                  return (
                    <div
                      key={thread.id}
                      className="chat-item-row"
                      data-active={isActive ? "true" : undefined}
                      data-selected={selecting && isChecked ? "true" : undefined}
                    >
                      {/* In selection mode the row's primary action becomes
                          "select", not "open": clicking a title to open a
                          conversation the user is about to delete is a trap. */}
                      <button
                        type="button" className="chat-item"
                        aria-current={isActive ? "true" : undefined}
                        aria-pressed={selecting ? isChecked : undefined}
                        onClick={() => (selecting ? toggle(thread.id) : onOpen(thread))}
                        title={selecting
                          ? `${isChecked ? "Remover da seleção" : "Selecionar"}: ${thread.title}`
                          : `${thread.title} · ${stamp}`}
                      >
                        {selecting && (
                          <span className="chat-item__icon">
                            <span className={`chat-item__check${isChecked ? " is-on" : ""}`}
                                  aria-hidden="true">
                              {isChecked ? <Icon name="check" size={12} strokeWidth={2.4} /> : null}
                            </span>
                          </span>
                        )}
                        <span className="chat-item__title">{thread.title}</span>
                      </button>
                      {!selecting && (
                        <RowMenu thread={thread} onRename={startRename} onDelete={setDeleting} />
                      )}
                    </div>
                  );
                })}
              </div>
            ))
          )}
        </div>
      )}

      <div className="rail__footer">
        <SectionButton
          entry={settings} active={current === "settings"} badge={0}
          collapsed={collapsed} onView={onView}
        />
        {!collapsed && version && <span className="rail__version">{version}</span>}
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
              Depois de mudares o nome, o NANO deixa de o alterar sozinho.
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

      {/* ONE confirmation for the whole batch, and it counts what it is about
          to remove out loud. "Apagar 12 conversas e 340 mensagens" is a
          different decision from "apagar 1", and the dialog has to say which
          one the user is making. */}
      <ConfirmDialog
        open={confirmBulk} danger
        title={`Apagar ${selected.size} conversa${selected.size === 1 ? "" : "s"}?`}
        confirmLabel={`Apagar ${selected.size}`}
        message={
          <>
            <strong>{selected.size} conversa{selected.size === 1 ? "" : "s"}</strong>
            {" "}e as suas {selectedMessages} mensagens são apagadas deste computador.
            Isto não pode ser desfeito.
            <br /><br />
            <span className="dim">
              As memórias de longo prazo que tenham nascido nestas conversas ficam
              guardadas — apagas cada uma em Memória › Memórias.
            </span>
          </>
        }
        onConfirm={() => {
          const ids = selectedThreads.map((thread) => thread.id);
          setConfirmBulk(false);
          if (ids.length) onDeleteMany(ids);
          exitSelection();
        }}
        onCancel={() => setConfirmBulk(false)}
      />
    </aside>
  );
}
