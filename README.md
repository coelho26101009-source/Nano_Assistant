<p align="center">
  <img src="frontend/public/branding/nano-mark-alpha.png" alt="Nano" width="96" />
</p>

<p align="center">
  <img src="frontend/public/branding/nano-wordmark-alpha.png" alt="NANO" width="340" />
</p>

<h1 align="center">Nano v1.0</h1>

<p align="center">
  <strong>Assistente pessoal de IA para Windows, com voz, modelos cloud/local, controlo seguro do PC, memória e arquitetura extensível.</strong>
</p>

<p align="center">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-Desktop-111111?style=for-the-badge&logo=windows11&logoColor=F40101" />
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12%2B-111111?style=for-the-badge&logo=python&logoColor=F40101" />
  <img alt="Electron" src="https://img.shields.io/badge/Electron-30-111111?style=for-the-badge&logo=electron&logoColor=F40101" />
  <img alt="Next.js" src="https://img.shields.io/badge/Next.js-14-111111?style=for-the-badge&logo=nextdotjs&logoColor=F40101" />
</p>

---

## O que é o Nano?

**Nano** é um assistente pessoal de IA para Windows concebido para viver no ambiente de trabalho — não apenas numa caixa de chat.

Combina uma aplicação Electron com identidade visual própria, modelos cloud e locais, voz global através de **Ctrl + Shift + Space**, memória persistente, ferramentas extensíveis e controlo seguro do Windows. O modelo pode pedir ações, mas **não recebe autoridade direta sobre o sistema operativo**.

A versão atual usa a interface **Ember**, uma experiência desktop em preto e vermelho com superfícies glass, navegação superior, histórico de conversas e overlay de voz independente da janela principal.

## Destaques do Nano v1.0

| Área | Estado atual |
|---|---|
| **Desktop** | Electron, tray, single-instance, interface Ember e shell responsiva |
| **IA cloud** | Groq como provider principal |
| **IA local** | Ollama com `qwen3:8b` |
| **Failover** | Modo AUTO: Groq → Ollama em falhas transitórias/rate limit |
| **Voz** | Hotkey global, STT local com faster-whisper e TTS |
| **PC Control** | Apps, janelas, volume, pastas, ficheiros, sistema e screenshots |
| **Segurança** | PolicyEngine + PermissionManager + ToolExecutor + target binding |
| **Memória** | Persistência local e contexto do utilizador |
| **Extensibilidade** | Sistema de plugins/tools com autorização centralizada |

---

## Início rápido

### Requisitos

- **Windows 10/11 x64**
- **Python 3.12 ou superior**
- **Node.js + npm** para Electron/frontend na primeira execução
- dependências Python instaladas com `requirements.txt`
- **Ollama** apenas se quiseres usar os modos AUTO/LOCAL

```bat
python -m pip install -r requirements.txt
```

Para preparar o modelo local usado atualmente:

```bat
ollama pull qwen3:8b
```

### Abrir o Nano Desktop

Faz duplo-clique em:

```text
NANO_DESKTOP.bat
```

O launcher valida o Python e as dependências, instala o Electron na primeira execução, recompila o frontend apenas quando necessário e entrega o controlo à shell Electron. A janela só aparece depois de o backend estar pronto.

Fechar a janela principal **esconde o Nano no tray** para que a hotkey global continue disponível. Para sair completamente, usa **Sair do Nano** no menu do tray.

### Modo navegador

Para desenvolvimento do frontend ou como alternativa ao Electron:

```text
NANO.bat
```

O modo navegador usa o mesmo backend, mas não inclui tray, hotkey global nem overlay de voz desktop.

---

## Experiência Desktop — Ember

A interface Nano v1.0 foi desenhada para funcionar como uma aplicação desktop, não como um dashboard web genérico.

- top bar flutuante com navegação por **Chat · Ferramentas · PC · Memória · Definições**
- rail de conversas à esquerda
- superfícies glass em preto e vermelho Nano (`#F40101`)
- wordmark e marca oficial em toda a aplicação
- ícone próprio no Windows, taskbar e tray
- composer flutuante e responsivo
- animações com suporte para `prefers-reduced-motion`
- layout validado desde 1920×1080 até ao mínimo da janela Electron

As conversas antigas são atualmente abertas em **modo de leitura**; o backend ainda não tem threads independentes com restauração completa de contexto.

