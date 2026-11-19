import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { EN } from "../src/lib/locales/en";

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

describe("i18n", () => {
  it("every t()/tj() string has an English entry", () => {
    const missing = new Set<string>();
    for (const file of files(join(__dirname, "../src"))) {
      if (file.includes("locales")) continue;
      const source = readFileSync(file, "utf8");
      for (const match of source.matchAll(/\bt(?:j)?\(\s*"((?:[^"\\]|\\.)*)"/g)) {
        const key = JSON.parse(`"${match[1]}"`);
        if (!(key in EN)) missing.add(key);
      }
    }
    expect([...missing]).toEqual([]);
  });

  it("placeholders survive translation", () => {
    for (const [pt, en] of Object.entries(EN)) {
      const names = (s: string) => (s.match(/\{[^}]+\}/g) ?? []).sort().join(",");
      expect(names(en), pt).toBe(names(pt));
    }
  });
});
