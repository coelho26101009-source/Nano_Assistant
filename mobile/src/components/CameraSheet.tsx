/**
 * The camera, full screen, for ONE photo. Opened only by a tap of the person:
 * the camera button in the chat, or "Abrir a câmara" on NANO's request card.
 *
 * While it is open the red "Câmara ligada" sign sits over the picture. Every
 * way out -- the shutter, the close button, Android's back button, the app
 * going to the background -- turns the camera off (lib/camera.ts).
 */
import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";
import { capture, closeCamera, openCamera, type Photo } from "../lib/camera";
import { t } from "../lib/i18n";
import { tap } from "../lib/native";
import { pushOverlay } from "../lib/overlay";

export default function CameraSheet({ title, note, onPhoto, onCancel }: {
  /** What this photo is for, e.g. the question NANO asked. */
  title: string;
  /** Where the photo will be seen, and that nothing is recorded. */
  note: string;
  onPhoto: (photo: Photo) => void;
  onCancel: () => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [facing, setFacing] = useState<"environment" | "user">("environment");
  const [live, setLive] = useState(false);
  const [error, setError] = useState("");
  const done = useRef(false);
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;

  useEffect(() => {
    let disposed = false;
    setLive(false);
    setError("");
    void openCamera(facing).then((media) => {
      if (disposed || done.current) { closeCamera(media); return; }
      stream.current = media;
      const el = video.current;
      if (el) {
        el.srcObject = media;
        void el.play().catch(() => { /* autoplay is allowed for muted video */ });
      }
      setLive(true);
    }).catch((err: Error) => {
      if (disposed) return;
      setError(err.message === "camera_denied"
        ? t("O NANO precisa de acesso à câmara. Podes dar acesso nas definições do Android.")
        : t("Não consegui abrir a câmara."));
    });
    return () => {
      disposed = true;
      closeCamera(stream.current);
      stream.current = null;
      if (video.current) video.current.srcObject = null;
    };
  }, [facing]);

  // Sent to the background (home button, screen off): the camera goes off and
  // nothing is sent. The person can open it again.
  useEffect(() => {
    const onHidden = () => {
      if (document.visibilityState === "hidden" && !done.current) {
        done.current = true;
        closeCamera(stream.current);
        cancelRef.current();
      }
    };
    document.addEventListener("visibilitychange", onHidden);
    return () => document.removeEventListener("visibilitychange", onHidden);
  }, []);

  // Android's back button closes the camera (and nothing else).
  useEffect(() => pushOverlay(() => cancelRef2.current()), []);

  const shoot = () => {
    const el = video.current;
    if (!el || done.current) return;
    const photo = capture(el);
    if (!photo) { setError(t("Não consegui tirar a foto. Tenta outra vez.")); return; }
    done.current = true;
    closeCamera(stream.current);
    stream.current = null;
    void tap();
    onPhoto(photo);
  };

  const cancel = () => {
    if (done.current) return;
    done.current = true;
    closeCamera(stream.current);
    stream.current = null;
    onCancel();
  };

  const cancelRef2 = useRef(cancel);
  cancelRef2.current = cancel;

  return (
    <div className="camera" role="dialog" aria-modal="true" aria-label={t("Câmara")}>
      <video ref={video} className="camera__video" playsInline muted autoPlay
        style={facing === "user" ? { transform: "scaleX(-1)" } : undefined} />
      <div className="camera__top">
        <button className="icon-btn camera__btn" onClick={cancel} aria-label={t("Fechar a câmara")}><Icon name="close" /></button>
        {live && (
          <span className="camera__live" role="status">
            <span className="camera__dot" aria-hidden="true" />{t("Câmara ligada")}
          </span>
        )}
        <button className="icon-btn camera__btn" onClick={() => setFacing(facing === "environment" ? "user" : "environment")}
          aria-label={t("Trocar de câmara")} disabled={!live}><Icon name="refresh" /></button>
      </div>
      <div className="camera__bottom">
        <p className="camera__title">{title}</p>
        <p className="camera__note">{note}</p>
        {error && <p className="camera__error" role="alert">{error}</p>}
        <button className="camera__shutter" onClick={shoot} disabled={!live} aria-label={t("Tirar a foto e mostrar ao NANO")}>
          <span />
        </button>
      </div>
    </div>
  );
}
