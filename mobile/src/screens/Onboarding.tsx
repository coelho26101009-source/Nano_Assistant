/**
 * First run: what NANO on the phone is, pairing with the PC, and notifications.
 * Three short steps, the way the PC's first-run guide is three short steps.
 */
import { useEffect, useRef, useState } from "react";
import Icon from "../components/Icon";
import { tj } from "../components/Tj";
import { NanoMark, NanoWordmark } from "../components/Brand";
import { t } from "../lib/i18n";
import { pair, parsePairLink, type PairState } from "../lib/link";
import { askNotifications, deviceName, isNative, scanQr, tap } from "../lib/native";

type Step = "welcome" | "pair" | "waiting" | "notifications";

export default function Onboarding({ onPaired, startAt = "welcome" }: { onPaired: () => void; startAt?: Step }) {
  const [step, setStep] = useState<Step>(startAt);
  const [state, setState] = useState<PairState | "">("");
  const [error, setError] = useState("");
  const [paste, setPaste] = useState("");
  const [showPaste, setShowPaste] = useState(!isNative());
  const [pcName, setPcName] = useState("");
  const cancel = useRef({ cancelled: false });

  useEffect(() => () => { cancel.current.cancelled = true; }, []);

  const begin = async (code: string) => {
    setError("");
    let parsed;
    try {
      parsed = parsePairLink(code);
    } catch (err) {
      setError((err as Error).message === "unsupported_version"
        ? t("Este código é de uma versão mais recente do NANO. Atualiza a app.")
        : t("Este não é um código do NANO. No PC, abre Definições → Geral → Telemóvel."));
      return;
    }
    setPcName(parsed.pcName);
    setStep("waiting");
    cancel.current = { cancelled: false };
    const result = await pair(parsed, deviceName(), "android", (s) => setState(s), cancel.current);
    if (result) {
      void tap();
      setStep("notifications");
    }
  };

  const scan = async () => {
    setError("");
    try {
      const code = await scanQr();
      if (code) await begin(code);
    } catch {
      setError(t("Não consegui abrir o leitor de códigos. Podes colar o código em vez disso."));
      setShowPaste(true);
    }
  };

  if (step === "welcome") {
    return (
      <main className="onboard">
        <div className="onboard__hero">
          <NanoMark size={64} />
          <NanoWordmark height={30} />
          <h1>{t("O teu NANO, no bolso.")}</h1>
          <p>{t("Fala com o NANO do teu PC a partir de qualquer lado.")}</p>
          <ul className="onboard__points">
            <li><Icon name="chat" /><span>{t("O mesmo NANO: as mesmas conversas, memória e agenda do teu PC.")}</span></li>
            <li><Icon name="lock" /><span>{t("Privado: tudo o que passa entre o telemóvel e o PC vai cifrado, e nada fica aberto à internet.")}</span></li>
            <li><Icon name="shield" /><span>{t("Tu decides: as ações no PC continuam a pedir a tua autorização.")}</span></li>
          </ul>
        </div>
        <div className="onboard__actions">
          <button className="btn btn--primary btn--block" onClick={() => setStep("pair")}>{t("Ligar ao meu PC")}</button>
        </div>
      </main>
    );
  }

  if (step === "pair") {
    return (
      <main className="onboard">
        <div className="onboard__hero" style={{ justifyContent: "flex-start", alignItems: "stretch", textAlign: "left" }}>
          <button className="icon-btn" onClick={() => setStep("welcome")} aria-label={t("Voltar")}><Icon name="back" /></button>
          <h1>{t("Liga o telemóvel ao PC")}</h1>
          <ol className="steps">
            <li><span>{tj("No PC, abre o NANO e vai a {0}.", { 0: <b>{t("Definições → Geral → Telemóvel")}</b> })}</span></li>
            <li><span>{tj("Liga {0} e carrega em {1}.", { 0: <b>{t("Acesso do telemóvel")}</b>, 1: <b>{t("Ligar um telemóvel")}</b> })}</span></li>
            <li><span>{t("Lê o código QR que aparece no PC e confirma no PC.")}</span></li>
          </ol>
          <div className="banner banner--info">
            <Icon name="wifi" size={20} />
            <span>{t("Na primeira vez, o telemóvel e o PC têm de estar na mesma rede Wi-Fi.")}</span>
          </div>
          {error && <p className="error-text" role="alert">{error}</p>}
          {showPaste && (
            <div className="field">
              <label htmlFor="paste">{t("Código do PC")}</label>
              <textarea id="paste" className="textarea" rows={3} value={paste} placeholder="nano://pair?…"
                onChange={(e) => setPaste(e.target.value)} />
              <button className="btn btn--block" disabled={!paste.trim()} onClick={() => void begin(paste)}>{t("Usar este código")}</button>
            </div>
          )}
        </div>
        <div className="onboard__actions">
          {isNative() && <button className="btn btn--primary btn--block" onClick={() => void scan()}><Icon name="qr" />{t("Ler o código QR")}</button>}
          {isNative() && !showPaste && <button className="btn btn--ghost btn--block" onClick={() => setShowPaste(true)}>{t("Colar o código em vez disso")}</button>}
        </div>
      </main>
    );
  }

  if (step === "waiting") {
    const failed = state === "denied" || state === "expired" || state === "unreachable" || state === "error";
    return (
      <main className="onboard">
        <div className="onboard__hero">
          {!failed && <div className="spinner" aria-hidden="true" />}
          {failed && <Icon name="alert" size={40} />}
          <div className="waiting" aria-live="polite">
            {state === "connecting" || state === "" ? <h1>{t("A ligar ao {0}…", { 0: pcName })}</h1> : null}
            {state === "pending" && <>
              <h1>{t("Confirma no PC")}</h1>
              <p>{t("O PC está a perguntar se \"{0}\" se pode ligar. Carrega em Permitir no PC.", { 0: deviceName() })}</p>
            </>}
            {state === "denied" && <><h1>{t("O PC recusou")}</h1><p>{t("Se foste tu, gera um código novo no PC e tenta outra vez.")}</p></>}
            {state === "expired" && <><h1>{t("O código expirou")}</h1><p>{t("Os códigos duram 5 minutos. Gera um novo no PC.")}</p></>}
            {state === "unreachable" && <>
              <h1>{t("Não encontrei o PC")}</h1>
              <ul className="onboard__points">
                <li><Icon name="wifi" /><span>{t("Confirma que o telemóvel está no mesmo Wi-Fi que o PC.")}</span></li>
                <li><Icon name="monitor" /><span>{t("Confirma que o NANO está aberto no PC e que o acesso do telemóvel está ligado.")}</span></li>
                <li><Icon name="shield" /><span>{t("Se o Windows perguntou pela firewall, escolhe permitir em redes privadas.")}</span></li>
              </ul>
            </>}
            {state === "error" && <><h1>{t("Algo correu mal")}</h1><p>{t("Gera um código novo no PC e tenta outra vez.")}</p></>}
          </div>
        </div>
        <div className="onboard__actions">
          {failed
            ? <button className="btn btn--primary btn--block" onClick={() => { setState(""); setStep("pair"); }}>{t("Tentar outra vez")}</button>
            : <button className="btn btn--ghost btn--block" onClick={() => { cancel.current.cancelled = true; setStep("pair"); }}>{t("Cancelar")}</button>}
        </div>
      </main>
    );
  }

  return (
    <main className="onboard">
      <div className="onboard__hero">
        <Icon name="bell" size={44} />
        <h1>{t("Ligado ao {0}", { 0: pcName })}</h1>
        <p>{t("Queres que os lembretes do NANO toquem no telemóvel? Funcionam mesmo com a app fechada.")}</p>
      </div>
      <div className="onboard__actions">
        <button className="btn btn--primary btn--block" onClick={async () => { await askNotifications(); onPaired(); }}>{t("Permitir notificações")}</button>
        <button className="btn btn--ghost btn--block" onClick={onPaired}>{t("Agora não")}</button>
      </div>
    </main>
  );
}
