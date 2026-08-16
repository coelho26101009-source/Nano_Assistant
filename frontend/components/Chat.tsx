import { useState, useEffect, useRef } from "react";
import type { Message } from "../pages/index";

interface ChatProps {
  messages: Message[];
  isThinking: boolean;
}

export default function Chat({ messages, isThinking }: ChatProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isThinking]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {messages.map((msg) => (
        <div key={msg.id} className={`chat-message-row ${msg.role}`}>
          <div className={`chat-avatar ${msg.role}`}>
            {msg.role === "assistant" ? "N" : "S"}
          </div>

          <div className="chat-bubble">
            {msg.content ? (
              <>
                <MessageContent content={msg.content} />
                {msg.streaming && <span className="streaming-cursor">|</span>}
              </>
            ) : msg.streaming ? (
              <div className="thinking-pulse-text">
                <span>A processar resposta...</span>
              </div>
            ) : null}
          </div>
        </div>
      ))}

      {isThinking && !messages.some((m) => m.streaming) && (
        <div className="chat-message-row assistant">
          <div className="chat-avatar assistant">N</div>
          <div className="chat-bubble">
            <div className="thinking-pulse-text">
              <span>A pensar...</span>
            </div>
          </div>
        </div>
      )}

      <div ref={endRef} />
    </div>
  );
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    if (navigator?.clipboard) {
      navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="code-block-wrapper">
      <div className="code-block-header">
        <span style={{ fontFamily: "JetBrains Mono, monospace" }}>{language || "code"}</span>
        <button
          type="button"
          onClick={handleCopy}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--teal)",
            cursor: "pointer",
            fontSize: 11,
            fontWeight: 500
          }}
        >
          {copied ? "Copiado" : "Copiar"}
        </button>
      </div>
      <pre className="code-block-pre">
        <code>{code}</code>
      </pre>
    </div>
  );
}

function MessageContent({ content }: { content: string }) {
  const parts: React.ReactNode[] = [];
  const codeBlockRegex = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g;
  let lastIndex = 0;
  let match;

  while ((match = codeBlockRegex.exec(content)) !== null) {
    const textBefore = content.slice(lastIndex, match.index);
    if (textBefore) {
      parts.push(<TextBlock key={`text-${lastIndex}`} text={textBefore} />);
    }
    const lang = match[1] || "";
    const code = match[2];
    parts.push(<CodeBlock key={`code-${match.index}`} language={lang} code={code} />);
    lastIndex = match.index + match[0].length;
  }

  const remaining = content.slice(lastIndex);
  if (remaining) {
    parts.push(<TextBlock key={`text-${lastIndex}`} text={remaining} />);
  }

  return <>{parts}</>;
}

function TextBlock({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <>
      {lines.map((line, i) => {
        if (line.startsWith("### ")) return <h3 key={i} style={{ margin: "10px 0 4px", fontSize: 15 }}>{line.slice(4)}</h3>;
        if (line.startsWith("## ")) return <h2 key={i} style={{ margin: "12px 0 6px", fontSize: 17 }}>{line.slice(3)}</h2>;
        if (line.startsWith("# ")) return <h1 key={i} style={{ margin: "14px 0 8px", fontSize: 20 }}>{line.slice(2)}</h1>;
        if (line.startsWith("> ")) {
          return (
            <blockquote
              key={i}
              style={{
                borderLeft: "2px solid var(--teal)",
                paddingLeft: 10,
                color: "var(--text-muted)",
                margin: "6px 0"
              }}
            >
              {line.slice(2)}
            </blockquote>
          );
        }
        if (line.startsWith("- ") || line.startsWith("* ")) {
          return (
            <li key={i} style={{ marginLeft: 18, margin: "2px 0" }}>
              {renderInline(line.slice(2))}
            </li>
          );
        }
        if (line === "") return <div key={i} style={{ height: 8 }} />;
        return (
          <span key={i} style={{ display: "block" }}>
            {renderInline(line)}
          </span>
        );
      })}
    </>
  );
}

function renderInline(text: string): React.ReactNode {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return parts.map((p, j) => {
    if (p.startsWith("`") && p.endsWith("`")) {
      return (
        <code
          key={j}
          style={{
            background: "rgba(255,255,255,0.06)",
            color: "var(--teal)",
            padding: "2px 5px",
            borderRadius: 4,
            fontFamily: "JetBrains Mono, monospace",
            fontSize: "0.9em"
          }}
        >
          {p.slice(1, -1)}
        </code>
      );
    }
    if (p.startsWith("**") && p.endsWith("**")) {
      return <strong key={j} style={{ color: "var(--text)", fontWeight: 600 }}>{p.slice(2, -2)}</strong>;
    }
    if (p.startsWith("*") && p.endsWith("*") && p.length > 2) {
      return <em key={j}>{p.slice(1, -1)}</em>;
    }
    return p;
  });
}
