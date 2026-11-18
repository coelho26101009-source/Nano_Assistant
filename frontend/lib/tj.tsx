/**
 * A translated sentence with React nodes inside it.
 *
 * `tj("Abre o NANO com {0} para os ativar.", { 0: <code>NANO_DESKTOP.bat</code> })`
 * translates the WHOLE sentence first and only then drops the nodes in, so the
 * English version can put `<code>` wherever English word order wants it. Cutting
 * a sentence into several `t()` fragments around an element cannot do that.
 */
import { Fragment, type ReactNode } from "react";
import { t } from "./i18n";

export function tj(text: string, nodes: Record<string, ReactNode>): ReactNode {
  const parts = t(text).split(/(\{\w+\})/g);
  return (
    <>
      {parts.map((part, index) => {
        const match = /^\{(\w+)\}$/.exec(part);
        if (match && Object.prototype.hasOwnProperty.call(nodes, match[1])) {
          return <Fragment key={index}>{nodes[match[1]]}</Fragment>;
        }
        return part ? <Fragment key={index}>{part}</Fragment> : null;
      })}
    </>
  );
}
