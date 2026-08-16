/**
 * Declarações TypeScript para a ponte Eel (Python ↔ Next.js)
 * O objeto `eel` é injectado pelo runtime do Eel no browser.
 */

declare global {
  interface Window {
    eel: any;
  }
}

export const initEel = () => {
  if (typeof window !== "undefined") {
    // Garante que o eel existe antes de registar callbacks
    window.eel = window.eel || {};
    window.eel.expose = window.eel.expose || function(f: any, n: string) {};
  }
};