# Nano

## Como iniciar

**Faz duplo-clique em `NANO_DESKTOP.bat`.**

É a forma normal de usar o Nano. Arranca a aplicação de ambiente de trabalho:
janela própria, ícone no tabuleiro do sistema e o atalho global
**Ctrl + Shift + Space** para falar com o Nano a partir de qualquer aplicação.
Nenhum separador do navegador é aberto.

O launcher valida o Python e as dependências, instala o Electron na primeira
execução, constrói o frontend só quando as fontes mudaram, e entrega o controlo
ao Electron — que arranca o motor Python, espera que fique realmente pronto e só
então mostra a janela. Se alguma coisa falhar, a janela fica aberta com o motivo.

Fechar a janela esconde o Nano no tabuleiro, para que o atalho global continue a
funcionar. Para o encerrar mesmo: tabuleiro → **Sair do Nano**.

### Modo navegador (desenvolvimento / alternativa)

`NANO.bat` corre o mesmo motor e abre a interface no navegador, uma única vez.
Não tem tabuleiro, atalho global nem painel de voz. É útil para desenvolvimento
do frontend e como alternativa se o Electron não estiver disponível.

Só um dos dois pode correr de cada vez: o segundo deteta o primeiro e diz-lo,
em vez de arrancar um segundo motor a disputar o microfone.

Arquitetura do desktop em [docs/architecture/DESKTOP.md](docs/architecture/DESKTOP.md).
Documentação completa em [docs/](docs/README.md).

---

Nano é um agente pessoal local-first, multimodal, extensível e seguro, concebido para funcionar como assistente executivo no computador do utilizador.

A identidade oficial do projeto passa a ser Nano. O nome "Nano Assistant" pode aparecer apenas como descrição complementar quando fizer sentido. A arquitetura prioriza:

- processamento local com Ollama
- cloud apenas como fallback/extensão
- memória persistente
- task orchestration
- tool execution com permissões
- background execution
- observabilidade e recuperação de erro
- extensibilidade por plugins

## Visão geral

O Nano não é apenas um chatbot. É um sistema com: 
- Agent brain
- model router
- memory
- context builder
- planner
- task engine
- tool executor
- permission manager
- event bus
- notification manager
- observability

## Arquitetura principal

Nano
├── Agent Brain
├── Model Router
│   ├── Ollama (local-first)
│   └── Cloud providers / fallback
├── Memory
├── Context Engine
├── Planning / Orchestration
├── Task Queue
├── Tool Executor
├── Permission Manager
├── Event Bus
├── Desktop / Browser / Voice / Vision adapters
└── UI Command Center

## Local-first

- memória local em SQLite
- modelos locais com Ollama
- cloud opcional para tarefas que exigirem capacidade extra
- ferramentas instaladas e executadas localmente
- secrets e credenciais devem ser tratadas separadamente das instruções

## Task Engine

O projeto inclui agora uma camada de fila persistente de tarefas com estados e recuperação, permitindo:

- tarefas instantâneas e long-running
- progresso e resultados persistentes
- retries e cancelamento
- estados como QUEUED, RUNNING, WAITING, RETRYING, FAILED, COMPLETED, CANCELLED

## Permissions e segurança

As ferramentas sensíveis devem sempre passar por gestão centralizada de risco. O sistema implementa uma base de:

- classificação por risco
- confirmação para ações de risco alto/crítico
- separação entre orchestration e execução direta

## Atualizações relevantes desta base

- arquitetura de eventos leve
- queue persistente
- orquestrador de tarefas
- contexto relevante por pedido
- perfil do utilizador em memória
- memória de preferências
- documentação do estado e evolução do sistema

## Configuração

O ficheiro principal de configuração continua em [config/settings.yaml](config/settings.yaml).

Documentação técnica: [docs/](docs/README.md).

As variáveis de ambiente relevantes incluem:

```env
NANO_API_KEY=...
GROQ_API_KEY=...
ELEVENLABS_API_KEY=...
OPENAI_API_KEY=...
PICOVOICE_ACCESS_KEY=...
```

## Testes

```bash
python -m pytest -q
```

Isto inclui a suite do Electron (`electron/test/run.js`) e a medição real de
layout em Chromium (`electron/test/render-check.js`), por isso um único comando
verifica o backend, a shell de ambiente de trabalho e a interface.

Para correr só a parte do Electron:

```bash
cd electron  &&  npm test
```

## Próximos passos de evolução

1. Agent orchestrator com execução real de tarefas multi-step
2. background workers para long-running agents
3. browser agent e web research
4. desktop control com permission policies
5. vision + voice providers
6. integrations GitHub / calendar / email / Discord / WhatsApp / IoT
7. UI command center refinada

## Observações de arquitetura

O projeto já tinha uma boa base, mas ainda tinha mistura de nomenclatura e ausência de uma camada central de orchestration. A evolução passa por preservar a base funcional, reforçar a persistência, o contexto, a segurança e a execução de tarefas.
