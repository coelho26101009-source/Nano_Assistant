/**
 * Interface language.
 *
 * THE PORTUGUESE SENTENCE IS THE KEY. NANO was written in European Portuguese
 * and every string in the components is still the Portuguese original, wrapped
 * in `t()`: `t("Nova conversa")` reads as what it says, and English is a lookup
 * in `locales/en.ts`. A string with no English entry shows as written, so a
 * missing translation degrades to Portuguese instead of to an id, and
 * tests/test_i18n.py fails on any `t()` literal that has no entry.
 *
 * Placeholders are `{0}`, `{1}`… (or names) and are filled AFTER translation, so
 * the English sentence puts them wherever English grammar wants them.
 *
 * THE LANGUAGE IS FIXED FOR THE LIFE OF THE PAGE. It is read once, when this
 * module loads, from `?lang=` in the address or else from the browser's own
 * language (in the desktop shell, the operating system's). That is what lets
 * module-level tables -- the navigation, the AI modes, the reason sentences --
 * be written with `t()` like everything else. Changing language saves the
 * choice in the backend and reloads the page with the new `?lang=`.
 *
 * No browser storage is involved: the choice lives in the backend's settings,
 * and the address only carries it across the reload.
 */
import { EN } from "./locales/en";

export type Locale = "en" | "pt-PT";
export const LOCALES: { id: Locale; label: string }[] = [
  { id: "en", label: "English" },
  { id: "pt-PT", label: "Português (Portugal)" },
];

/** A language tag from the address, the browser or the backend, as a Locale. */
export function normaliseLocale(tag: string | null | undefined): Locale | null {
  const value = String(tag ?? "").trim().toLowerCase();
  if (!value) return null;
  if (value.startsWith("pt")) return "pt-PT";
  if (value.startsWith("en")) return "en";
  return null;
}

function detect(): Locale {
  if (typeof window === "undefined") return "en";
  try {
    const fromUrl = normaliseLocale(new URLSearchParams(window.location.search).get("lang"));
    if (fromUrl) return fromUrl;
  } catch { /* no usable address: fall through */ }
  const languages = typeof navigator !== "undefined"
    ? [...(navigator.languages ?? []), navigator.language]
    : [];
  for (const tag of languages) {
    const found = normaliseLocale(tag);
    if (found) return found;
  }
  // English when NANO does not speak the user's language yet: the widest reach.
  return "en";
}

const current: Locale = detect();

export function locale(): Locale {
  return current;
}

type Values = Record<string, string | number | null | undefined>;

/** Translate one Portuguese string into the language in force. */
export function t(text: string, values?: Values): string {
  let out = current === "en" ? (EN[text] ?? text) : text;
  if (values) {
    out = out.replace(/\{(\w+)\}/g, (whole: string, name: string) =>
      Object.prototype.hasOwnProperty.call(values, name) ? String(values[name] ?? "") : whole);
  }
  return out;
}

/**
 * Switch language: the caller has already saved the choice in the backend.
 * `next` is the RESOLVED language ("system" resolved by the backend).
 */
export function reloadInLocale(next: Locale): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  url.searchParams.set("lang", next);
  window.location.replace(url.toString());
}

/** The BCP-47 tag for dates and numbers in this language. */
export function intlLocale(): string {
  return current === "en" ? "en-GB" : "pt-PT";
}
