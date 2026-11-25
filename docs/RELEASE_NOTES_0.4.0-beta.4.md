NANO 0.4.0-beta.4 is the third update to "Beta 4": NANO now speaks first without being annoying, its voice is more natural, it carries out several tasks in a row, and it is safer on the web. Still a pre-release.

[Português mais abaixo](#português)

## What's new

- **NANO speaks first, without being annoying.** A morning briefing with the
  weather (only if you type a city), alerts such as "memory almost full" or
  "battery charged", suggestions from your habits, and routines with a time
  (PC → Modos). Everything goes through one filter: a level you choose, quiet
  hours, "don't bother me for an hour", full-screen games and being away from
  the PC. Notices can be said out loud (Settings → Geral).
- **A more natural voice.** Besides Microsoft's voice you can install an
  offline natural voice (Settings → Voz → **Instalar voz offline**), or use
  ElevenLabs with your own key. If one voice fails, the next one speaks: NANO
  is never silent. No gaps between sentences.
- **Several tasks at once.** Ask for a few things in one sentence ("open Notion
  and Chrome, put Chrome on the left and the volume at 30"); NANO does each
  step, checks it, and tells you what did not get done. Safe plans no longer ask
  "Go ahead?"; anything dangerous still asks.
- **Speech recognition works again** on an installed NANO ("Ei Nano" and the
  microphone). It was broken by a library version inside the installer.
- **Safer on the web.** Every web page NANO reads is fetched from the address
  it checked, the hidden browser checks each request, and the coding helper
  cannot drop scripts outside your project.
- **A more careful clean-up** ("limpa a cache do Windows"): only files untouched
  for 24 hours, and it says which files were in use or not allowed.
- **A note when another AI answers.** If your preferred provider fails and
  another one answers, a line under the answer says so. An optional small local
  model (Settings → IA) can answer short questions fast.
- **Phone pairing is safer** (protocol 3). **To pair a phone, update both the
  PC and the phone app.** Phones already paired keep working.
- **35 bugs fixed** from a full review (notices, routines, plans, voice, memory,
  phone link, updates).
- **The GitHub account has a new name** (`coelho97sc`). This update teaches NANO
  the new address; older NANOs still find this update because GitHub redirects
  the old one.

## Getting it

- **You have 0.4.0-beta.1, beta.2 or beta.3 installed:** nothing to do. NANO
  finds this update by itself within a few hours, downloads it, checks its
  signature and installs it when you close NANO. To get it now: Settings →
  About → Updates → **Check now**, then **Restart and update**.
- **You have Beta 3 or older:** install `Nano-Setup-0.4.0-beta.4-x64.exe` from
  this page over it once. Your data in `%LOCALAPPDATA%\NanoAssistant` is kept.
- **Android test app:** the app shows a banner when a newer test build exists,
  or install `NANO-Android-0.4.0-beta.4.apk` from this page. If you already
  paired a phone, it keeps working; to pair a new one, update both.

## Downloads

| File | For |
| --- | --- |
| `Nano-Setup-0.4.0-beta.4-x64.exe` | Windows 10/11 x64 installer |
| `NANO-Android-0.4.0-beta.4.apk` | Android 7 or newer (install by hand) |
| `SHA256SUMS.txt` | Checksums of both files |

Check a download in PowerShell with
`Get-FileHash .\Nano-Setup-0.4.0-beta.4-x64.exe -Algorithm SHA256` and compare it
with the line in `SHA256SUMS.txt`.

The installer is **not code-signed** yet, so Windows SmartScreen shows "Windows
protected your PC" and "Unknown publisher". Only install a file you downloaded
from this page and whose checksum matches; then choose **More info → Run
anyway**. Do not turn off SmartScreen or Defender to install NANO.

## What leaves your computer

New in this update, and only when you use it: the weather (a city you typed
goes to Open-Meteo, nothing else), the offline voice and its speech packs
(downloads you start with a button), and ElevenLabs (only if you choose it and
add your key). The Android app's microphone uses Android's own speech
recognition, which is usually Google's. Everything is listed in
[PRIVACY.md](https://github.com/coelho97sc/Nano_Assistant/blob/main/PRIVACY.md).

## Known limitations

The same as Beta 4: the installer is unsigned, automatic updates, the phone away
from home and the new voices have not yet been tested on many real PCs and
phones, and the Android app is a test build installed by hand. The offline
Portuguese voice is for personal, non-commercial use. "Ei Nano" understands
Portuguese best.

---

## Português

O NANO 0.4.0-beta.4 é a terceira atualização da "Beta 4": o NANO passa a falar primeiro sem ser chato, a voz está mais natural, faz várias tarefas seguidas e está mais seguro na web. Continua a ser uma pré-versão.

### O que há de novo

- **O NANO fala primeiro, sem ser chato.** Resumo da manhã com o tempo (só se
  escreveres uma cidade), avisos como "memória quase cheia" ou "bateria
  carregada", sugestões pelos teus hábitos e rotinas com hora (PC → Modos).
  Tudo passa por um filtro: um nível à tua escolha, horas de silêncio,
  "não me incomodes durante uma hora", jogos em ecrã inteiro e quando não estás
  ao PC. Os avisos podem ser ditos em voz alta (Definições → Geral).
- **Uma voz mais natural.** Além da voz da Microsoft, podes instalar uma voz
  natural offline (Definições → Voz → **Instalar voz offline**) ou usar o
  ElevenLabs com a tua chave. Se uma voz falhar, fala a seguinte: o NANO nunca
  fica calado. Sem pausas entre frases.
- **Várias tarefas de uma vez.** Pede umas quantas coisas na mesma frase ("abre
  o Notion e o Chrome, põe o Chrome à esquerda e o volume a 30"); o NANO faz
  cada passo, verifica e diz-te o que não ficou feito. Planos seguros já não
  perguntam "Avanço?"; o que é perigoso continua a pedir autorização.
- **O reconhecimento de voz volta a funcionar** no NANO instalado ("Ei Nano" e o
  microfone). Estava estragado por uma versão de uma biblioteca dentro do
  instalador.
- **Mais seguro na web.** Cada página que o NANO lê é pedida ao endereço que ele
  verificou, o navegador escondido verifica cada pedido, e o ajudante de código
  não escreve scripts fora do teu projeto.
- **Limpeza mais cuidadosa** ("limpa a cache do Windows"): só ficheiros sem
  mexer há 24 horas, e diz quais estavam em uso ou sem permissão.
- **Uma nota quando responde outra IA.** Se o teu fornecedor preferido falhar e
  outro responder, uma linha por baixo da resposta di-lo. Um modelo local
  pequeno e opcional (Definições → IA) responde depressa a perguntas curtas.
- **O emparelhamento do telemóvel está mais seguro** (protocolo 3). **Para
  emparelhar um telemóvel, atualiza o PC e a app do telemóvel.** Os telemóveis
  já emparelhados continuam a funcionar.
- **35 erros corrigidos** numa revisão completa (avisos, rotinas, planos, voz,
  memória, ligação ao telemóvel, atualizações).
- **A conta do GitHub tem um nome novo** (`coelho97sc`). Esta atualização ensina
  o novo endereço ao NANO; os NANO mais antigos continuam a encontrar esta
  atualização porque o GitHub redireciona o endereço antigo.

### Como obter

- **Tens a 0.4.0-beta.1, beta.2 ou beta.3 instalada:** não precisas de fazer
  nada. O NANO encontra esta atualização sozinho em poucas horas, descarrega-a,
  confirma a assinatura e instala-a quando o fechas. Para a ter já:
  Definições → Sobre → Atualizações → **Procurar agora**, depois
  **Reiniciar e atualizar**.
- **Tens a Beta 3 ou mais antiga:** instala `Nano-Setup-0.4.0-beta.4-x64.exe`
  desta página por cima, uma vez. Os teus dados em
  `%LOCALAPPDATA%\NanoAssistant` mantêm-se.
- **App Android de teste:** a app mostra um aviso quando há uma versão de teste
  nova, ou instala `NANO-Android-0.4.0-beta.4.apk` desta página. Um telemóvel já
  emparelhado continua a funcionar; para emparelhar um novo, atualiza os dois.

### Transferências

| Ficheiro | Para |
| --- | --- |
| `Nano-Setup-0.4.0-beta.4-x64.exe` | Instalador para Windows 10/11 x64 |
| `NANO-Android-0.4.0-beta.4.apk` | Android 7 ou mais recente (instalação manual) |
| `SHA256SUMS.txt` | Somas de verificação dos dois ficheiros |

O instalador ainda **não está assinado**, por isso o SmartScreen do Windows
mostra "O Windows protegeu o seu PC" e "Editor desconhecido". Instala só um
ficheiro descarregado desta página e com a soma de verificação certa; depois
escolhe **Mais informações → Executar mesmo assim**. Não desligues o SmartScreen
nem o Defender para instalar o NANO.

### O que sai do teu computador

Novo nesta atualização, e só quando usas: o tempo (a cidade que escreveste vai
para o Open-Meteo, mais nada), a voz offline e os seus pacotes (descargas que
começas com um botão) e o ElevenLabs (só se o escolheres e puseres a tua chave).
O microfone da app Android usa o reconhecimento de voz do próprio Android, que
costuma ser o da Google. Está tudo listado em
[PRIVACY.md](https://github.com/coelho97sc/Nano_Assistant/blob/main/PRIVACY.md).

### Limitações conhecidas

As mesmas da Beta 4: o instalador não está assinado, as atualizações
automáticas, o telemóvel fora de casa e as vozes novas ainda não foram testados
em muitos PCs e telemóveis reais, e a app Android é uma versão de teste
instalada à mão. A voz offline em português é só para uso pessoal e não
comercial. O "Ei Nano" percebe melhor português.
