/**
 * Text files attached in the composer.
 *
 * The window reads the file itself and sends its text with the message, so the
 * backend never opens a path the model chose. Only text is accepted: images
 * and PDFs need a reader NANO does not have yet, and are refused here with the
 * reason instead of arriving as garbage. The backend validates again
 * (core/attachments.py) and fences the text as untrusted data.
 *
 * Nothing here touches browser storage: a file lives in component state until
 * the message is sent, and then only in the backend's conversation store.
 */
import { t } from "./i18n";

/** One file waiting in the composer. */
export type PendingFile = { id: string; name: string; size: number; text: string };

/** What a sent message shows for each file. */
export type AttachmentSummary = { name: string; size: number; truncated?: boolean };

export const MAX_FILES = 5;
/** Bigger than this is not read at all. The backend keeps ~12 000 characters
 *  per turn anyway, so a larger file would mostly be thrown away. */
export const MAX_FILE_BYTES = 1024 * 1024;

const TEXT_EXTENSIONS = [
  "txt", "md", "markdown", "csv", "tsv", "json", "log", "xml", "yaml", "yml", "ini", "toml",
  "cfg", "conf", "env.example", "html", "htm", "css", "scss", "js", "jsx", "ts", "tsx", "mjs",
  "py", "java", "kt", "c", "h", "cpp", "hpp", "cs", "go", "rs", "rb", "php", "swift", "lua",
  "sh", "ps1", "bat", "sql", "srt", "vtt", "tex", "rst",
];

/** The picker's filter. The type check below still runs on dropped files. */
export const ACCEPT = [...TEXT_EXTENSIONS.map((ext) => `.${ext}`), "text/*"].join(",");

function looksLikeText(file: File): boolean {
  if (file.type.startsWith("text/")) return true;
  if (/^application\/(json|xml|x-yaml|javascript|x-sh|sql)/.test(file.type)) return true;
  const name = file.name.toLowerCase();
  return TEXT_EXTENSIONS.some((ext) => name.endsWith(`.${ext}`));
}

/** A readable size: "820 B", "14 KB", "1,2 MB". */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Read one file as text, or say why it cannot be attached. */
export async function readAttachment(file: File): Promise<PendingFile | { error: string }> {
  if (!looksLikeText(file)) {
    return { error: t("{0}: só ficheiros de texto (por exemplo .txt, .md, .csv ou código).", { 0: file.name }) };
  }
  if (file.size > MAX_FILE_BYTES) {
    return { error: t("{0} é demasiado grande (máximo {1}).", { 0: file.name, 1: formatSize(MAX_FILE_BYTES) }) };
  }
  let text: string;
  try {
    text = await file.text();
  } catch {
    return { error: t("Não foi possível ler {0}.", { 0: file.name }) };
  }
  if (text.includes("\u0000")) {
    return { error: t("{0} não parece ser um ficheiro de texto.", { 0: file.name }) };
  }
  return { id: crypto.randomUUID(), name: file.name, size: text.length, text };
}
