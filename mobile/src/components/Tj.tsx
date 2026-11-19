import React from "react";
import { t } from "../lib/i18n";

/** A whole translated sentence with elements inside it: tj("Vai a {0}.", { 0: <b>…</b> }). */
export function tj(source: string, parts: Record<string, React.ReactNode>): React.ReactNode {
  const text = t(source);
  const pieces = text.split(/(\{[^}]+\})/g);
  return pieces.map((piece, i) => {
    const name = piece.match(/^\{([^}]+)\}$/)?.[1];
    return name !== undefined && name in parts ? <React.Fragment key={i}>{parts[name]}</React.Fragment> : piece;
  });
}
