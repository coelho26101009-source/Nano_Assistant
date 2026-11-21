/** Settings: the connection, voice, notifications, look, privacy and about. */
import { useEffect, useState } from "react";
import Icon from "../components/Icon";
import { tj } from "../components/Tj";
import { t, type LanguageChoice } from "../lib/i18n";
import type { LinkState, NanoLink } from "../lib/link";
import { askNotifications, isNative, notificationsAllowed } from "../lib/native";
import { PrivacyPolicy } from "./Privacy";
import { UpdateSettings } from "../components/Updates";

export type SpeakMode = "voice" | "always" | "never";
export type ThemeChoice = "dark" | "light" | "system";

export const APP_VERSION = "0.4.0-beta.1";

function stateText(state: LinkState, pcName: string): string {
  switch (state) {
    case "online": return t("Ligado ao {0}", { 0: pcName });
    case "connecting": return t("A ligar ao {0}…", { 0: pcName });
    case "offline": return t("Sem ligação ao {0}", { 0: pcName });
    case "revoked": return t("Este telemóvel foi removido no PC");
    default: return t("Não ligado");
  }
}

function Choice<T extends string>({ id, label, value, options, onChange }: {
  id: string; label: string; value: T; options: { value: T; label: string }[]; onChange: (value: T) => void;
}) {
  return (
    <div className="row">
      <div className="row__body"><label className="row__title" htmlFor={id}>{label}</label></div>
      <select id={id} className="select" style={{ width: "auto", minWidth: 150 }} value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

export default function SettingsScreen({
  link, state, speakMode, setSpeakMode, theme, setTheme, language, setLanguage, onUnpaired, toast, remindersOnPhone,
}: {
  link: NanoLink;
  state: LinkState;
  speakMode: SpeakMode;
  setSpeakMode: (mode: SpeakMode) => void;
  theme: ThemeChoice;
  setTheme: (theme: ThemeChoice) => void;
  language: LanguageChoice;
  setLanguage: (language: LanguageChoice) => void;
  onUnpaired: () => void;
  toast: (text: string) => void;
  remindersOnPhone: number;
}) {
  const [notifications, setNotifications] = useState<boolean | null>(null);
  const [sheet, setSheet] = useState<"away" | "privacy" | "forget" | null>(null);
  const [address, setAddress] = useState("");
  const [addressError, setAddressError] = useState("");
  const [, force] = useState(0);
  const info = link.info;

  useEffect(() => { void notificationsAllowed().then(setNotifications); }, []);

  const addAddress = async () => {
    setAddressError("");
    if (!(await link.addManualHost(address))) { setAddressError(t("Endereço inválido. Exemplo: 100.101.102.103")); return; }
    setAddress("");
    force((n) => n + 1);
    toast(t("Endereço adicionado."));
  };

  return (
    <div className="page__inner">
      <section className="section">
        <h2 className="section__title">{t("Ligação")}</h2>
        <div className="list">
          <div className="row">
            <span className={`dot dot--${state === "unpaired" ? "offline" : state}`} aria-hidden="true" />
            <div className="row__body">
              <div className="row__title">{stateText(state, link.pcName)}</div>
              <div className="row__meta">{t("Cifrado de ponta a ponta entre o telemóvel e o PC")}</div>
            </div>
          </div>
          {info && (
            <div className="row">
              <Icon name="wifi" />
              <div className="row__body">
                <div className="row__title">{t("Endereços do PC")}</div>
                <div className="row__meta selectable">{[...info.hosts.map((h) => `${h}:${info.port}`), ...info.extraHosts].join(" · ")}</div>
              </div>
            </div>
          )}
          <button className="row" onClick={() => setSheet("away")}>
            <Icon name="globe" />
            <div className="row__body">
              <div className="row__title">{t("Usar fora de casa")}</div>
              <div className="row__meta">{link.awayFromHome
                ? t("Ligado: funciona em qualquer lado, sempre cifrado")
                : t("Com o Tailscale, grátis e sem abrir portas")}</div>
            </div>
            <Icon name="chevronRight" />
          </button>
        </div>
        {state === "revoked" && (
          <div className="banner banner--danger" style={{ marginTop: 12 }}>
            <Icon name="alert" size={20} />
            <span>{t("O PC já não aceita este telemóvel. Esquece este PC e liga-o de novo.")}</span>
          </div>
        )}
      </section>

      <section className="section">
        <h2 className="section__title">{t("Voz e avisos")}</h2>
        <div className="list">
          <Choice id="speak" label={t("Ler as respostas em voz alta")} value={speakMode} onChange={setSpeakMode}
            options={[{ value: "voice", label: t("Quando falo") }, { value: "always", label: t("Sempre") }, { value: "never", label: t("Nunca") }]} />
          <div className="row">
            <Icon name="bell" />
            <div className="row__body">
              <div className="row__title">{t("Notificações")}</div>
              <div className="row__meta">
                {notifications
                  ? t("Ligadas · {0} lembretes agendados no telemóvel", { 0: remindersOnPhone })
                  : isNative() ? t("Desligadas: os lembretes não tocam no telemóvel") : t("Só na app Android")}
              </div>
            </div>
            {isNative() && !notifications && (
              <button className="btn btn--sm" onClick={async () => setNotifications(await askNotifications())}>{t("Ligar")}</button>
            )}
          </div>
        </div>
      </section>

      <section className="section">
        <h2 className="section__title">{t("Aspeto")}</h2>
        <div className="list">
          <Choice id="theme" label={t("Tema")} value={theme} onChange={setTheme}
            options={[{ value: "dark", label: t("Escuro") }, { value: "light", label: t("Claro") }, { value: "system", label: t("Igual ao telemóvel") }]} />
          <Choice id="language" label={t("Idioma")} value={language} onChange={setLanguage}
            options={[{ value: "system", label: t("Igual ao telemóvel") }, { value: "en", label: "English" }, { value: "pt-PT", label: "Português (Portugal)" }]} />
        </div>
      </section>

      <section className="section">
        <h2 className="section__title">{t("Privacidade")}</h2>
        <div className="list">
          <button className="row" onClick={() => setSheet("privacy")}>
            <Icon name="lock" />
            <div className="row__body"><div className="row__title">{t("Política de privacidade")}</div></div>
            <Icon name="chevronRight" />
          </button>
          <button className="row" onClick={() => setSheet("forget")}>
            <Icon name="trash" />
            <div className="row__body">
              <div className="row__title" style={{ color: "var(--danger)" }}>{t("Esquecer este PC")}</div>
              <div className="row__meta">{t("Apaga a chave deste telemóvel. Para voltar, liga-o de novo.")}</div>
            </div>
          </button>
        </div>
      </section>

      <section className="section">
        <h2 className="section__title">{t("Sobre")}</h2>
        <div className="list">
          <div className="row">
            <div className="row__body">
              <div className="row__title">NANO {APP_VERSION}</div>
              <div className="row__meta">{t("O NANO do teu PC, no telemóvel.")}</div>
            </div>
          </div>
          <UpdateSettings toast={toast} />
        </div>
      </section>

      {sheet && <div className="scrim" onClick={() => setSheet(null)} />}
      {sheet === "away" && (
        <div className="sheet" role="dialog" aria-modal="true" aria-label={t("Usar fora de casa")}>
          <div className="sheet__grab" />
          <h2>{t("Usar fora de casa")}</h2>
          <div className="stack">
            {link.awayFromHome && (
              <div className="banner" style={{ alignItems: "flex-start" }}>
                <Icon name="lock" size={20} />
                <span>{t("Este telemóvel já funciona fora de casa, sem configurar nada. Liga-se ao PC por um reencaminhador do NANO que só passa mensagens cifradas e não as consegue ler. O PC tem de estar ligado, com o NANO aberto.")}</span>
              </div>
            )}
            <p className="hint">{link.awayFromHome
              ? t("Se preferires, também podes usar o Tailscale: uma rede privada só tua, gratuita para uso pessoal.")
              : t("Em casa, o telemóvel fala com o PC pelo Wi-Fi. Para funcionar em qualquer lado sem abrir o teu PC à internet, usa o Tailscale: uma rede privada só tua, gratuita para uso pessoal.")}</p>
            <ol className="steps">
              <li><span>{tj("Instala o {0} no PC e no telemóvel e entra com a mesma conta.", { 0: <b>Tailscale</b> })}</span></li>
              <li><span>{t("No PC, abre o NANO → Definições → Geral → Telemóvel e gera um código novo: o endereço do Tailscale já vai incluído.")}</span></li>
              <li><span>{t("Ou escreve aqui o endereço do PC no Tailscale (começa por 100.).")}</span></li>
            </ol>
            <div className="field">
              <label htmlFor="address">{t("Endereço do PC")}</label>
              <input id="address" className="input" inputMode="url" placeholder="100.101.102.103" value={address}
                onChange={(e) => setAddress(e.target.value)} />
            </div>
            {addressError && <p className="error-text" role="alert">{addressError}</p>}
            <button className="btn btn--primary btn--block" disabled={!address.trim()} onClick={() => void addAddress()}>{t("Adicionar")}</button>
            {info?.extraHosts.map((h) => (
              <div className="row" key={h} style={{ paddingLeft: 0, paddingRight: 0 }}>
                <div className="row__body"><div className="row__title selectable">{h}</div></div>
                <button className="icon-btn" aria-label={t("Remover {0}", { 0: h })}
                  onClick={async () => { await link.removeManualHost(h); force((n) => n + 1); }}><Icon name="trash" size={20} /></button>
              </div>
            ))}
          </div>
        </div>
      )}
      {sheet === "privacy" && (
        <div className="sheet" role="dialog" aria-modal="true" aria-label={t("Política de privacidade")}>
          <div className="sheet__grab" />
          <PrivacyPolicy />
          <button className="btn btn--block" style={{ marginTop: 20 }} onClick={() => setSheet(null)}>{t("Fechar")}</button>
        </div>
      )}
      {sheet === "forget" && (
        <div className="sheet" role="dialog" aria-modal="true" aria-label={t("Esquecer este PC")}>
          <div className="sheet__grab" />
          <h2>{t("Esquecer este PC?")}</h2>
          <p className="hint" style={{ marginBottom: 16 }}>{t("O telemóvel apaga a chave e o PC deixa de o aceitar. As tuas conversas e memórias ficam no PC.")}</p>
          <div className="stack">
            <button className="btn btn--danger btn--block" onClick={async () => { await link.unpair(); setSheet(null); onUnpaired(); }}>{t("Esquecer")}</button>
            <button className="btn btn--block" onClick={() => setSheet(null)}>{t("Cancelar")}</button>
          </div>
        </div>
      )}
    </div>
  );
}
