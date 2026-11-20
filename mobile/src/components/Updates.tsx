/**
 * The phone's update UI: a banner when a new test build exists, and a row in
 * Settings → About. All checks happen in src/lib/updates.ts and on the native
 * side; this file only shows them.
 */
import { useCallback, useEffect, useState } from "react";
import Icon from "./Icon";
import { t } from "../lib/i18n";
import {
  autoCheckDue, autoCheckEnabled, checkForUpdate, installUpdate, setAutoCheck,
  type UpdateCheck, type UpdateOffer,
} from "../lib/updates";

function errorText(code: string): string {
  switch (code) {
    case "offline": return t("Sem internet. Tenta outra vez mais tarde.");
    case "feed_bad_signature":
    case "feed_unknown_key":
    case "feed_bad_url":
    case "feed_wrong_product":
    case "bad_redirect":
    case "download_bad_hash":
    case "download_too_large":
    case "apk_wrong_app":
    case "apk_wrong_signer":
      return t("A atualização não passou na verificação de segurança e foi ignorada.");
    case "apk_not_newer": return t("Já tens esta versão.");
    case "busy": return t("A atualização já está a ser descarregada.");
    default: return t("A atualização falhou. Tenta outra vez mais tarde.");
  }
}

/** Install flow shared by the banner and Settings. */
export function useUpdateInstaller(toast: (text: string) => void) {
  const [progress, setProgress] = useState<number | null>(null);

  const install = useCallback(async (offer: UpdateOffer) => {
    setProgress(0);
    try {
      const result = await installUpdate(offer, setProgress);
      if (result === "needs_permission") {
        toast(t("Permite instalar apps do NANO nas definições do Android e toca outra vez em Atualizar."));
      }
    } catch (error) {
      toast(errorText(error instanceof Error ? error.message : ""));
    } finally {
      setProgress(null);
    }
  }, [toast]);

  return { install, progress };
}

/** Checks once on start when a check is due (at most every 12 hours). */
export function useAutomaticUpdateCheck(): UpdateOffer | null {
  const [offer, setOffer] = useState<UpdateOffer | null>(null);
  useEffect(() => {
    let active = true;
    void (async () => {
      if (!(await autoCheckDue())) return;
      const result = await checkForUpdate();
      if (active && result.status === "available") setOffer(result.offer);
    })();
    return () => { active = false; };
  }, []);
  return offer;
}

export function UpdateBanner({ offer, toast, onDismiss }: {
  offer: UpdateOffer; toast: (text: string) => void; onDismiss: () => void;
}) {
  const { install, progress } = useUpdateInstaller(toast);
  return (
    <div className="banner banner--info" style={{ margin: "12px 16px 0", alignItems: "center" }}>
      <Icon name="refresh" size={20} />
      <span style={{ flex: 1 }}>
        {progress === null
          ? t("Há uma versão nova do NANO (teste {0}).", { 0: offer.versionCode })
          : t("A descarregar… {0}%", { 0: Math.round(progress * 100) })}
      </span>
      {progress === null && (
        <>
          <button className="btn btn--sm btn--primary" onClick={() => void install(offer)}>{t("Atualizar")}</button>
          <button className="icon-btn" aria-label={t("Agora não")} onClick={onDismiss}><Icon name="close" size={18} /></button>
        </>
      )}
    </div>
  );
}

/** Settings → About: what this build is, a manual check and the switch. */
export function UpdateSettings({ toast }: { toast: (text: string) => void }) {
  const [result, setResult] = useState<UpdateCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [auto, setAuto] = useState(true);
  const { install, progress } = useUpdateInstaller(toast);

  useEffect(() => { void autoCheckEnabled().then(setAuto); }, []);

  const check = async () => {
    setChecking(true);
    try { setResult(await checkForUpdate()); } finally { setChecking(false); }
  };

  if (result?.status === "unsupported") {
    return (
      <div className="row">
        <Icon name="refresh" />
        <div className="row__body">
          <div className="row__title">{t("Atualizações")}</div>
          <div className="row__meta">{t("Esta versão é atualizada pela Play Store.")}</div>
        </div>
      </div>
    );
  }

  let meta = t("Procura uma versão de teste nova no GitHub.");
  if (result?.status === "none") meta = t("Tens a versão mais recente (teste {0}).", { 0: result.current });
  if (result?.status === "available") meta = t("Há uma versão nova: teste {0}.", { 0: result.offer.versionCode });
  if (result?.status === "error") meta = errorText(result.code);
  if (progress !== null) meta = t("A descarregar… {0}%", { 0: Math.round(progress * 100) });

  return (
    <>
      <div className="row">
        <Icon name="refresh" />
        <div className="row__body">
          <div className="row__title">{t("Atualizações")}</div>
          <div className="row__meta">{meta}</div>
        </div>
        {result?.status === "available" ? (
          <button className="btn btn--sm btn--primary" disabled={progress !== null}
            onClick={() => void install(result.offer)}>{t("Atualizar")}</button>
        ) : (
          <button className="btn btn--sm" disabled={checking} onClick={() => void check()}>{t("Procurar")}</button>
        )}
      </div>
      <div className="row">
        <div className="row__body">
          <div className="row__title" id="update-auto">{t("Procurar atualizações sozinho")}</div>
          <div className="row__meta">{t("No máximo duas vezes por dia, quando abres a app. Instalar pede sempre a tua confirmação.")}</div>
        </div>
        <button type="button" role="switch" className="switch" aria-checked={auto} aria-labelledby="update-auto"
          onClick={async () => { const next = !auto; setAuto(next); await setAutoCheck(next); }} />
      </div>
    </>
  );
}