---

## Modos de IA

O Nano respeita três modos explícitos:

| Modo | Comportamento |
|---|---|
| **CLOUD** | Usa Groq apenas. Se a cloud falhar, devolve um erro limpo e não muda para local. |
| **AUTO** | Groq é o principal. Em falhas transitórias/rate limit, continua o mesmo turno com Ollama. |
| **LOCAL** | Usa apenas Ollama. Não faz pedidos Groq no hot path. |

No modo AUTO, o Nano mantém um cooldown leve para não insistir repetidamente na Groq enquanto o provider está temporariamente limitado. O fallback preserva o mesmo turno, resultados de tools e permissões para evitar ações duplicadas.

O modelo local atual é:

```text
qwen3:8b
```

É mais lento que a cloud, mas permite continuar a conversar e usar tools quando a Groq está indisponível.

Mais detalhes: [Model Routing](docs/architecture/MODEL_ROUTING.md)

---

## Voz

A voz faz parte da experiência desktop principal.

Pressiona:

```text
Ctrl + Shift + Space
```

para falar com o Nano a partir de qualquer aplicação.

### Speech-to-text

A configuração de produção atual usa:

```text
faster-whisper
model: small
device: cpu
compute_type: int8
language: pt
```

O modelo foi escolhido através de benchmark local e usa uma pequena pista de vocabulário para nomes importantes do ecossistema Nano.

### Overlay Ember

O overlay de voz é uma janela Electron própria, sempre no topo e independente da janela principal. Mostra estados distintos para:

- Listening
- Transcribing
- Processing
- Speaking
- Busy
- Error

Continua a funcionar quando a janela principal está minimizada ou escondida no tray.

Documentação: [Voice](docs/VOICE.md) · [Speech Accuracy](docs/architecture/SPEECH_ACCURACY.md)

---

## PC Control V1

O Nano já consegue interagir com o Windows através de ferramentas estreitas e auditáveis.

### Aplicações e janelas

- procurar aplicações instaladas
- abrir aplicações conhecidas
- listar janelas
- focar, minimizar, maximizar e restaurar janelas
- fechar janelas de forma graciosa

### Sistema

- consultar e alterar volume
- mute / unmute
- abrir pastas conhecidas
- pesquisar ficheiros de forma limitada
- abrir documentos seguros
- consultar CPU, RAM, disco, GPU, bateria e uptime
- capturar screenshots com confirmação

Exemplos:

```text
Abre a calculadora
Minimiza a calculadora
Qual é o volume atual?
Abre a pasta Downloads
Mostra as janelas abertas
Como está a memória do computador?
```

O PC Control V1 é **intencionalmente limitado**. Não existe uma tool genérica de PowerShell/CMD/shell exposta ao modelo, nem fallback para terminar processos à força.

Documentação: [PC Control](docs/architecture/PC_CONTROL.md)

---

## Segurança

A regra central do Nano é simples: **o modelo pode pedir; o sistema decide e executa**.

```text
MODEL
  ↓
REQUEST
  ↓
POLICY
  ↓
PERMISSION
  ↓
ToolExecutor
  ↓
TOOL
  ↓
REAL RESULT
```

A autorização é centralizada e inclui, conforme a ação:

- classificação por risco
- `PolicyEngine`
- `PermissionManager`
- `ToolExecutor`
- permissões `ALLOW_ONCE`
- permissões limitadas à tarefa
- binding por capability + target + scope
- paths protegidos e execution scopes
- confirmação para ações sensíveis
- limites de tamanho/estrutura dos resultados
- falha fechada para tools desconhecidas ou argumentos inválidos

Uma permissão para fechar uma janela específica, por exemplo, não se transforma numa autorização genérica para fechar qualquer outra janela.

Política técnica: [Security Policy](docs/SECURITY_POLICY.md)

---

## Arquitetura

