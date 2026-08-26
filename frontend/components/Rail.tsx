/**
 * The conversation rail.
 *
 * Left column of the chat view: start a conversation, search the ones that
 * exist, and reopen one. It replaces the old navigation rail, which put nine
 * technical pages at the same level as the conversation and made the shell feel
 * like a control panel rather than an assistant.
 *
 * EVERY ROW IS A REAL CONVERSATION. The list is derived from the stored message
 * log (see lib/conversations.ts) — real titles, real timestamps, real calendar
 * groups. There is no seeded example row, and when the log holds a single
 * conversation the list shows exactly one.
 *
 * Only the newest conversation is live, because only its messages are in the
 * Brain's context window. Older ones open read-only, and the rail says so
 * rather than letting the user discover it by typing into a conversation that
 * cannot answer.
 */
import React from "react";

import NanoLogo from "./NanoLogo";
import { Button, Skeleton } from "./ui";
import { Session, groupSessions, matchesQuery, sessionStamp } from "../lib/conversations";

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

export default function Rail({
  sessions, liveId, openId, query, onQuery, onNew, onOpen,
  loading, messageCount, onOpenMemory, drawer, onCloseDrawer,
}: {
  sessions: Session[];
  /** The conversation the Brain currently holds — the only writable one. */
  liveId: string | null;
  /** The conversation on screen. Equal to liveId unless the user is reading. */
  openId: string | null;
  query: string;
  onQuery: (value: string) => void;
  onNew: () => void;
  onOpen: (session: Session) => void;
  loading: boolean;
  /** Real count from core/memory.count_messages, or null before it is known. */
  messageCount: number | null;
  onOpenMemory: () => void;
  /** "open" | "closed" at narrow widths, where the rail is an overlay. */
  drawer: "open" | "closed" | "docked";
  onCloseDrawer: () => void;
}) {
  const matching = React.useMemo(
    () => sessions.filter((session) => matchesQuery(session, query)),
    [sessions, query],
  );
  const groups = React.useMemo(() => groupSessions(matching), [matching]);

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
        {loading && !sessions.length ? (
          <div className="stack stack--tight" style={{ padding: "0 8px" }}>
            <Skeleton height={42} /><Skeleton height={42} /><Skeleton height={42} />
          </div>
        ) : !sessions.length ? (
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
                <span className="section-label" aria-hidden="true">{group.sessions.length}</span>
              </div>
              {group.sessions.map((session) => {
                const isLive = session.id === liveId;
                return (
                  <button
                    key={session.id} type="button" className="chat-item"
                    aria-current={session.id === openId ? "true" : undefined}
                    onClick={() => onOpen(session)}
                    title={isLive ? session.title : `${session.title} — abre em modo de leitura`}
                  >
                    <span className="chat-item__icon">
                      {isLive ? <NanoLogo size={16} /> : <Glyph d={DOC} size={15} />}
                    </span>
                    <span className="chat-item__title">{session.title}</span>
                    <span className="chat-item__time">{sessionStamp(session)}</span>
                  </button>
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
    </aside>
  );
}
