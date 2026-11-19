NANO 0.3.0-beta.1 ("Beta 3") is a public Beta: the third one, still a pre-release.

[Português mais abaixo](#português)

## What's new

- **English by default, Portuguese too.** NANO follows your system language and
  answers in the language you write in. Change it in Settings → General.
- **Voice in English and continuous conversation.** Speech recognition, NANO's
  voice and the wake phrase ("hey nano" / "ei nano") follow the language. After
  an answer NANO keeps listening for a few seconds, so you can just keep
  talking. New full-screen voice mode: **Ctrl+Shift+M**.
- **NANO sees.** With your permission it can describe what is on your screen,
  read the text on it, and keep an eye on it for a while ("let me know when the
  download finishes"). You can attach **PDFs, images and text files** to a
  message.
- **Memory that knows you.** NANO remembers facts about you without repeating
  itself, refuses to store passwords, PINs, card numbers or things said about
  other people, and links what it knows to your agenda.
- **NANO speaks first.** A morning briefing with today's events, a heads-up a few
  minutes before each one, and an offer to help you get ready the evening before
  an exam, interview or trip. Turn it off in Settings → General.
- **Local AI, guided.** Settings → AI → "AI on your PC: guided setup" checks your
  PC, recommends one light model that writes, sees and uses tools, and downloads
  it on one click. Free, no limits, nothing leaves the PC in LOCAL mode.
- **Tasks of several steps.** For a request that takes three or more actions,
  NANO shows the plan once and carries it out after your yes.
- **NANO on your phone (Android).** A companion app for the NANO on your PC: the
  same chats, memory and agenda, voice, reminders that ring with the app closed.
  Pair it with a QR code in Settings → General → Phone. Every message is
  end-to-end encrypted, and it only talks to your own PC (same Wi-Fi, or
  Tailscale away from home).
- **Everyday polish.** A three-step first run, a personal home page, approvals
  inside the chat, pinned chats and projects, "Open beside" for long answers,
  retry and edit, recurring reminders ("every weekday at 8"), an Agenda page,
  Windows notifications, work modes, and a startup screen instead of a black
  window.

## Downloads

| File | For |
| --- | --- |
| `Nano-Setup-0.3.0-beta.1-x64.exe` | Windows 10/11 x64 installer |
| `NANO-Android-0.3.0-beta.1.apk` | Android 7 or newer (install by hand) |
| `SHA256SUMS.txt` | Checksums of both files |

Check a download in PowerShell with
`Get-FileHash .\Nano-Setup-0.3.0-beta.1-x64.exe -Algorithm SHA256` and compare it
with the line in `SHA256SUMS.txt`.

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
guide is in [docs/TESTAR_NO_TELEMOVEL.md](https://github.com/coelho26101009-source/Nano_Assistant/blob/main/docs/TESTAR_NO_TELEMOVEL.md).

## What leaves your computer

- **Seeing the screen and images:** in AUTO or CLOUD mode the picture goes to the
  cloud vision provider you use (Groq, Mistral or Google). In LOCAL mode only an
  Ollama model on your PC is used. Off until you turn it on in Settings →
  Privacy, and it asks every time.
- **Phone:** the phone talks only to your own PC, encrypted end to end. NANO
  opens no port to the internet.
- Everything else is as described in [PRIVACY.md](https://github.com/coelho26101009-source/Nano_Assistant/blob/main/PRIVACY.md).

## New things that act on the computer

- **Plans of several steps** run ordinary PC Control actions after one yes.
  Closing apps, power, deleting and sending still ask on their own.
- **Watching the screen** only tells you; it never acts.
- **Reminders** are now created without a confirmation card.

## Known limitations

- Beta software. Back up anything important.
- The Windows installer is unsigned (see above). There is no automatic updater:
  install this version over the previous one; your data in
  `%LOCALAPPDATA%\NanoAssistant` is kept.
- **Speaking to NANO on the PC** needs the optional local speech package
  (faster-whisper), which the installer does not include yet; see
  [docs/VOICE.md](https://github.com/coelho26101009-source/Nano_Assistant/blob/main/docs/VOICE.md).
  Spoken replies work out of the box, and so does voice in the Android app.
- **Reading text on screen** (and watching for text) uses optional Windows OCR
  packages that the installer does not include yet. Describing the screen with
  AI works without them.
- The Android app is a test build installed by hand; it is not on the Play
  Store yet.
- Installation on a genuinely clean Windows machine is still being validated.

---

## Português

O NANO 0.3.0-beta.1 ("Beta 3") é uma Beta pública: a terceira, ainda uma
pré-versão.

### O que há de novo

- **Inglês por omissão, português também.** O NANO segue a língua do sistema e
  responde na língua em que escreves. Muda em Definições → Geral.
- **Voz em inglês e conversa contínua.** O reconhecimento de fala, a voz do NANO
  e a frase de ativação ("ei nano" / "hey nano") seguem a língua. Depois de
  responder, o NANO continua a ouvir uns segundos, por isso podes continuar a
  falar. Novo modo de voz em ecrã inteiro: **Ctrl+Shift+M**.
- **O NANO vê.** Com a tua autorização descreve o que está no ecrã, lê o texto
  e fica de olho durante algum tempo ("avisa-me quando o download acabar").
  Podes anexar **PDFs, imagens e ficheiros de texto** a uma mensagem.
- **Memória que te conhece.** Lembra-se de factos sobre ti sem se repetir,
  recusa guardar palavras-passe, PINs, números de cartão ou coisas ditas sobre
  outras pessoas, e liga o que sabe à tua agenda.
- **O NANO fala primeiro.** Um resumo de manhã com os eventos do dia, um aviso
  uns minutos antes de cada um, e uma oferta de ajuda na véspera de um teste,
  entrevista ou viagem. Desliga em Definições → Geral.
- **IA local, guiada.** Definições → IA → "IA no teu PC: configuração guiada" vê
  o teu PC, recomenda um modelo leve que escreve, vê e usa ferramentas, e
  descarrega-o com um clique. Grátis, sem limites, e em modo LOCAL nada sai do
  PC.
- **Tarefas de vários passos.** Para um pedido com três ou mais ações, o NANO
  mostra o plano uma vez e executa-o depois do teu sim.
- **O NANO no telemóvel (Android).** Uma app que acompanha o NANO do teu PC: as
  mesmas conversas, memória e agenda, voz, e lembretes que tocam com a app
  fechada. Liga-se com um código QR em Definições → Geral → Telemóvel. Todas as
  mensagens vão cifradas de ponta a ponta e só falam com o teu PC (mesmo Wi-Fi,
  ou Tailscale fora de casa).
- **Melhorias do dia a dia.** Primeira execução em três passos, página inicial
  pessoal, aprovações dentro do chat, conversas fixadas e projetos, "Abrir ao
  lado" para respostas longas, tentar de novo e editar, lembretes que se
  repetem ("nos dias úteis às 8"), página Agenda, notificações do Windows, modos
  de trabalho, e um ecrã de arranque em vez de uma janela preta.

### Transferências

| Ficheiro | Para |
| --- | --- |
| `Nano-Setup-0.3.0-beta.1-x64.exe` | Instalador para Windows 10/11 x64 |
| `NANO-Android-0.3.0-beta.1.apk` | Android 7 ou mais recente (instalação manual) |
| `SHA256SUMS.txt` | Somas de verificação dos dois ficheiros |

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
[docs/TESTAR_NO_TELEMOVEL.md](https://github.com/coelho26101009-source/Nano_Assistant/blob/main/docs/TESTAR_NO_TELEMOVEL.md).

### O que sai do teu computador

- **Ver o ecrã e imagens:** em modo AUTO ou CLOUD a imagem vai para o provedor
  de visão na cloud que usas (Groq, Mistral ou Google). Em modo LOCAL só é usado
  um modelo do Ollama no teu PC. Está desligado até o ligares em Definições →
  Privacidade, e pergunta sempre.
- **Telemóvel:** o telemóvel só fala com o teu PC, cifrado de ponta a ponta. O
  NANO não abre nenhuma porta para a internet.

### Limitações conhecidas

- É uma Beta. Guarda cópia do que for importante.
- O instalador não está assinado (ver acima) e não há atualização automática:
  instala esta versão por cima da anterior; os teus dados em
  `%LOCALAPPDATA%\NanoAssistant` mantêm-se.
- **Falar com o NANO no PC** precisa do pacote opcional de fala local
  (faster-whisper), que o instalador ainda não inclui; ver
  [docs/VOICE.md](https://github.com/coelho26101009-source/Nano_Assistant/blob/main/docs/VOICE.md).
  As respostas faladas funcionam logo, e a voz na app Android também.
- **Ler o texto do ecrã** (e vigiar texto) usa pacotes opcionais de OCR do
  Windows que o instalador ainda não inclui. Descrever o ecrã com IA funciona
  sem eles.
- A app Android é uma versão de teste instalada à mão; ainda não está na Play
  Store.
- A instalação num Windows totalmente limpo ainda está a ser validada.
