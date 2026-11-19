/**
 * A small Markdown renderer for NANO's answers: paragraphs, headings, lists,
 * quotes, code blocks, **bold**, *italic*, `code` and links.
 *
 * It builds React elements and never sets innerHTML, so an answer that
 * contains HTML is shown as text, not run. Links open only for http(s).
 */
import React from "react";

function inline(text: string, keyBase: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const pattern = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*|_[^_\n]+_)|(\[[^\]\n]+\]\((https?:\/\/[^\s)]+)\))|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let n = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const key = `${keyBase}-${n++}`;
    const token = match[0];
    if (match[1]) out.push(<code key={key}>{token.slice(1, -1)}</code>);
    else if (match[2]) out.push(<strong key={key}>{inline(token.slice(2, -2), key)}</strong>);
    else if (match[3]) out.push(<em key={key}>{inline(token.slice(1, -1), key)}</em>);
    else if (match[4]) {
      const label = token.slice(1, token.indexOf("]("));
      out.push(<a key={key} href={match[5]} target="_blank" rel="noopener noreferrer">{label}</a>);
    } else if (match[6]) out.push(<a key={key} href={token} target="_blank" rel="noopener noreferrer">{token}</a>);
    last = match.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function Markdown({ text }: { text: string }) {
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push(<pre key={key++}><code>{body.join("\n")}</code></pre>);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (heading) { blocks.push(<h3 key={key++}>{inline(heading[1], `h${key}`)}</h3>); i++; continue; }
    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      blocks.push(<blockquote key={key++}>{inline(body.join(" "), `q${key}`)}</blockquote>);
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i++].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
      }
      const children = items.map((item, n) => <li key={n}>{inline(item, `l${key}-${n}`)}</li>);
      blocks.push(ordered ? <ol key={key++}>{children}</ol> : <ul key={key++}>{children}</ul>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^\s*([-*•]|\d+[.)])\s+|^#{1,6}\s|^\s*>|^\s*```/.test(lines[i])) {
      para.push(lines[i++]);
    }
    const k = key++;
    blocks.push(<p key={k}>{para.flatMap((l, n) => n ? [<br key={`br${n}`} />, ...inline(l, `p${k}-${n}`)] : inline(l, `p${k}-${n}`))}</p>);
  }
  return <>{blocks}</>;
}
