import React from "react";
import { createRoot } from "react-dom/client";
import App, { applyTheme } from "./App";
import { initLocale, type LanguageChoice } from "./lib/i18n";
import { getPref } from "./lib/storage";
import type { SpeakMode, ThemeChoice } from "./screens/SettingsScreen";
import "./styles.css";

async function boot() {
  const [language, theme, speak] = await Promise.all([getPref("language"), getPref("theme"), getPref("speak")]);
  initLocale(language);
  const themeChoice: ThemeChoice = theme === "light" || theme === "system" ? theme : "dark";
  applyTheme(themeChoice);
  const initial = {
    theme: themeChoice,
    language: (language === "en" || language === "pt-PT" ? language : "system") as LanguageChoice,
    speak: (speak === "always" || speak === "never" ? speak : "voice") as SpeakMode,
  };
  createRoot(document.getElementById("root")!).render(<React.StrictMode><App initial={initial} /></React.StrictMode>);
}

void boot();
