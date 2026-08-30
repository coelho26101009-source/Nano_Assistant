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
| **PC Control** | Apps, janelas, volume, brilho, teclado, ficheiros, web, definições, energia e capturas |
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

## PC Control V2

O Nano interage com o Windows através de **56 ferramentas estreitas e
auditáveis**. A ideia é sempre a mesma: cobertura larga através de muitas
capabilities pequenas, nunca através de um executor genérico.

### Aplicações e janelas

- procurar e abrir aplicações instaladas — incluindo apps da Microsoft Store
- mudar para uma aplicação já aberta, e listar o que está aberto
- focar, minimizar, maximizar, restaurar e fechar janelas
- mover, redimensionar, centrar e encostar janelas a metades e cantos
- mandar uma janela para outro monitor, ou mantê-la sempre à frente
- minimizar, restaurar ou fechar todas as janelas de uma aplicação

### Som, ecrã e teclado

- consultar e alterar volume, mute / unmute
- reproduzir, pausar e saltar faixas
- ler e alterar o brilho dos monitores que o suportam
- escrever texto e usar atalhos numa janela indicada
- ler, escrever e limpar a área de transferência

### Ficheiros, web e sistema

- abrir pastas conhecidas e documentos seguros
- pesquisar ficheiros de forma limitada
- criar pastas e ficheiros de texto, copiar, mover e mudar nomes
- enviar ficheiros e pastas para a **Reciclagem** (nunca apagar definitivamente)
- abrir endereços e pesquisas no navegador predefinido
- abrir secções das Definições do Windows
- consultar CPU, RAM, disco, GPU, bateria, ligação e armazenamento
- bloquear, suspender, reiniciar, desligar ou terminar sessão
- capturar o ecrã, a janela ativa ou uma janela indicada

Exemplos:

```text
Abre a calculadora
Mete a calculadora à esquerda
Muda para o Discord
Escreve "olá" no Bloco de Notas
Qual é o volume atual?
Baixa o brilho
Abre as definições de som
Abre o YouTube
Cria uma pasta chamada Notas no Ambiente de Trabalho
Como está a memória do computador?
```

O PC Control é **intencionalmente estreito**. Não existe nenhuma tool genérica
de PowerShell/CMD/shell exposta ao modelo, não há terminação de processos, não
há eliminação permanente de ficheiros, e o Nano recusa escrever numa janela de
consola — abrir um terminal e escrever nele seria uma shell montada a partir de
duas ações inofensivas.

Ações sensíveis pedem sempre autorização, e o cartão de confirmação mostra o
que vai acontecer, a quê e com que alcance.

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
│  Ember UI · Tray · Global Hotkey · Voice Overlay · Window Lifecycle     │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ parent/child control channel
                                   ▼
┌──────────────────────────── Python Backend ─────────────────────────────┐
│                                                                         │
│  Brain / Model Routing ── Groq                                          │
│          │             └─ Ollama                                        │
│          │                                                              │
│          ├─ Memory / Context                                            │
│          ├─ Task Engine                                                 │
│          ├─ VoiceRuntime                                                │
│          │                                                              │
│          └─ Policy → Permission → ToolExecutor                          │
│                                      │                                  │
│                                      └─ Plugins / PC Control            │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
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
- não há clique por coordenadas nem OCR: o Nano não vê o ecrã, e não finge ver
- o brilho por software depende do monitor (DDC/CI); onde não existe, é reportado como tal
- conversas antigas ainda são de leitura, sem threads completas independentes
- anexos ainda estão marcados como “brevemente”
- wake phrase permanece experimental/desativada por omissão
- o fallback local é mais lento que Groq
- providers cloud continuam sujeitos aos respetivos rate limits

---

## Roadmap

Próximas áreas de evolução, sem ordem rígida:

- **Browser/Web** — pesquisa e automação web segura
- **Vision / OCR** — ler o ecrã, com controlos de privacidade próprios
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

### Projeto e comunidade

| Documento | Conteúdo |
|---|---|
| [Privacidade](PRIVACY.md) | O que é guardado, onde, e o que sai do computador |
| [Segurança](SECURITY.md) | Modelo de segurança e como reportar uma vulnerabilidade |
| [Contribuir](CONTRIBUTING.md) | Setup, testes e como propor uma capacidade em segurança |
| [Suporte](SUPPORT.md) | Onde colocar bugs, ideias, dúvidas e vulnerabilidades |
| [Código de conduta](CODE_OF_CONDUCT.md) | Contributor Covenant 2.1 |
| [Changelog](CHANGELOG.md) | O que mudou, por marco |
| [Licenças de terceiros](THIRD_PARTY_NOTICES.md) | Dependências e respetivas licenças (rascunho) |
| [Releasing](docs/RELEASING.md) | Versionamento e o processo de release futuro |
| [Checklist de lançamento](docs/PUBLIC_RELEASE_CHECKLIST.md) | O que falta para uma beta pública |

> **Licença:** Nano está licenciado sob a [Apache License 2.0](LICENSE). É uma
> licença permissiva: permite reutilização, modificação e redistribuição,
> incluindo em contexto comercial, e não obriga aplicações modificadas a serem
> de código aberto. Isto não elimina as obrigações de dependências de terceiros
> — ver [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), nomeadamente as
> dependências LGPL.

---

<p align="center">
  <img src="frontend/public/branding/nano-mark-alpha.png" alt="Nano mark" width="52" />
</p>

<p align="center">
  <strong>Nano v1.0</strong><br/>
  AI on your desktop. Authority stays with the system.
</p>
