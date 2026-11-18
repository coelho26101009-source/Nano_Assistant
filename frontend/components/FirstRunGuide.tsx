import React, { useState } from "react";
import type { ProviderPayload, ReadinessPayload } from "../lib/backend";
import { useFetch } from "../lib/backend";
import { VERSION } from "../lib/version";
import type { Section } from "./SettingsPage";
import { Badge, Button, MetricRow, StatusIndicator } from "./ui";
import { t } from "../lib/i18n";

/**
 * First run, in three short steps: a brain, what stays private, your name.
 *
 * Setup reuses the normal settings controls: keys never pass through a second
 * form, and viewing the guide never enables a service or a permission. When a
 * provider is already usable the shell skips the guide entirely; it can be
 * reopened from Definições → Sobre.
 */
export default function FirstRunGuide({
  providers, readiness, saving, onSettings, onFinish, onRefresh, displayName, onDisplayName,
}: {
  providers: ProviderPayload | null;
  readiness: ReadinessPayload | null;
  saving: boolean;
  onSettings: (section: Section) => void;
  onFinish: () => void;
  onRefresh: () => void;
  displayName: string;
  onDisplayName: (value: string) => void;
}) {
  const { data: location } = useFetch<{ data_dir?: string }>("get_data_location", true);
  const usable = providers?.route?.usable === true;
  const [step, setStep] = useState(0);
  const [name, setName] = useState(displayName);
  const steps = [t("Inteligência"), t("Privacidade"), t("Pronto")];

  const finish = () => {
    if (name.trim() !== displayName) onDisplayName(name.trim());
    onFinish();
  };

  return (
    <section className="first-run page-scroll" aria-labelledby="first-run-title">
      <div className="first-run__inner stack">
        <header>
          <Badge tone="accent">{t("{0} · Beta", { 0: VERSION.display })}</Badge>
          <h2 id="first-run-title" className="page-title">{t("Bem-vindo ao NANO")}</h2>
          <ol className="first-run__steps" aria-label={t("Passos")}>
            {steps.map((label, index) => (
              <li key={label} aria-current={index === step ? "step" : undefined}
                  className={index < step ? "is-done" : undefined}>
                <span className="first-run__dot" aria-hidden="true">{index + 1}</span>
                {label}
              </li>
            ))}
          </ol>
        </header>

        {step === 0 && (
          <div className="first-run__card">
            <h3>{t("Escolhe o cérebro do NANO")}</h3>
            <p className="muted">{t("Na cloud (Groq, Gemini ou Mistral) as respostas são rápidas e precisas de uma chave de API gratuita. No modo Local, com o Ollama, nada sai do computador.")}</p>
            <MetricRow label={t("Chat")} value={<StatusIndicator state={providers ? (usable ? "READY" : "SETUP_REQUIRED") : "UNKNOWN"}
              label={providers ? (usable ? t("Provedor disponível") : t("Configura um provedor para conversar")) : t("A verificar provedores…")} />} />
            <div className="inline first-run__actions">
              <Button variant={usable ? "default" : "primary"} onClick={() => onSettings("ai")}>{t("Configurar IA")}</Button>
              <Button onClick={onRefresh}>{t("Verificar novamente")}</Button>
            </div>
          </div>
        )}

        {step === 1 && (
          <div className="first-run__card">
            <h3>{t("O que fica contigo")}</h3>
            <ul className="first-run__points">
              <li>{t("As conversas, a memória e as definições ficam neste computador.")}</li>
              <li>{t("Tudo o que mexe no PC (fechar, apagar, escrever) pede a tua autorização primeiro.")}</li>
              <li>{t("A voz é opcional. Podes sempre escrever.")}</li>
            </ul>
            <details className="first-run__details">
              <summary>{t("Mais detalhes")}</summary>
              <p className="muted">{t("No modo Cloud, o texto das mensagens vai para o provedor escolhido. A resposta falada usa um serviço Microsoft e envia o texto lido, mesmo em modo Local. O NANO não envia relatórios de diagnóstico automaticamente.")}</p>
              {location?.data_dir && <code className="first-run__path">{location.data_dir}</code>}
              <MetricRow label={t("Voz (opcional)")} value={<StatusIndicator state={readiness?.voice?.state} />} />
              <div className="inline first-run__actions">
                <Button size="sm" onClick={() => onSettings("privacy")}>{t("Ver privacidade")}</Button>
                <Button size="sm" onClick={() => onSettings("pccontrol")}>{t("Ver segurança do PC")}</Button>
              </div>
            </details>
          </div>
        )}

        {step === 2 && (
          <div className="first-run__card">
            <h3>{t("Como te chamas?")}</h3>
            <p className="muted">{t("Opcional. O NANO usa-o para te cumprimentar. Fica só neste computador.")}</p>
            <label className="sr-only" htmlFor="first-run-name">{t("O teu nome")}</label>
            <input id="first-run-name" className="input" maxLength={40} value={name}
                   placeholder={t("O teu nome")} onChange={(event) => setName(event.target.value)}
                   onKeyDown={(event) => { if (event.key === "Enter") finish(); }} />
          </div>
        )}

        <div className="first-run__finish">
          {step > 0 && <Button onClick={() => setStep(step - 1)}>{t("Voltar")}</Button>}
          {step < 2 ? (
            <Button variant="primary" onClick={() => setStep(step + 1)}>{t("Continuar")}</Button>
          ) : (
            <Button variant="primary" disabled={saving} onClick={finish}>
              {saving ? t("A guardar…") : usable ? t("Começar a conversar") : t("Explorar sem configurar")}
            </Button>
          )}
          <p className="dim">{t("Podes reabrir este guia em Definições → Sobre.")}</p>
        </div>
      </div>
    </section>
  );
}