```text
┌──────────────────────── Nano Desktop / Electron ────────────────────────┐
│  Ember UI · Tray · Global Hotkey · Voice Overlay · Window Lifecycle   │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ parent/child control channel
                                   ▼
┌──────────────────────────── Python Backend ─────────────────────────────┐
│                                                                       │
│  Brain / Model Routing ── Groq                                       │
│          │             └─ Ollama                                     │
│          │                                                            │
│          ├─ Memory / Context                                           │
│          ├─ Task Engine                                                │
│          ├─ VoiceRuntime                                               │
│          │                                                             │
│          └─ Policy → Permission → ToolExecutor                         │
│                                      │                                │
│                                      └─ Plugins / PC Control           │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

A shell Electron controla o ciclo de vida da aplicação e arranca o backend Python como processo filho. A execução de tools continua centralizada no backend; a UI não recebe uma ponte genérica para executar comandos no sistema.

Documentação detalhada: [Desktop Architecture](docs/architecture/DESKTOP.md) · [Architecture](docs/architecture/ARCHITECTURE.md)

---

## Estrutura do projeto

```text
Nano_Assistant/
├── core/        # brain, providers, memória, segurança, voz e execução
├── plugins/     # tools e integrações autorizadas
├── frontend/    # Next.js + React + interface Ember
├── electron/    # shell desktop, tray, hotkey e voice overlay
├── config/      # configuração base
├── tests/       # testes backend, segurança e integração
├── docs/        # arquitetura, segurança, voz e design
├── scripts/     # utilitários de desenvolvimento/build
├── NANO_DESKTOP.bat
└── NANO.bat
```

---

## Configuração e secrets

A configuração base vive em:

```text
config/settings.yaml
```

Preferências do utilizador são separadas da configuração do repositório sempre que aplicável.

A chave Groq pode ser configurada através da própria interface e é guardada no Windows através do armazenamento seguro baseado em **DPAPI**, em vez de ficar exposta no frontend.

Nunca publiques ficheiros `.env`, logs, gravações de voz, screenshots privadas ou chaves no repositório.

---

## Testes

Suite principal:

```bat
python -m pytest -q
```

Testes Electron:

```bat
cd electron
npm test
```

Frontend:

```bat
cd frontend
npm run build
```

A suite inclui testes de segurança, PC Control, failover Groq→Ollama, desktop shell e verificações reais de layout Chromium.

---

## Limitações atuais

Nano v1.0 ainda está em desenvolvimento ativo.

- o runtime Python empacotado para distribuição final ainda precisa de ser concluído
- **Start with Windows** depende do fluxo de aplicação empacotada
- browser automation ainda não faz parte do produto atual
- PC Control é deliberadamente estreito e não oferece shell arbitrária
- conversas antigas ainda são de leitura, sem threads completas independentes
- anexos ainda estão marcados como “brevemente”
- wake phrase permanece experimental/desativada por omissão
- o fallback local é mais lento que Groq
- providers cloud continuam sujeitos aos respetivos rate limits

---

## Roadmap

Próximas áreas de evolução, sem ordem rígida:

- **PC Control V2** — mais aplicações e controlo do Windows mantendo capabilities estreitas
- **Browser/Web** — pesquisa e automação web segura
- **Conversation Threads** — histórico real por thread e restauração de contexto
- **Memory / RAG** — recuperação e contexto mais ricos
- **Coding / GitHub** — workflows de desenvolvimento assistido
- **Produtividade** — calendário, email e integrações externas
- **Packaging** — installer/runtime Python totalmente autocontido
- **Public Release Hardening** — CSP, privacidade, licenças e auditoria final de segurança

---

## Documentação

| Documento | Conteúdo |
|---|---|
| [Desktop](docs/architecture/DESKTOP.md) | Electron, lifecycle, tray, hotkey e bridge |
| [PC Control](docs/architecture/PC_CONTROL.md) | Tools Windows, capabilities e permissões |
| [Speech Accuracy](docs/architecture/SPEECH_ACCURACY.md) | Benchmark e decisões de STT |
| [Model Routing](docs/architecture/MODEL_ROUTING.md) | Groq, Ollama e routing |
| [Security Policy](docs/SECURITY_POLICY.md) | Política de capabilities e aprovação |
| [Voice](docs/VOICE.md) | Runtime de voz, STT/TTS e wake |
| [Design](docs/design/README.md) | Identidade e decisões visuais do Nano |

---

<p align="center">
  <img src="frontend/public/branding/nano-mark-alpha.png" alt="Nano mark" width="52" />
</p>

<p align="center">
  <strong>Nano v1.0</strong><br/>
  AI on your desktop. Authority stays with the system.
</p>
