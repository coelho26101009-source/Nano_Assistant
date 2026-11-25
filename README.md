<p align="center">
  <img src="frontend/public/branding/nano-symbol.png" alt="Nano" width="96" />
</p>

<p align="center">
  <img src="frontend/public/branding/nano-wordmark-original.png" alt="NANO" width="340" />
</p>

<p align="center">
  <strong>Assistente pessoal de IA para Windows: voz, modelos cloud e locais, controlo seguro do PC, conversas com histórico real, memória de longo prazo e arquitetura extensível.</strong>
</p>

<p align="center">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-Desktop-111111?style=for-the-badge&logo=windows11&logoColor=8EB5FF" />
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12%2B-111111?style=for-the-badge&logo=python&logoColor=8EB5FF" />
  <img alt="Electron" src="https://img.shields.io/badge/Electron-44-111111?style=for-the-badge&logo=electron&logoColor=8EB5FF" />
  <img alt="Next.js" src="https://img.shields.io/badge/Next.js-16-111111?style=for-the-badge&logo=nextdotjs&logoColor=8EB5FF" />
</p>

<p align="center">
  <a href="https://github.com/coelho97sc/Nano_Assistant/releases/tag/v0.4.0-beta.4"><img alt="Beta publica" src="https://img.shields.io/badge/Beta%20p%C3%BAblica-v0.4.0--beta.4-2F6FED?style=for-the-badge" /></a>
  <img alt="Licenca" src="https://img.shields.io/badge/Licen%C3%A7a-Apache%202.0-111111?style=for-the-badge" />
</p>

<p align="center">
  <a href="https://nanoassistantsite.vercel.app"><strong>Website oficial: nanoassistantsite.vercel.app</strong></a>
</p>

---

> ### Estado do projeto — Beta pública
>
> **Versão atual: `0.4.0-beta.4`.** Esta é a quarta Beta pública do Nano ("Beta 4"),
> na sua terceira atualização (NANO que fala primeiro, voz mais natural, tarefas seguidas). Não é uma versão
> estável e não é 1.0. Espera arestas por limar e guarda cópias do que for
> importante para ti.
>
> O `version.json` diz `0.4.0-beta.4`: é a **versão canónica** que a interface,
> a shell Electron, o backend e o instalador leem para não se contradizerem.
> Segue a política pré-1.0 descrita em [`docs/RELEASING.md`](docs/RELEASING.md).
>
> **Os binários não estão assinados.** O Windows pode mostrar um aviso de
> *Editor desconhecido* ou do SmartScreen. Descarrega apenas a partir da página
> de releases oficial deste repositório e confirma o `SHA256SUMS.txt` antes de
> instalar. Não desligues o SmartScreen nem o Defender para instalar o Nano.
>
> **Atualiza-se sozinho.** A partir da 0.4.0-beta.1, o NANO instalado procura
> versões novas, descarrega-as, confirma a assinatura e instala-as quando o
> fechas. Quem tem a 0.3.0-beta.1 instala esta à mão uma vez. Ver
> [docs/UPDATES.md](docs/UPDATES.md).

## Descarregar

<p align="center">
  <a href="https://github.com/coelho97sc/Nano_Assistant/releases/tag/v0.4.0-beta.4"><strong>Descarregar o Nano 0.4.0-beta.4 para Windows</strong></a>
</p>

