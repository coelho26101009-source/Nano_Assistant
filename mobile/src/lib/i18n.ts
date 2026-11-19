/**
 * Interface language: English by default, European Portuguese second.
 *
 * Same rule as the PC app (frontend/lib/i18n.ts): THE PORTUGUESE SENTENCE IS
 * THE KEY. Every string is the Portuguese original wrapped in `t()`, and
 * English is a lookup in `locales/en.ts`. test/i18n.test.ts fails on any `t()`
 * literal without an English entry, so a missing translation is caught before
 * it ships.
 *
 * The language follows the phone ("system") unless the person picks one in
 * Settings. It is fixed for the life of the page: changing it reloads.
 */
import { EN } from "./locales/en";

export type Locale = "en" | "pt-PT";
export type LanguageChoice = "system" | Locale;

export function normaliseLocale(tag: string | null | undefined): Locale | null {
  const value = String(tag ?? "").trim().toLowerCase();
  if (value.startsWith("pt")) return "pt-PT";
  if (value.startsWith("en")) return "en";
  return null;
}

function systemLocale(): Locale {
  const tags = typeof navigator !== "undefined" ? [...(navigator.languages ?? []), navigator.language] : [];
  for (const tag of tags) {
    const found = normaliseLocale(tag);
    if (found) return found;
  }
  return "en";
}

let current: Locale = systemLocale();

/** Called once at start-up with the stored choice. */
export function initLocale(choice: string | null) {
  current = choice === "en" || choice === "pt-PT" ? choice : systemLocale();
  if (typeof document !== "undefined") document.documentElement.lang = current === "en" ? "en" : "pt-PT";
}

export function locale(): Locale {
  return current;
}

/** BCP-47 tag for speech, dates and numbers. */
export function speechTag(): string {
  return current === "en" ? "en-US" : "pt-PT";
}

export function t(source: string, values?: Record<string, string | number>): string {
  let text = current === "en" ? (EN[source] ?? source) : source;
  if (values) {
    for (const [name, value] of Object.entries(values)) text = text.split(`{${name}}`).join(String(value));
  }
  return text;
}
