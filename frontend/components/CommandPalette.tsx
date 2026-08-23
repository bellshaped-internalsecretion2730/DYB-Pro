"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export type Command = {
  id: string;
  label: string;
  hint?: string;
  disabled?: boolean;
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
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = commands.filter((c) => !c.disabled);
    return q ? rows.filter((c) => c.label.toLowerCase().includes(q)) : rows;
  }, [commands, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setCursor(0);
      inputRef.current?.focus();
    }
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="palette-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="palette" role="dialog" aria-modal="true" aria-label="command palette">
        <input
          ref={inputRef}
          type="text"
          value={query}
          placeholder="Run a command…"
          aria-label="command"
          onChange={(e) => {
            setQuery(e.target.value);
            setCursor(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setCursor((c) => Math.min(matches.length - 1, c + 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setCursor((c) => Math.max(0, c - 1));
            }
            if (e.key === "Enter") {
              const cmd = matches[cursor];
              if (cmd) {
                onClose();
                cmd.run();
              }
            }
          }}
        />
        <div className="options">
          {matches.map((c, i) => (
            <button
              type="button"
              className="option"
              key={c.id}
              aria-selected={i === cursor}
              onMouseEnter={() => setCursor(i)}
              onClick={() => {
                onClose();
                c.run();
              }}
            >
              <span>{c.label}</span>
              {c.hint && <span className="hintkey">{c.hint}</span>}
            </button>
          ))}
          {matches.length === 0 && (
            <p className="hint" style={{ padding: "10px 12px", margin: 0 }}>
              No matching command
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
