import React from "react";
import type { ProviderPayload, ReadinessPayload } from "../lib/backend";
import { useFetch } from "../lib/backend";
import { VERSION } from "../lib/version";
import type { Section } from "./SettingsPage";
import { Badge, Button, MetricRow, Panel, StatusIndicator } from "./ui";

/** Setup reuses the normal settings controls: keys never pass through a
 * second form and viewing the guide never enables a service or permission. */
export default function FirstRunGuide({ providers, readiness, saving, onSettings, onFinish, onRefresh }: {
  providers: ProviderPayload | null;
  readiness: ReadinessPayload | null;
  saving: boolean;
  onSettings: (section: Section) => void;
  onFinish: () => void;
  onRefresh: () => void;
}) {
  const { data: location } = useFetch<{ data_dir?: string }>("get_data_location", true);
  const usable = providers?.route?.usable === true;
  return (
    <section className="first-run page-scroll" aria-labelledby="first-run-title">
      <div className="first-run__inner stack">
        <header>
          <Badge tone="accent">{VERSION.display} · Beta</Badge>
          <h2 id="first-run-title" className="page-title">Bem-vindo ao Nano</h2>
          <p className="muted">Escolhe uma forma de conversar. Basta um provedor disponível; podes configurar o resto mais tarde.</p>
        </header>

        <Panel title="1. Liga um provedor de IA">
          <p className="muted">Cloud: adiciona a tua chave em Definições → IA e testa a ligação. As mensagens e o contexto usado na resposta são enviados ao provedor escolhido.</p>
          <p className="muted">Local: instala o Ollama e descarrega um modelo, escolhe-o em Definições → IA e seleciona o modo Local. O Ollama é opcional se usares cloud.</p>
          <MetricRow label="Chat" value={<StatusIndicator state={providers ? (usable ? "READY" : "SETUP_REQUIRED") : "UNKNOWN"}
            label={providers ? (usable ? "Provedor disponível" : "Configura um provedor para conversar") : "A verificar provedores…"} />} />
          <MetricRow label="Ollama (opcional)" value={<StatusIndicator state={providers?.ollama?.state} />} />
          <div className="inline first-run__actions">
            <Button variant="primary" onClick={() => onSettings("ai")}>Configurar IA</Button>
            <Button onClick={onRefresh}>Verificar novamente</Button>
          </div>
        </Panel>

        <Panel title="2. Voz, permissões e dados">
          <MetricRow label="Voz (opcional)" value={<StatusIndicator state={readiness?.voice?.state} />} />
          <p className="muted">Podes escrever sem microfone. Para falar, verifica o microfone e as permissões do Windows em Definições → Voz. A resposta falada usa um serviço Microsoft e envia o texto lido, mesmo em modo Local.</p>
          <p className="muted">As ações sensíveis no PC pedem autorização com a ação, o alvo e o âmbito. Este guia não concede permissões.</p>
          <details className="first-run__details">
            <summary>Onde ficam os meus dados?</summary>
            <p className="muted">Conversas, Memória, definições e permissões ficam neste computador. A desinstalação preserva os dados. O Nano não envia relatórios de diagnóstico automaticamente.</p>
            {location?.data_dir && <code className="first-run__path">{location.data_dir}</code>}
            <p className="dim">Beta: pode haver falhas. Não existe atualização automática; consulta a versão em Definições → Sobre.</p>
          </details>
          <div className="inline first-run__actions">
            <Button size="sm" onClick={() => onSettings("voice")}>Configurar voz</Button>
            <Button size="sm" onClick={() => onSettings("pccontrol")}>Ver segurança do PC</Button>
            <Button size="sm" onClick={() => onSettings("privacy")}>Ver privacidade</Button>
          </div>
        </Panel>

        <div className="first-run__finish">
          <Button variant={usable ? "primary" : "default"} disabled={saving} onClick={onFinish}>
            {saving ? "A guardar…" : usable ? "Começar a conversar" : "Explorar sem configurar"}
          </Button>
          <p className="dim">Podes reabrir este guia em Definições → Sobre.</p>
        </div>
      </div>
    </section>
  );
}
