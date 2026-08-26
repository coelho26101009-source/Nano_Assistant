/**
 * The Windows caption controls.
 *
 * The window is frameless, so these replace the OS caption buttons. They no
 * longer live in a bar of their own: the redesign merges the caption into the
 * top navigation bar, exactly as the approved reference does, so this component
 * is just the minimise / maximise / close cluster that sits at its right end.
 *
 * Three things make that safe rather than annoying:
 *
 *  1. DRAG REGIONS ARE DELIBERATE. The top bar carries
 *     `-webkit-app-region: drag`, and every interactive child explicitly opts
 *     out. Miss one and that control becomes un-clickable: the drag region
 *     swallows the press before it reaches the handler. `.window-control`
 *     carries the no-drag rule, and globals.css also applies it by selector to
 *     every button, link and input inside `.topbar`, so a new control cannot
 *     forget it.
 *
 *  2. IT ONLY EXISTS IN THE DESKTOP SHELL. TopNav renders it from capability
 *     detection, so the same bundle opened in a browser during development
 *     simply has no caption buttons — no dead controls, no crash.
 *
 *  3. CLOSE HIDES TO THE TRAY rather than quitting, because the global shortcut
 *     has to keep working with the window gone. The tooltip says so, and Quit
 *     is a separate explicit action in the tray menu.
 */
import React from "react";

import {
  hideWindow, minimizeWindow, toggleMaximizeWindow, useWindowState,
} from "../lib/desktop";

/* Windows caption glyphs, drawn rather than typed: the Segoe MDL2 font is not
   guaranteed, and a missing glyph renders as a tofu box in the corner of the
   application. Crisp at 10 px because every line sits on a whole pixel. */
const Minimize = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
    <rect x="0" y="4.5" width="10" height="1" fill="currentColor" />
  </svg>
);

const Maximize = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
    <rect x="0.5" y="0.5" width="9" height="9" stroke="currentColor" />
  </svg>
);

const Restore = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
    <rect x="0.5" y="2.5" width="7" height="7" stroke="currentColor" />
    <path d="M2.5 2.5V0.5h7v7h-2" stroke="currentColor" />
  </svg>
);

const Close = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
    <path d="M0.5 0.5l9 9M9.5 0.5l-9 9" stroke="currentColor" />
  </svg>
);

export default function WindowControls() {
  const windowState = useWindowState();
  const maximized = windowState?.maximized ?? false;

  return (
    <div className="window-controls">
      <button
        type="button" className="window-control"
        onClick={minimizeWindow}
        aria-label="Minimizar" title="Minimizar"
      >
        <Minimize />
      </button>
      <button
        type="button" className="window-control"
        onClick={toggleMaximizeWindow}
        aria-label={maximized ? "Restaurar" : "Maximizar"}
        title={maximized ? "Restaurar" : "Maximizar"}
      >
        {maximized ? <Restore /> : <Maximize />}
      </button>
      <button
        type="button" className="window-control window-control--close"
        onClick={hideWindow}
        aria-label="Fechar para o tabuleiro"
        // Says exactly what happens. A close button that does not close is
        // only acceptable if it never surprises anyone.
        title="Fechar para o tabuleiro — o Nano continua a correr"
      >
        <Close />
      </button>
    </div>
  );
}

/** Whether the window currently reports focus, for chrome that dims with it. */
export function useCaptionFocus(): boolean {
  const state = useWindowState();
  return state?.focused !== false;
}
