NANO 0.4.0-beta.1 ("Beta 4") is a public Beta: the fourth one, still a pre-release.
It is the first Beta that updates itself.

[Português mais abaixo](#português)

## What's new

- **The NANO island.** A black pill at the top of the screen that changes shape
  for what NANO is doing: listening, thinking, speaking, using a tool, waiting
  for your approval, a reminder, a PC alert. At rest it stays small and ready
  (**Ambient mode**; switch it off in the tray or Settings → General). It also
  shows the **song playing** on the PC (Spotify, a browser, any app in Windows'
  media controls) with its cover and previous / play-pause / next, and a level
  bar when the **volume** changes.
- **The Second Brain in 3D.** The memory graph is now a 3D brain in black space:
  lit spheres, a slow turn, drag to orbit, search to glide to a node.
- **Deep research with sources.** Ask "research … in depth" or "make me a report
  on …": NANO searches from several angles, reads up to eight sites,
  cross-checks the facts and figures, and writes a report with `[n]` citations,
  where the sources disagree and how confident it is. The list of sources is
  written by NANO from the pages it really read, so a link cannot be invented.
- **NANO remembers past conversations (Memory M3).** When you come back to a
  subject, or ask "what did we decide about …?", NANO recalls that conversation
  and shows which one with a chip that opens it. See, edit or forget every recap
  in Memory → Past conversations.
- **The phone's camera, with permission each time.** "What is this?": take a
  photo from the phone app's chat. NANO can also ask to see, but the camera only
  opens when you tap on the phone, and a red sign shows while it is on. Nothing
  is recorded.
- **The phone away from home, always encrypted.** The Android app now works on
  mobile data with nothing to set up. The PC and the phone meet through NANO's
  relay, which only passes along sealed envelopes it cannot read. No port is
  opened on your router. Settings → General → Phone → Away from home.
- **Automatic updates.** From this version on, NANO on the PC checks for a new
  Beta, downloads it in the background, checks its signature and installs it
  when you close NANO (or now, with **Restart and update**). The Android test
  app offers new test builds in a banner. Settings → About → Updates.
- **More JARVIS.** Voice ready in one click (Settings → Voice → "Set up
  voice"), spoken answers that start at the first sentence, a **personality**
  (tone, how NANO addresses you, answer length, your own instructions),
  **protocols** (your routines started by a phrase, like "study mode" or "good
  night"), and **PC alerts** (low battery, disk almost full, network down,
  download finished).
- **Read aloud** under every answer, and a **6-digit code** on both screens when
  you pair a phone.
- **Fixes.** A full review fixed 50 bugs, including safer folder deletes,
  approvals that queue instead of hiding, and the phone surviving a PC restart.

## Downloads

| File | For |
| --- | --- |
| `Nano-Setup-0.4.0-beta.1-x64.exe` | Windows 10/11 x64 installer |
| `NANO-Android-0.4.0-beta.1.apk` | Android 7 or newer (install by hand) |
| `SHA256SUMS.txt` | Checksums of both files |

Check a download in PowerShell with
`Get-FileHash .\Nano-Setup-0.4.0-beta.1-x64.exe -Algorithm SHA256` and compare it
with the line in `SHA256SUMS.txt`.

**Coming from Beta 3?** 0.3.0-beta.1 cannot update itself, so install this
version over it once. Your data in `%LOCALAPPDATA%\NanoAssistant` is kept. After
that, NANO updates itself.

### "Windows protected your PC"

The installer is **not code-signed** yet, so Windows SmartScreen shows "Windows
protected your PC" and "Unknown publisher". That is expected for an unsigned
Beta. Only install a file you downloaded from this page and whose checksum
matches; then choose **More info → Run anyway**. Do not turn off SmartScreen or
Defender to install NANO.

### Installing the Android app

Open this page on the phone, tap the `.apk`, and allow installs from your
browser when Android asks. If Play Protect says the app is unknown, choose
**More details → Install anyway**: this build is not from the Play Store and is
signed with NANO's test key, so it installs over earlier test builds. The full
guide is in [docs/TESTAR_NO_TELEMOVEL.md](https://github.com/coelho97sc/Nano_Assistant/blob/main/docs/TESTAR_NO_TELEMOVEL.md).

## What leaves your computer

- **Deep research:** your question is sent as web searches to DuckDuckGo (Bing
  if DuckDuckGo finds nothing) and Wikipedia, and NANO opens the public pages it
  finds. Only when you ask for research.
- **The phone away from home:** messages pass through NANO's relay on
  Cloudflare, sealed with your phone's key (AES-256-GCM); the relay cannot read
  them and keeps nothing. Turn it off in Settings → General → Phone.
- **The phone's camera:** the photo is described by a vision model, a local one
  first; with no local model, in AUTO or CLOUD mode, it goes to the cloud vision
  provider you use. Only the description is kept.
- **Updates:** the app reads one small file from this GitHub repository every
  few hours. Switch it off in Settings → About → Updates.
- **The island's music** stays on the PC: nothing is asked of Spotify or any web
  service.
- Everything else is as described in [PRIVACY.md](https://github.com/coelho97sc/Nano_Assistant/blob/main/PRIVACY.md).

## New things that act on the computer

- **Island music controls:** previous, play/pause and next, the same as a
  keyboard's media keys.
- **Protocols** run several ordinary actions from one phrase. Each step goes
  through the normal permissions; closing apps, power, deleting and sending
  still ask on their own.
- **Updates** install a new NANO when you close it, only if the installer
  matches a feed signed with the project's key, and never an older version.

## Known limitations

- Beta software. Back up anything important.
- The Windows installer is unsigned (see above).
- Automatic updates and the phone away from home have been tested in CI and in
  the cloud, not yet on many real PCs and phones. If an update fails, NANO tells
  you on the next start and keeps working; you can always install from this
  page by hand.
- **Reading text on screen** uses optional Windows OCR packages that the
  installer does not include yet. Describing the screen with AI works without
  them.
- The Android app is a test build installed by hand; it is not on the Play
  Store yet.
- Installation on a genuinely clean Windows machine is still being validated.

---

## Português

O NANO 0.4.0-beta.1 ("Beta 4") é uma Beta pública: a quarta, ainda uma
pré-versão. É a primeira Beta que se atualiza sozinha.

### O que há de novo

- **A ilha do NANO.** Uma pílula preta no topo do ecrã que muda de forma para o
  que o NANO está a fazer: a ouvir, a pensar, a falar, a usar uma ferramenta, à
  espera da tua autorização, um lembrete, um aviso do PC. Em repouso fica
  pequena e pronta (**Modo ambiente**; desliga no tabuleiro ou em Definições →
  Geral). Mostra também a **música a tocar** no PC (Spotify, um navegador,
  qualquer app nos controlos de multimédia do Windows) com a capa e
  anterior / pausa / seguinte, e uma barra quando o **volume** muda.
- **O Segundo Cérebro em 3D.** O grafo da memória é agora um cérebro em 3D num
  espaço negro: esferas iluminadas, rotação lenta, arrastar para girar, pesquisar
  para deslizar até um nó.
- **Investigação profunda com fontes.** Pede "investiga a fundo …" ou "faz-me
  um relatório sobre …": o NANO pesquisa de vários ângulos, lê até oito sites,
  cruza os factos e os números e escreve um relatório com citações `[n]`, onde
  as fontes discordam e o grau de confiança. A lista de fontes é escrita pelo
  NANO a partir das páginas que leu mesmo, por isso nenhum link é inventado.
- **O NANO lembra-se de conversas antigas (Memória M3).** Quando voltas a um
  assunto, ou perguntas "o que decidimos sobre …?", o NANO lembra-se dessa
  conversa e mostra qual, com um botão que a abre. Vê, edita ou esquece cada
  resumo em Memória → Conversas antigas.
- **A câmara do telemóvel, com autorização de cada vez.** "O que é isto?": tira
  uma foto no chat da app. O NANO também pode pedir para ver, mas a câmara só
  abre quando tocas no telemóvel, e aparece um aviso vermelho enquanto está
  ligada. Nada é gravado.
- **O telemóvel fora de casa, sempre cifrado.** A app Android funciona agora com
  dados móveis, sem configurar nada. O PC e o telemóvel encontram-se no
  intermediário do NANO, que só passa envelopes selados que não consegue ler.
  Nenhuma porta é aberta no router. Definições → Geral → Telemóvel → Fora de
  casa.
- **Atualizações automáticas.** A partir desta versão, o NANO no PC procura uma
  Beta nova, descarrega-a em segundo plano, confirma a assinatura e instala-a
  quando fechas o NANO (ou logo, com **Reiniciar e atualizar**). A app Android
  de teste mostra as versões de teste novas num aviso. Definições → Sobre →
  Atualizações.
- **Mais JARVIS.** Voz pronta com um clique (Definições → Voz → "Preparar a
  voz"), respostas faladas que começam logo na primeira frase, uma
  **personalidade** (tom, como o NANO te trata, tamanho das respostas, as tuas
  instruções), **protocolos** (as tuas rotinas com uma frase, como "modo
  estudo" ou "boa noite") e **avisos do PC** (bateria fraca, disco quase cheio,
  rede em baixo, download acabado).
- **Ler em voz alta** em cada resposta, e um **código de 6 dígitos** nos dois
  ecrãs quando ligas um telemóvel.
- **Correções.** Uma revisão completa corrigiu 50 erros, incluindo apagar pastas
  com mais segurança, autorizações que ficam em fila em vez de se esconderem, e
  o telemóvel a aguentar um reinício do PC.

### Transferências

| Ficheiro | Para |
| --- | --- |
| `Nano-Setup-0.4.0-beta.1-x64.exe` | Instalador para Windows 10/11 x64 |
| `NANO-Android-0.4.0-beta.1.apk` | Android 7 ou mais recente (instalação manual) |
| `SHA256SUMS.txt` | Somas de verificação dos dois ficheiros |

**Vens da Beta 3?** A 0.3.0-beta.1 não se atualiza sozinha, por isso instala
esta versão por cima uma vez. Os teus dados em `%LOCALAPPDATA%\NanoAssistant`
mantêm-se. A partir daí, o NANO atualiza-se sozinho.

### "O Windows protegeu o seu PC"

O instalador ainda **não está assinado**, por isso o SmartScreen do Windows
mostra "O Windows protegeu o seu PC" e "Editor desconhecido". É normal numa Beta
não assinada. Instala só um ficheiro descarregado desta página e com a soma de
verificação certa; depois escolhe **Mais informações → Executar mesmo assim**.
Não desligues o SmartScreen nem o Defender para instalar o NANO.

### Instalar a app Android

Abre esta página no telemóvel, toca no `.apk` e permite instalações do navegador
quando o Android perguntar. Se o Play Protect disser que a app é desconhecida,
escolhe **Mais detalhes → Instalar mesmo assim**: esta versão não vem da Play
Store e está assinada com a chave de teste do NANO, por isso instala por cima
das versões de teste anteriores. O guia completo está em
[docs/TESTAR_NO_TELEMOVEL.md](https://github.com/coelho97sc/Nano_Assistant/blob/main/docs/TESTAR_NO_TELEMOVEL.md).

### O que sai do teu computador

- **Investigação profunda:** a tua pergunta vai como pesquisas para o DuckDuckGo
  (Bing se o DuckDuckGo não encontrar nada) e a Wikipédia, e o NANO abre as
  páginas públicas que encontra. Só quando pedes uma investigação.
- **Telemóvel fora de casa:** as mensagens passam pelo intermediário do NANO na
  Cloudflare, seladas com a chave do teu telemóvel (AES-256-GCM); o
  intermediário não as consegue ler e não guarda nada. Desliga em Definições →
  Geral → Telemóvel.
- **Câmara do telemóvel:** a foto é descrita por um modelo de visão, primeiro um
  local; sem modelo local, em modo AUTO ou CLOUD, vai para o provedor de visão na
  cloud que usas. Só a descrição fica guardada.
- **Atualizações:** a app lê um pequeno ficheiro deste repositório do GitHub de
  algumas em algumas horas. Desliga em Definições → Sobre → Atualizações.
- **A música da ilha** fica no PC: nada é pedido ao Spotify nem a nenhum serviço
  web.

### Coisas novas que agem no computador

- **Controlos de música da ilha:** anterior, pausa e seguinte, o mesmo que as
  teclas de multimédia do teclado.
- **Protocolos** fazem várias ações normais com uma frase. Cada passo passa
  pelas permissões normais; fechar apps, energia, apagar e enviar continuam a
  perguntar à parte.
- **Atualizações** instalam um NANO novo quando o fechas, só se o instalador
  coincidir com um ficheiro assinado com a chave do projeto, e nunca uma versão
  mais antiga.

### Limitações conhecidas

- É uma Beta. Guarda cópia do que for importante.
- O instalador não está assinado (ver acima).
- As atualizações automáticas e o telemóvel fora de casa foram testados no CI e
  na cloud, ainda não em muitos PCs e telemóveis reais. Se uma atualização
  falhar, o NANO avisa no arranque seguinte e continua a funcionar; podes sempre
  instalar à mão a partir desta página.
- **Ler o texto do ecrã** usa pacotes opcionais de OCR do Windows que o
  instalador ainda não inclui. Descrever o ecrã com IA funciona sem eles.
- A app Android é uma versão de teste instalada à mão; ainda não está na Play
  Store.
- A instalação num Windows totalmente limpo ainda está a ser validada.
