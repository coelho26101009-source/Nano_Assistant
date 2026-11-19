/**
 * Files attached in the composer: text, PDFs and images.
 *
 * The window reads the file itself and sends it with the message, so the
 * backend never opens a path the model chose. Text files go as text. PDFs and
 * images go as base64: the backend reads a PDF's text locally and asks a
 * vision model to describe an image (core/attachments.py, core/vision.py),
 * then fences the result as untrusted data. Anything else is refused here
 * with the reason instead of arriving as garbage.
 *
 * Nothing here touches browser storage: a file lives in component state until
 * the message is sent. The backend keeps only the text, never the bytes.
 */
import { t } from "./i18n";

export type AttachmentKind = "text" | "pdf" | "image";

/** One file waiting in the composer. */
export type PendingFile = {
  id: string; name: string; size: number; kind: AttachmentKind;
  text: string; data?: string; mime?: string;
};

/** What a sent message shows for each file. */
export type AttachmentSummary = { name: string; size: number; truncated?: boolean; kind?: AttachmentKind };

export const MAX_FILES = 5;
/** Bigger than this is not read at all. The backend keeps ~12 000 characters
 *  per turn anyway, so a larger file would mostly be thrown away. */
export const MAX_FILE_BYTES = 1024 * 1024;
/** Same limits as the backend (core/vision.py, core/attachments.py). */
export const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
export const MAX_PDF_BYTES = 15 * 1024 * 1024;

const TEXT_EXTENSIONS = [
  "txt", "md", "markdown", "csv", "tsv", "json", "log", "xml", "yaml", "yml", "ini", "toml",
  "cfg", "conf", "env.example", "html", "htm", "css", "scss", "js", "jsx", "ts", "tsx", "mjs",
  "py", "java", "kt", "c", "h", "cpp", "hpp", "cs", "go", "rs", "rb", "php", "swift", "lua",
  "sh", "ps1", "bat", "sql", "srt", "vtt", "tex", "rst",
];
const IMAGE_TYPES: Record<string, string> = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp", gif: "image/gif",
};

/** The picker's filter. The type check below still runs on dropped files. */
export const ACCEPT = [
  ...TEXT_EXTENSIONS.map((ext) => `.${ext}`), "text/*", ".pdf", "application/pdf",
  ...Object.keys(IMAGE_TYPES).map((ext) => `.${ext}`),
].join(",");

function extension(file: File): string {
  const name = file.name.toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1) : "";
}

function looksLikeText(file: File): boolean {
  if (file.type.startsWith("text/")) return true;
  if (/^application\/(json|xml|x-yaml|javascript|x-sh|sql)/.test(file.type)) return true;
  const name = file.name.toLowerCase();
  return TEXT_EXTENSIONS.some((ext) => name.endsWith(`.${ext}`));
}

/** The image type the backend accepts, or "" when this is not one. */
export function imageMime(file: File): string {
  if (Object.values(IMAGE_TYPES).includes(file.type)) return file.type;
  return IMAGE_TYPES[extension(file)] ?? "";
}

export function isPdf(file: File): boolean {
  return file.type === "application/pdf" || extension(file) === "pdf";
}

/** A readable size: "820 B", "14 KB", "1,2 MB". */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result ?? "");
      resolve(url.slice(url.indexOf(",") + 1));
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

/** Read one file, or say why it cannot be attached. */
export async function readAttachment(file: File): Promise<PendingFile | { error: string }> {
  const mime = imageMime(file);
  if (mime || isPdf(file)) {
    const kind: AttachmentKind = mime ? "image" : "pdf";
    const limit = mime ? MAX_IMAGE_BYTES : MAX_PDF_BYTES;
    if (file.size > limit) {
      return { error: t("{0} é demasiado grande (máximo {1}).", { 0: file.name, 1: formatSize(limit) }) };
    }
    try {
      const data = await readBase64(file);
      return { id: crypto.randomUUID(), name: file.name, size: file.size, kind, text: "",
               data, mime: mime || "application/pdf" };
    } catch {
      return { error: t("Não foi possível ler {0}.", { 0: file.name }) };
    }
  }
  if (!looksLikeText(file)) {
    return { error: t("{0}: só texto, PDF ou imagens (por exemplo .txt, .pdf, .png ou .jpg).", { 0: file.name }) };
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
  return { id: crypto.randomUUID(), name: file.name, size: text.length, kind: "text", text };
}

/** What send_message receives for one file. */
export function toPayload(file: PendingFile): Record<string, string> {
  if (file.kind === "text") return { name: file.name, text: file.text };
  return { name: file.name, kind: file.kind, data: file.data ?? "", mime: file.mime ?? "" };
}
