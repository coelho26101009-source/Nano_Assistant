# Nano

## Instalação

### Requisitos

- Python 3.12 ou superior
- Node.js + npm (apenas para o build do frontend, feito automaticamente no primeiro arranque)
- Git

### Linux

```bash
git clone https://github.com/coelho26101009-source/Nano_Assistant.git
cd Nano_Assistant

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

chmod +x NANO.sh
./NANO.sh
```

`NANO.sh` é o launcher suportado no Linux (equivalente ao `NANO.bat` do
Windows): confirma que existe um `.venv` com as dependências instaladas,
constrói o frontend na primeira execução e arranca o backend. Mantém o
terminal aberto enquanto o Nano estiver a correr; fecha-o ou faz `Ctrl+C`
para parar.

O PyAudio precisa da biblioteca de sistema `portaudio`. Instala-a antes do
`pip install` se ainda não a tiveres:

```bash
# Arch
sudo pacman -S portaudio

# Debian/Ubuntu
sudo apt install portaudio19-dev
```

### Windows

1. Instala o [Python 3.12+](https://www.python.org/downloads/) e garante que
   a opção "Add Python to PATH" fica marcada no instalador.
2. Clona o repositório (ou faz download do ZIP e extrai).
3. Abre um terminal na pasta do projeto e cria o ambiente virtual:

   ```cmd
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

4. Faz duplo-clique em `NANO.bat` (ou corre `NANO.bat` a partir do terminal).

`NANO.bat` é a única forma suportada de arrancar o Nano no Windows. O
launcher valida o Python, constrói o frontend na primeira execução, arranca
o servidor Ollama se ainda não estiver a correr, arranca o backend e a
escuta por voz, e abre a interface uma única vez. Se alguma coisa falhar, a
janela fica aberta com o motivo.

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
