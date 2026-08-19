/** Keyboard-first command palette (Ctrl+K). */
import React, { useEffect, useMemo, useRef, useState } from "react";

export type Command = {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
};

export default function CommandPalette({
  open,
  commands,
  onClose,
}: {
  open: boolean;
  commands: Command[];
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return commands;
    return commands.filter((command) => command.label.toLowerCase().includes(needle));
  }, [commands, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      window.setTimeout(() => inputRef.current?.focus(), 20);
    }
  }, [open]);

  useEffect(() => { setIndex(0); }, [query]);

  if (!open) return null;

  const run = (command?: Command) => {
    if (!command) return;
    onClose();
    command.run();
  };

  return (
    <div className="palette-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Paleta de comandos">
        <input
          ref={inputRef}
          className="palette-input"
          placeholder="Escreve um comando…"
          value={query}
          aria-label="Procurar comando"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") { event.preventDefault(); onClose(); }
            else if (event.key === "ArrowDown") { event.preventDefault(); setIndex((i) => Math.min(i + 1, filtered.length - 1)); }
            else if (event.key === "ArrowUp") { event.preventDefault(); setIndex((i) => Math.max(i - 1, 0)); }
            else if (event.key === "Enter") { event.preventDefault(); run(filtered[index]); }
          }}
        />
        <div className="palette-list" role="listbox">
          {filtered.length === 0 ? (
            <div className="empty-state"><p className="empty-state-title">Sem resultados</p></div>
          ) : (
            filtered.map((command, position) => (
              <button
                key={command.id}
                type="button"
                role="option"
                aria-selected={position === index}
                className="palette-item"
                data-active={position === index}
                onMouseEnter={() => setIndex(position)}
                onClick={() => run(command)}
              >
                <span>{command.label}</span>
                {command.hint && <span className="palette-item-hint">{command.hint}</span>}
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
