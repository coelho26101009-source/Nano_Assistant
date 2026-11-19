/**
 * Settings → Geral → Telemóvel: NANO on the phone.
 *
 * Off until the person turns it on. "Ligar um telemóvel" shows a one-time QR
 * code; when a phone reads it, THIS dialog asks "<phone> quer ligar-se" and
 * nothing is issued until the person approves it here, on the PC. Paired
 * phones are listed and can be removed at any time. See core/mobile_link.py.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { call } from "../lib/backend";
import { t } from "../lib/i18n";
import { tj } from "../lib/tj";
import { Button, MetricRow, Modal, Panel, StatusIndicator, Toggle } from "./ui";

type Address = { address: string; kind: "lan" | "private_network" };
type Device = { id: string; name: string; platform: string; pairedAt?: number; lastSeen?: number };
type MobileStatus = {
  ok: boolean; enabled: boolean; running: boolean; error: string; port: number;
  addresses: Address[]; devices: Device[]; pcName: string;
};
type Pairing = {
  ok: boolean; error?: string; sessionId: string; link: string; qrSvg: string;
  addresses: Address[]; expiresIn: number; remoteReady: boolean;
};
type PairState = { state: "waiting" | "pending" | "approved" | "denied" | "expired"; deviceName?: string };

function when(seconds?: number): string {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleString();
}

function PairDialog({ onClose }: { onClose: (changed: boolean) => void }) {
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [state, setState] = useState<PairState>({ state: "waiting" });
  const [error, setError] = useState("");
  const [left, setLeft] = useState(0);
  const changed = useRef(false);

  const start = useCallback(async () => {
    setError("");
    setState({ state: "waiting" });
    const result = await call<Pairing>("mobile_start_pairing");
    if (!result?.ok) { setError(t("Não foi possível gerar o código. Confirma que o acesso do telemóvel está ligado.")); return; }
    setPairing(result);
    setLeft(result.expiresIn);
  }, []);

  useEffect(() => { void start(); }, [start]);

  useEffect(() => {
    if (!pairing) return;
    const timer = window.setInterval(async () => {
      setLeft((value) => Math.max(0, value - 1.5));
      const next = await call<PairState>("mobile_pairing_state", pairing.sessionId);
      if (next) setState((prev) => (prev.state === "approved" ? prev : next));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [pairing]);

  const decide = async (approve: boolean) => {
    if (!pairing) return;
    const result = await call<{ ok: boolean; state?: string }>("mobile_decide_pairing", pairing.sessionId, approve);
    if (result?.ok && approve) { changed.current = true; setState({ state: "approved", deviceName: state.deviceName }); }
    else if (result?.ok) setState({ state: "denied", deviceName: state.deviceName });
  };

  const close = () => {
    if (state.state !== "approved") void call("mobile_cancel_pairing");
    onClose(changed.current);
  };

  return (
    <Modal open onClose={close} title={t("Ligar um telemóvel")} width="narrow"
      footer={<Button onClick={close}>{state.state === "approved" ? t("Concluído") : t("Fechar")}</Button>}>
      {error && <p className="field__error">{error}</p>}
      {pairing && state.state === "waiting" && (
        <div className="stack" style={{ alignItems: "center", textAlign: "center" }}>
          <p className="muted" style={{ fontSize: 14 }}>{t("Abre a app NANO no telemóvel, escolhe \"Ligar ao meu PC\" e lê este código.")}</p>
          {pairing.qrSvg
            ? <img src={`data:image/svg+xml;utf8,${encodeURIComponent(pairing.qrSvg)}`} width={232} height={232}
                alt={t("Código QR para ligar o telemóvel")} style={{ background: "#fff", borderRadius: 12, padding: 8 }} />
            : <code style={{ wordBreak: "break-all", fontSize: 11 }}>{pairing.link}</code>}
          <p className="dim" style={{ fontSize: 12 }}>
            {left > 0 ? t("O código expira em {0} s e só serve uma vez.", { 0: Math.ceil(left) }) : t("O código expirou.")}
          </p>
          {left <= 0 && <Button size="sm" onClick={() => void start()}>{t("Gerar um código novo")}</Button>}
          <p className="dim" style={{ fontSize: 12 }}>
            {pairing.remoteReady
              ? t("Tailscale detetado: o telemóvel também vai funcionar fora de casa.")
              : t("O telemóvel tem de estar no mesmo Wi-Fi. Para usar fora de casa, instala o Tailscale no PC e no telemóvel.")}
          </p>
          <details style={{ fontSize: 12, width: "100%", textAlign: "left" }}>
            <summary className="dim">{t("Sem câmara? Copia o código")}</summary>
            <code style={{ wordBreak: "break-all", display: "block", marginTop: 6, userSelect: "all" }}>{pairing.link}</code>
          </details>
        </div>
      )}
      {state.state === "pending" && (
        <div className="stack">
          <p style={{ fontSize: 16, fontWeight: 600 }}>{t("\"{0}\" quer ligar-se a este NANO.", { 0: state.deviceName || t("Telemóvel") })}</p>
          <p className="muted" style={{ fontSize: 14 }}>{t("Só permitas se for o teu telemóvel e o tiveres acabado de ligar. Um telemóvel ligado pode falar com o NANO, ver as tuas conversas, agenda e memória, e responder aos pedidos de autorização.")}</p>
          <div className="inline">
            <Button onClick={() => void decide(false)}>{t("Recusar")}</Button>
            <Button variant="primary" onClick={() => void decide(true)}>{t("Permitir")}</Button>
          </div>
        </div>
      )}
      {state.state === "approved" && (
        <p style={{ fontSize: 15 }}>{t("Pronto. \"{0}\" está ligado ao NANO.", { 0: state.deviceName || t("Telemóvel") })}</p>
      )}
      {state.state === "denied" && <p className="muted">{t("Recusado. Nenhum acesso foi dado.")}</p>}
      {state.state === "expired" && pairing && (
        <div className="stack">
          <p className="muted">{t("O código expirou.")}</p>
          <Button size="sm" onClick={() => void start()}>{t("Gerar um código novo")}</Button>
        </div>
      )}
    </Modal>
  );
}

export default function PhonePanel() {
  const [status, setStatus] = useState<MobileStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [pairing, setPairing] = useState(false);
  const [removing, setRemoving] = useState<Device | null>(null);

  const refresh = useCallback(async () => {
    const result = await call<MobileStatus>("get_mobile_status");
    if (result) setStatus(result);
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  if (!status) return null;

  const toggle = async (value: boolean) => {
    setBusy(true);
    try {
      const result = await call<MobileStatus>("set_mobile_access", value);
      if (result) setStatus(result);
    } finally { setBusy(false); }
  };

  const remove = async (device: Device) => {
    setRemoving(null);
    await call("mobile_remove_device", device.id);
    await refresh();
  };

  const listening = status.enabled && status.running;
  return (
    <Panel title={t("Telemóvel")}>
      <p className="muted" style={{ fontSize: 13, marginBottom: 10 }}>{t("Usa o NANO no telemóvel Android, com as mesmas conversas, memória e agenda. Tudo vai cifrado entre o telemóvel e este PC, e nada fica aberto à internet.")}</p>
      <Toggle
        label={t("Acesso do telemóvel")}
        hint={t("Deixa telemóveis emparelhados falar com este NANO pela tua rede. Desligado, nada fica à escuta.")}
        checked={status.enabled}
        disabled={busy}
        onChange={(value) => void toggle(value)}
      />
      {status.enabled && (
        <>
          <MetricRow label={t("Estado")} value={
            <StatusIndicator state={listening ? "READY" : "ERROR"}
              label={listening ? t("À escuta na porta {0}", { 0: status.port })
                : status.error === "port_in_use" ? t("A porta {0} está ocupada", { 0: status.port }) : t("Não foi possível ligar")} />
          } />
          <MetricRow label={t("Endereços")} value={
            status.addresses.length
              ? status.addresses.map((a) => `${a.address}${a.kind === "private_network" ? " (Tailscale)" : ""}`).join(", ")
              : t("Nenhuma rede encontrada")} />
          <div className="inline" style={{ marginTop: 12 }}>
            <Button variant="primary" size="sm" disabled={!listening} onClick={() => setPairing(true)}>{t("Ligar um telemóvel")}</Button>
          </div>
          <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>{tj("Na primeira vez, o Windows pode perguntar pela firewall: escolhe {0}.", { 0: <strong>{t("Redes privadas")}</strong> })}</p>
        </>
      )}
      {status.devices.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className="field__label" style={{ marginBottom: 6 }}>{t("Telemóveis ligados")}</div>
          {status.devices.map((device) => (
            <div key={device.id} className="inline" style={{ justifyContent: "space-between", padding: "6px 0", borderTop: "1px solid var(--border)" }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 500 }}>{device.name}</div>
                <div className="dim" style={{ fontSize: 11 }}>{t("Visto pela última vez: {0}", { 0: when(device.lastSeen) })}</div>
              </div>
              <Button size="sm" variant="danger" onClick={() => setRemoving(device)}>{t("Remover")}</Button>
            </div>
          ))}
        </div>
      )}
      {pairing && <PairDialog onClose={(changed) => { setPairing(false); if (changed) void refresh(); }} />}
      {removing && (
        <Modal open onClose={() => setRemoving(null)} title={t("Remover telemóvel")} width="narrow"
          footer={<>
            <Button onClick={() => setRemoving(null)}>{t("Cancelar")}</Button>
            <Button variant="danger" onClick={() => void remove(removing)}>{t("Remover")}</Button>
          </>}>
          <p className="muted" style={{ fontSize: 14 }}>{t("\"{0}\" deixa de poder falar com o NANO. Para voltar, tem de ser ligado de novo.", { 0: removing.name })}</p>
        </Modal>
      )}
    </Panel>
  );
}