| | |
|---|---|
| **Instalador recomendado** | `Nano-Setup-0.4.0-beta.4-x64.exe` |
| **Sistema** | Windows 10/11 **x64** |
| **Verificação** | `SHA256SUMS.txt`, na mesma página de release |
| **Assinatura** | não assinado — ver o aviso acima |
| **Telemóvel (Android)** | `NANO-Android-0.4.0-beta.4.apk`, na mesma página — ver [guia do telemóvel](docs/TESTAR_NO_TELEMOVEL.md) |
| **Todas as versões** | [página de releases](https://github.com/coelho97sc/Nano_Assistant/releases) |

### Instalar

1. Descarrega `Nano-Setup-0.4.0-beta.4-x64.exe` da página de release.
2. Confirma a soma de verificação (abaixo).
3. Executa o instalador. Se o Windows mostrar *Editor desconhecido*, isso é
   esperado numa Beta não assinada: escolhe **Mais informações → Executar
   mesmo assim** depois de teres confirmado a soma de verificação.
4. Abre o Nano e segue o guia da primeira execução.

Para confirmar a descarga, no PowerShell:

```powershell
Get-FileHash .\Nano-Setup-0.4.0-beta.4-x64.exe -Algorithm SHA256
```

O valor tem de coincidir com a linha correspondente em `SHA256SUMS.txt`.

### O que há de novo na Beta 4

A ilha do NANO (Modo ambiente, com a música e o volume), o Segundo Cérebro em
3D, investigação profunda com relatório e fontes, memória de conversas antigas
(M3), a câmara do telemóvel com autorização de cada vez, o telemóvel fora de
casa sempre cifrado, personalidade, protocolos e avisos do PC, e atualizações
automáticas no PC e no telemóvel. A atualização 0.4.0-beta.2 deixa a ilha com
cores sóbrias; a 0.4.0-beta.3 esconde a ilha até ser precisa e traz o "Ei Nano"
no instalador; a 0.4.0-beta.4 faz o NANO falar primeiro (sem ser chato), traz a
voz offline natural, tarefas seguidas e mais segurança na web. Lista completa nas notas da [Beta 4](docs/RELEASE_NOTES_0.4.0-beta.1.md)
e das atualizações ([beta.2](docs/RELEASE_NOTES_0.4.0-beta.2.md),
[beta.3](docs/RELEASE_NOTES_0.4.0-beta.3.md),
[beta.4](docs/RELEASE_NOTES_0.4.0-beta.4.md)), e no
[CHANGELOG](CHANGELOG.md).

### A seguir

- **[Guia da Beta](docs/BETA_GUIDE.md)** — primeira execução, ligar um provedor
  de IA, voz, e o que fazer quando algo corre mal.
- **[Privacidade](PRIVACY.md)** — o que fica no teu computador e o que sai dele.
- **[Segurança](SECURITY.md)** — modelo de segurança e como reportar uma
  vulnerabilidade.
- **[Suporte](SUPPORT.md)** — onde reportar erros e colocar dúvidas.

## Como é

Capturas do NANO 0.3.0-beta.1 com a interface em inglês, feitas num perfil
isolado com uma pessoa fictícia ("Alex") e dados de demonstração. Nenhuma
mostra dados reais nem chaves.

### Ambiente de trabalho

<p align="center">
  <img src="docs/assets/screenshots/nano-home.png" alt="Ecrã inicial do NANO em inglês: conversas à esquerda, cumprimento pelo nome, campo de mensagem e sugestões" width="900" />
</p>

### Conversa com memória e painel lateral

<p align="center">
  <img src="docs/assets/screenshots/nano-chat.png" alt="Plano de estudo curto do modelo local que usa o que o NANO sabe sobre a pessoa" width="440" />
  <img src="docs/assets/screenshots/nano-side-panel.png" alt="Resposta com código Python aberta no painel lateral, ao lado da conversa" width="440" />
</p>

### Modo de voz e Agenda

<p align="center">
  <img src="docs/assets/screenshots/nano-voice-mode.png" alt="Modo de voz em ecrã inteiro, com o estado de escuta simulado" width="440" />
  <img src="docs/assets/screenshots/nano-agenda.png" alt="Agenda com lembretes que se repetem e eventos fictícios" width="440" />
</p>

### Memória

<p align="center">
  <img src="docs/assets/screenshots/nano-memory.png" alt="Página Memória com sete memórias de demonstração criadas pelo utilizador" width="900" />
</p>

### No telemóvel (Android)

<p align="center">
  <img src="docs/assets/screenshots/nano-phone-chat.png" alt="App Android do NANO: conversa sobre o teste de amanhã" width="260" />
  <img src="docs/assets/screenshots/nano-phone-agenda.png" alt="App Android do NANO: agenda com lembretes e eventos" width="260" />
</p>

### A ilha do NANO

<p align="center">
  <img src="docs/assets/screenshots/nano-island.png" alt="A ilha do NANO: a ouvir, a pesquisar na web, à espera de uma aprovação, e o leitor de música" width="760" />
</p>

Os estados da ilha (e a música, com uma capa e um título de demonstração) foram
simulados em Electron para mostrar a apresentação; não representam um teste
humano do microfone nem do Spotify. As capturas do
telemóvel vêm da app a correr com dados de demonstração, sem ligação a um PC
real.

## O que é o Nano?

**Nano** é um assistente pessoal de IA para Windows concebido para viver no
ambiente de trabalho — não apenas numa caixa de chat.

Combina uma aplicação Electron com identidade visual própria, vários provedores
de modelos cloud e um modelo local, voz global através de
**Ctrl + Shift + Space**, conversas persistentes, memória de longo prazo,
ferramentas extensíveis e controlo seguro do Windows. O modelo pode pedir
ações, mas **não recebe autoridade direta sobre o sistema operativo**.

A interface é escura por omissão, com tema claro opcional: superfícies em
carvão, o símbolo N branco com luz azul e acentos azuis contidos. O rail de
conversas fica à esquerda, e a cápsula de voz funciona fora da janela principal.

## O que já existe

| Área | Estado atual |
|---|---|
| **Desktop** | Electron, tray, single-instance, tema escuro por omissão, tema claro opcional e shell responsiva |
| **IA cloud** | **Groq**, **Mistral** e **Google (Gemini)** |
| **IA local** | Ollama, por omissão `qwen3:8b` |
| **Modos** | AUTO / CLOUD / LOCAL, com uma única autoridade de routing |
| **Failover** | Em AUTO: cloud preferida → restantes clouds → Ollama, sempre visível |
| **Conversas** | Threads reais: criar, abrir, renomear, apagar uma ou várias |
| **Memória** | Memória de longo prazo com extração determinística e recuperação (RAG), e lembrança de conversas antigas (M3) |
| **Second Brain** | Nós e relações derivados das memórias ativas, num grafo 3D |
| **Voz** | Hotkey global e "Ei Nano", STT local com faster-whisper, voz Microsoft, voz offline (Piper/Kokoro) ou ElevenLabs, nunca calado |
| **Ilha do NANO** | Modo ambiente no topo do ecrã: estado da voz, música, volume, lembretes e aprovações |
| **Proativo** | Resumo da manhã com o tempo, avisos do PC, sugestões e rotinas com hora, com um filtro anti-incómodo |
| **Tarefas** | Planos de vários passos, protocolos e rotinas por voz, com paragem de emergência |
| **Investigação** | Investigação profunda com relatório, citações e fontes |
| **Telemóvel** | App Android ligada ao PC, cifrada de ponta a ponta, em casa e fora de casa, com câmara a pedido |
| **PC Control** | 60 ferramentas estreitas: apps, janelas, áudio, teclado, ficheiros, web, ecrã, sistema, energia e capturas |
| **Segurança** | Validação de esquema + PolicyEngine + PermissionManager + ToolExecutor + target binding |
| **Extensibilidade** | Sistema de plugins/tools com autorização centralizada |

---

## Início rápido

### Requisitos

- **Windows 10/11 x64**
- **Python 3.12** — a versão testada no CI e a que o instalador embute
- **Node.js 22.12 ou superior + npm** para Electron/frontend na primeira execução
- dependências Python instaladas a partir dos locks com hashes em `requirements/`
- **Ollama** apenas se quiseres usar os modos AUTO/LOCAL
- uma chave de API de pelo menos um provedor cloud, para AUTO/CLOUD. A chave é tua e
  segue as regras desse provedor: a Groq e a API do Gemini pedem 18 anos ou mais, e
  no escalão grátis do Gemini a Google pode usar o que envias para melhorar os
  produtos dela. O modo LOCAL (Ollama) não precisa de conta nenhuma.

```bat
python -m pip install --require-hashes -r requirements\build.lock
python -m pip install --require-hashes --no-build-isolation -r requirements\runtime.lock
```

Os locks fixam a versão exata e o hash de cada pacote, e são os mesmos que o CI
testa e que o instalador embute. `requirements.txt` continua a ser o manifesto
editado à mão; ver [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

Para preparar o modelo local usado por omissão:

```bat
ollama pull qwen3:8b
```

Capacidades opcionais (transcrição local, PDF, automação de browser, wake word)
vivem em `requirements-optional.txt` e não são instaladas por omissão. Para as
instalar no Windows, com as mesmas garantias:

```bat
python -m pip install --require-hashes --no-build-isolation -r requirements\optional.lock
```

### Abrir o Nano Desktop

Faz duplo-clique em:

```text
NANO_DESKTOP.bat
```

O launcher valida o Python e as dependências, instala o Electron na primeira
execução, recompila o frontend apenas quando necessário e entrega o controlo à
shell Electron. A janela só aparece depois de o backend estar pronto.

Fechar a janela principal **esconde o Nano no tray** para que a hotkey global
continue disponível. Para sair completamente, usa **Sair do Nano** no menu do
tray.

### Modo navegador

Para desenvolvimento do frontend ou como alternativa ao Electron:

```text
NANO.bat
```

O modo navegador usa o mesmo backend, mas não inclui tray, hotkey global nem
overlay de voz desktop.

---

## Experiência Desktop

- rail de navegação por **Chat · Ferramentas · PC · Memória · Definições**
- rail de conversas à esquerda, com pesquisa, seleção múltipla e eliminação
- superfícies em carvão, com azul suave reservado para foco e estados ativos
- tema escuro por omissão e tema claro opcional, com tokens semânticos
- símbolo N e wordmark aprovados na aplicação
- ícone próprio no Windows, taskbar e tray
- composer flutuante e responsivo
- Memória, Second Brain e grafo com nós e relações guardados localmente
- cápsula de voz independente da janela principal
- animações com suporte para `prefers-reduced-motion`
- layout validado desde 1920×1080 até ao mínimo da janela Electron

---

## Conversas

As conversas são **threads reais**, não um histórico só de leitura.

- criar uma conversa nova, mudar de conversa e continuar a escrever em qualquer
  uma delas;
- abrir uma conversa antiga **reconstrói o contexto do modelo** a partir das
  mensagens e do resumo dessa thread, para que a resposta seguinte seja dada
  contra o histórico certo;
- renomear, arquivar, apagar uma conversa ou apagar várias numa única ação
  confirmada;
- apagar uma conversa remove as suas mensagens, o resumo, os factos da thread e
  as entradas de índice — mas **não** as memórias de longo prazo que dela
  nasceram, que são geridas à parte na Memória.

Cada conversa tem um dono explícito: uma mensagem pertence à thread ativa no
momento em que foi escrita, e não à que estiver aberta quando a resposta chega.

---

## Memória e Second Brain

**Memória de longo prazo.** O que vale a pena guardar é decidido por
`core/memory_extraction.py`, que é **determinístico e local** — não é uma
chamada ao modelo. Uma frase que pediste explicitamente para guardar fica ativa.
Uma frase que o Nano apenas *inferiu* é pontuada: só uma inferência de
confiança alta se torna memória ativa, uma mais fraca fica como candidata inerte
que podes promover, e abaixo disso não é guardado nada. Preferir falhar uma
memória ambígua a guardar uma errada é intencional.

**Recuperação (RAG).** `core/context_composer.py` é o único sítio que decide o
que o modelo sabe sobre o passado: a cauda recente da conversa, o resumo da
parte mais antiga, mensagens relevantes **desta** thread, memórias de longo
prazo relevantes e as entradas do Second Brain a que dizem respeito. Cada secção
tem o seu próprio orçamento de tokens e tudo é desduplicado, para que um facto
repetido em quatro sítios não seja enviado quatro vezes.

**Conversas antigas (Memória M3).** Cada conversa com pelo menos duas
mensagens tuas fica com uma recordação: resumo, decisões e temas, tirados só de
frases que **tu** escreveste (`core/conversation_recall.py`, extrativo e local,
como o resumo das threads). Quando uma mensagem nova toca no mesmo assunto, ou
perguntas "lembras-te…?" / "o que decidimos…?", a recordação dessa conversa
entra no contexto com o título e a data como fonte, e a resposta mostra
**Lembrei-me de «…»** com um botão para abrir essa conversa. Em **Memória →
Conversas antigas** vês, editas (a tua versão fica), esqueces ou voltas a lembrar
cada uma; apagar a conversa apaga a recordação, e há um interruptor em
Definições → Memória.

**Second Brain.** Nós e relações entre as coisas de que as memórias falam. A
contenção é a escolha de design: um nó só nasce de uma memória ativa ou de uma
ação explícita tua — nunca de texto solto — e uma aresta só é escrita quando
dois nós aparecem na mesma memória.

> **Honestamente:** o Second Brain está implementado e testado, com CRUD, leitura
> do grafo e reconciliação. Não é um grafo de conhecimento maduro e densamente
> povoado — isso vem de uso real prolongado, e ainda não aconteceu.

---

## Modos de IA

O Nano respeita três modos explícitos. Além do modo, existe uma
**cloud preferida** (Definições → IA), que decide qual o provedor cloud a ser
tentado primeiro. São duas definições independentes.

| Modo | Comportamento |
|---|---|
| **CLOUD** | Usa **apenas a cloud preferida**. Não substitui por outro fornecedor e não muda para local. Se falhar, devolve um erro limpo. |
| **AUTO** | Cloud preferida primeiro → restantes clouds pela ordem do sistema → Ollama local como último recurso. |
| **LOCAL** | Usa apenas o Ollama. Nenhum provedor cloud é contactado, nem sequer para uma sonda de estado. |

A ordem das clouds *depois* da preferida é do sistema, não da preferência:

```text
groq → mistral → google → ollama
```

`preferredCloud` decide quem vai primeiro; `CLOUD_PROVIDER_IDS` decide quem vem
a seguir. Podes escolher o Google em primeiro lugar e a ordem restante continua
a ser governada por esse tuplo.

Essa ordem é uma **decisão medida**, exportada em
[`benchmarks/provider_routing/`](benchmarks/provider_routing/README.md), com as
suas ressalvas: uma conta, um dia, uma execução por caso, e o Google
sub-medido por limites de utilização. Não é uma afirmação de que um fornecedor
é objetivamente melhor do que outro.

Em AUTO, o Nano respeita o `Retry-After` real de um 429 e mantém um cooldown por
fornecedor, para não insistir num provedor que sabe estar limitado. Quando a
resposta vem de outro fornecedor que não o escolhido, aparece por baixo dela
quem respondeu e porquê (por exemplo "Groq: limite atingido. Respondeu Mistral.").

Com Ollama, podes escolher um **modelo local rápido** (Definições → IA →
"Modelo rápido para respostas curtas", por exemplo `qwen3:1.7b`): cumprimentos e perguntas curtas usam-no; tools, trabalho
longo e o meio de um turno com tools continuam no modelo principal. O fallback
preserva o mesmo turno, os resultados de tools e as permissões — o
**Execution Ledger** garante que uma ação já executada não é repetida quando o
turno muda de fornecedor.

Mais detalhes: [Model Routing](docs/architecture/MODEL_ROUTING.md)

---

## Voz

A voz faz parte da experiência desktop principal. Pressiona:

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

A transcrição é **inteiramente local**: não existe caminho de STT na cloud no
código. O modelo foi escolhido através de benchmark local e usa uma pequena
pista de vocabulário para nomes importantes do ecossistema Nano.

### Text-to-speech

As respostas faladas usam o serviço de voz **Edge da Microsoft**. Isto vale em
**todos os modos, incluindo LOCAL**: o texto a ser lido sai da máquina para ser
convertido em áudio. "Local" refere-se ao modelo de linguagem, não a silêncio
total de rede. Desliga as respostas faladas em Definições → Voz se quiseres que
nada saia. Ver [PRIVACY.md](PRIVACY.md).

### A ilha do NANO (Modo ambiente)

A antiga cápsula de voz passou a ser a **ilha**: uma pílula preta no topo do
ecrã, sempre por cima das outras aplicações e independente da janela principal,
que muda de forma com o que o NANO está a fazer: a ouvir (com o tempo a
contar), a pensar, a falar, a usar uma ferramenta, à espera de uma aprovação,
um lembrete, um aviso do PC, um download, a música que está a tocar (capa,
título, progresso e botões anterior / pausa / seguinte, lidos dos controlos de
multimédia do Windows) e o volume quando muda. Em repouso fica pequena e pronta
("Modo ambiente", desligável). Clicar abre o NANO; aprovações decidem-se sempre
no NANO, nunca na ilha. Cada estado vem de um evento real do backend. Ver
[DESKTOP.md](docs/architecture/DESKTOP.md#the-nano-island-modo-ambiente).

Documentação: [Voice](docs/VOICE.md) · [Speech Accuracy](docs/architecture/SPEECH_ACCURACY.md)

---

## PC Control

O Nano interage com o Windows através de **60 ferramentas estreitas e
auditáveis** (as tools com prefixo `pc_`; o registo completo, incluindo memória,
calendário, lembretes e web, tem 84 entradas). A ideia é sempre a mesma:
cobertura larga através de muitas capabilities pequenas, nunca através de um
executor genérico.

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

Fechar uma janela envia `WM_CLOSE`, a mesma mensagem do botão X. A aplicação
pode recusar, e nesse caso o Nano relata que recusou: "fechei" e "continuou
aberta" são ambas respostas honestas, "fechado" não seria. O resultado é
verificado contra a identidade da janela (handle **e** processo dono), porque um
handle pode ser reciclado por outra janela.

Ações sensíveis pedem sempre autorização, e o cartão de confirmação mostra o
que vai acontecer, a quê e com que alcance.

Documentação: [PC Control](docs/architecture/PC_CONTROL.md)

---

## Segurança

A regra central do Nano é simples: **o modelo pode pedir; o sistema decide e
executa**. O caminho real, tal como está em `core/tool_execution.py`:

```text
MODEL
  ↓
resolução de capability
  ↓
validação de argumentos (esquema registado)
  ↓
classificação de scope / resolução de target
  ↓
PolicyEngine
  ↓
PermissionManager
  ↓
ToolExecutor
  ↓
TOOL ESTREITA → OS
  ↓
RESULTADO VERIFICADO → auditoria
```

A autorização é centralizada e inclui, conforme a ação:

- classificação por risco
- validação central de esquema antes de a política decidir
- `PolicyEngine`, `PermissionManager` e `ToolExecutor`
- permissões `ALLOW_ONCE` e permissões limitadas à tarefa
- binding por capability + target + scope
- paths protegidos e execution scopes
- confirmação para ações sensíveis
- limites de tamanho/estrutura dos resultados
- falha fechada para tools desconhecidas ou argumentos inválidos

Uma permissão para fechar uma janela específica não se transforma numa
autorização genérica para fechar qualquer outra janela.

Tudo o que o NANO lê da web (pesquisa, "lê esta página", investigação
profunda, o tempo) passa por `core/safe_http.py`: o nome é procurado uma só
vez, no momento de ligar, é recusado se der um endereço privado (o router, o
próprio PC, um aparelho Tailscale) e a ligação vai para o endereço que foi
verificado. O navegador sem janela verifica cada pedido e cada WebSocket que
uma página abre. Escrever um ficheiro que corre ao abrir (`.bat`, `.ps1`, `.lnk`,
`.exe`, …) fora da pasta do projeto é recusado sem perguntar.

Os handlers de plugin nunca são invocados diretamente: o `plugin_loader` recusa
executar um handler a menos que quem chama apresente o ToolExecutor como
autoridade de execução, por isso contornar o pipeline falha fechado.

Política técnica: [Security Policy](docs/SECURITY_POLICY.md) · [SECURITY.md](SECURITY.md)

---

## Arquitetura

```text
┌──────────────────────── Nano Desktop / Electron ────────────────────────┐
│  Nano UI · Tray · Global Hotkey · Voice Overlay · Window Lifecycle      │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ canal de controlo pai/filho (stdio)
                                   ▼
┌──────────────────────────── Python Backend ─────────────────────────────┐
│                                                                         │
│  Brain / resolve_route ──┬─ Groq                                        │
│          │               ├─ Mistral                                     │
│          │               ├─ Google (Gemini)                             │
│          │               └─ Ollama (local, terminal)                    │
│          │                                                              │
│          ├─ Conversas (threads)                                         │
│          ├─ Memória de longo prazo · RAG · Second Brain                 │
│          ├─ Task Engine                                                 │
│          ├─ VoiceRuntime                                                │
│          │                                                              │
│          └─ Schema → Policy → Permission → ToolExecutor                 │
│                                      │                                  │
│                                      └─ Plugins / PC Control            │
│                                                                         │
│  SQLite (uma base de dados, migrada por versão — nunca substituída)     │
└─────────────────────────────────────────────────────────────────────────┘
```

A shell Electron controla o ciclo de vida da aplicação e arranca o backend
Python como processo filho. A execução de tools continua centralizada no
backend; a UI não recebe uma ponte genérica para executar comandos no sistema.

Documentação detalhada: [Desktop Architecture](docs/architecture/DESKTOP.md) · [Architecture](docs/architecture/ARCHITECTURE.md)

---

## Estrutura do projeto

```text
Nano/
├── core/        # brain, providers, memória, segurança, voz e execução
├── plugins/     # tools e integrações autorizadas
├── frontend/    # Next.js + React + interface do Nano
├── electron/    # shell desktop, tray, hotkey e voice overlay
├── config/      # configuração base
├── benchmarks/  # artefactos de medição versionados
├── tests/       # testes backend, segurança e integração
├── docs/        # arquitetura, segurança, voz e design
├── scripts/     # utilitários de desenvolvimento/build
├── NANO_DESKTOP.bat
└── NANO.bat
```

---

## Configuração e secrets

A configuração base vive em `config/settings.yaml`. As preferências do
utilizador são separadas da configuração do repositório.

As chaves de API são configuradas pela própria interface e guardadas no Windows
através de armazenamento seguro baseado em **DPAPI**, por conta e por
fornecedor, em vez de ficarem expostas no frontend. Variáveis de ambiente são
lidas como fallback só de leitura, para que um `.env` existente continue a
funcionar. Ver [PRIVACY.md](PRIVACY.md).

Nunca publiques ficheiros `.env`, logs, gravações de voz, screenshots privadas
ou chaves no repositório.

---

## Testes

```bat
python -m pytest -q          :: backend e contratos da interface
cd electron && npm test      :: shell desktop
cd frontend && npm run build :: bundle de produção
```

O CI no GitHub Actions corre seis verificações em cada push e pull request:

| Job | O que corre |
|---|---|
| **Python tests (ubuntu-latest)** | a suite backend, e uma segunda passagem por ordem baralhada |
| **Python tests (windows-latest)** | a mesma suite na plataforma alvo |
| **Frontend** | typecheck e build de produção |
| **Electron tests** | testes da shell desktop |
| **Chromium UI tests** | 57 testes que carregam o **bundle de produção** no Chromium do próprio Electron, sob `xvfb` |
| **Security static checks** | rejeita `shell=True`, `os.system`, `os.popen`, `eval`, `exec`, e permissões de workflow desnecessárias |

Os testes de Chromium **correm mesmo** — costumavam ser saltados no CI porque
todos os módulos procuravam `electron.exe`, um nome que não pode existir em
Linux, e o skip lia-se como um pass. Deixou de ser assim.

Isto não é cobertura de todo o comportamento possível da UI. O que o job de
Chromium cobre são quatro harnesses conduzidos a partir do pytest —
`render-check` (layout de 1920 até 940×620), `chat-drive`, `settings-drive` e
`memory-render` — mais os contratos de UI que os acompanham. O que **não** corre
no CI: `csp-check`, `overlay-live`, `island-visual` e
`focus-trap-render`, que pertencem ao gate manual de release, o caminho de
renderização em Windows, e a verificação da
aplicação real. Ver [`docs/RELEASING.md`](docs/RELEASING.md).

---

## Limitações atuais

- **Os binários da Beta não estão assinados.** O Windows mostra avisos de
  editor desconhecido; verifica sempre o `SHA256SUMS.txt` da release.
- **O instalador publicado é o NSIS x64.** O MSI é construído e verificado, mas
  ainda não foi validado num ciclo completo de instalação gerida, por isso não
  é publicado nesta Beta.
- **Start with Windows** depende do fluxo de aplicação empacotada
- a primeira execução apresenta um guia opcional; o chat exige pelo menos
  um provedor cloud configurado ou um modelo disponível no Ollama
- as atualizações automáticas só instalam versões assinadas; uma release
  publicada sem a chave de assinatura não é oferecida
- automação de browser existe mas depende do `playwright` opcional; não é
  instalada por omissão
- não há clique por coordenadas: o Nano lê e descreve o ecrã só quando ligas
  essa opção em Privacidade, e cada captura pede autorização
- PC Control é deliberadamente estreito e não oferece shell arbitrária
- o brilho por software depende do monitor (DDC/CI); onde não existe, é
  reportado como tal
- o Second Brain está implementado e testado, mas na prática ainda é esparso
- o "Ei Nano" vem desligado por omissão; liga-se em Definições → Voz
- ainda não foi corrido num telemóvel Android real com dados móveis, nem num
  Windows limpo de raiz
- o fallback local é mais lento que a cloud (≈19 s por turno contra ≈0,45 s)
- os provedores cloud continuam sujeitos aos respetivos rate limits

---

## Próximas áreas

Sem ordem rígida, e sem prometer datas:

- **Validação limpa** — instalação, configuração, uso e desinstalação fora da máquina de desenvolvimento
- **Code signing** — a Beta não está assinada e pode mostrar um aviso SmartScreen
- **Modelo automático** — escolher sozinho o modelo e o provedor certos para cada pedido
- **Coding / GitHub** — workflows de desenvolvimento assistido
- **Produtividade** — email, Home Assistant e outras integrações externas (a Agenda local já existe)

---

## Documentação

Para instalação e configuração da Beta, consulta o [guia da Beta](docs/BETA_GUIDE.md).

| Documento | Conteúdo |
|---|---|
| [Desktop](docs/architecture/DESKTOP.md) | Electron, lifecycle, tray, hotkey e bridge |
| [PC Control](docs/architecture/PC_CONTROL.md) | Tools Windows, capabilities e permissões |
| [Model Routing](docs/architecture/MODEL_ROUTING.md) | Provedores, modos, failover e Execution Ledger |
| [Provider Benchmark](benchmarks/provider_routing/README.md) | A medição por detrás da ordem de fallback |
| [Speech Accuracy](docs/architecture/SPEECH_ACCURACY.md) | Benchmark e decisões de STT |
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
  <img src="frontend/public/branding/nano-symbol.png" alt="Nano mark" width="52" />
</p>

<p align="center">
  <strong>Nano</strong> — em desenvolvimento ativo<br/>
  AI on your desktop. Authority stays with the system.
</p>
