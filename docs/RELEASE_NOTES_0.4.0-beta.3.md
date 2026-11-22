NANO 0.4.0-beta.3 is the second update to "Beta 4": the island stays out of the way, and "Ei Nano" works. Still a pre-release.

[Português mais abaixo](#português)

## What's new

- **The island stays hidden until it is needed.** It comes out for a voice
  turn, an approval, a reminder or alert, a tool at work, the volume or a new
  song, and when you push the mouse against the top centre of the screen.
  Then it slides back up by itself. Want the old always-on pill? Tray menu →
  **Keep the island on screen**.
- **"Ei Nano" works on an installed NANO.** Speech recognition now comes inside
  the installer (before, it was missing, so "Ei Nano" did nothing). Turn it on
  in Settings → Voz; the first time, NANO downloads its voice model (about
  480 MB) and starts listening as soon as it is in. NANO now waits for you to
  speak, stops recording when you pause, and a request in the same breath
  ("Ei Nano, abre o Spotify") goes straight through.
- **A muted microphone is said, not ignored.** If Windows or your headset's
  mute button has the microphone muted, the island shows "Microfone sem som"
  instead of NANO silently never hearing you.
- **The island follows your music.** Skipping songs with the keyboard (Fn+F3)
  now updates the song and its cover on the island right away.

## Getting it

- **You have 0.4.0-beta.1 or 0.4.0-beta.2 installed:** nothing to do. NANO
  finds this update by itself within a few hours, downloads it, checks its
  signature and installs it when you close NANO. To get it now: Settings →
  About → Updates → **Check now**, then **Restart and update**.
- **You have Beta 3 or older:** install `Nano-Setup-0.4.0-beta.3-x64.exe` from
  this page over it once. Your data in `%LOCALAPPDATA%\NanoAssistant` is kept.
- **Android test app:** the app shows a banner when a newer test build exists,
  or install `NANO-Android-0.4.0-beta.3.apk` from this page.

The installer is bigger than before, because it now carries speech
recognition.

## Downloads

| File | For |
| --- | --- |
| `Nano-Setup-0.4.0-beta.3-x64.exe` | Windows 10/11 x64 installer |
| `NANO-Android-0.4.0-beta.3.apk` | Android 7 or newer (install by hand) |
| `SHA256SUMS.txt` | Checksums of both files |

Check a download in PowerShell with
`Get-FileHash .\Nano-Setup-0.4.0-beta.3-x64.exe -Algorithm SHA256` and compare it
with the line in `SHA256SUMS.txt`.

The installer is **not code-signed** yet, so Windows SmartScreen shows "Windows
protected your PC" and "Unknown publisher". Only install a file you downloaded
from this page and whose checksum matches; then choose **More info → Run
anyway**. Do not turn off SmartScreen or Defender to install NANO.

## What leaves your computer

Nothing new. "Ei Nano" listens and transcribes **on this PC**: no audio is sent
anywhere. The voice model is downloaded once from Hugging Face when you turn
the voice on. Everything else is as described in the Beta 4 notes and in
[PRIVACY.md](https://github.com/coelho26101009-source/Nano_Assistant/blob/main/PRIVACY.md).

## Known limitations

The same as Beta 4: the installer is unsigned, automatic updates and the phone
away from home have not yet been tested on many real PCs and phones, and the
Android app is a test build installed by hand. "Ei Nano" understands
Portuguese best.

---

## Português

O NANO 0.4.0-beta.3 é a segunda atualização da "Beta 4": a ilha deixa de estar
sempre no ecrã e o "Ei Nano" funciona. Continua a ser uma pré-versão.

### O que há de novo

- **A ilha fica escondida até ser precisa.** Aparece quando falas com o NANO,
  quando ele precisa da tua autorização, num lembrete ou aviso, quando usa uma
  ferramenta, quando mudas o volume ou começa uma música nova, e quando levas
  o rato ao meio do topo do ecrã. Depois volta a subir sozinha. Queres a ilha
  sempre visível como antes? Menu da bandeja → **Ilha sempre no ecrã**.
- **O "Ei Nano" funciona no NANO instalado.** O reconhecimento de voz vem agora
  dentro do instalador (antes faltava, por isso o "Ei Nano" não fazia nada).
  Liga-o em Definições → Voz; da primeira vez o NANO descarrega o modelo de voz
  (cerca de 480 MB) e começa a ouvir mal ele chega. O NANO espera que fales,
  para de gravar quando fazes uma pausa, e um pedido dito de seguida
  ("Ei Nano, abre o Spotify") segue logo.
- **Microfone em mute? O NANO avisa.** Se o Windows ou o botão de mute do
  headset tiver o microfone sem som, a ilha mostra "Microfone sem som" em vez
  de o NANO simplesmente nunca te ouvir.
- **A ilha acompanha a tua música.** Passar de música com o teclado (Fn+F3)
  atualiza logo a música e a capa na ilha.

### Como obter

- **Tens a 0.4.0-beta.1 ou a 0.4.0-beta.2 instalada:** não precisas de fazer
  nada. O NANO encontra esta atualização sozinho em poucas horas, descarrega-a,
  confirma a assinatura e instala-a quando o fechas. Para a ter já:
  Definições → Sobre → Atualizações → **Procurar agora**, depois
  **Reiniciar e atualizar**.
- **Tens a Beta 3 ou mais antiga:** instala `Nano-Setup-0.4.0-beta.3-x64.exe`
  desta página por cima, uma vez. Os teus dados em
  `%LOCALAPPDATA%\NanoAssistant` mantêm-se.
- **App Android de teste:** a app mostra um aviso quando há uma versão de teste
  nova, ou instala `NANO-Android-0.4.0-beta.3.apk` desta página.

O instalador é maior do que antes, porque agora traz o reconhecimento de voz.

### Transferências

| Ficheiro | Para |
| --- | --- |
| `Nano-Setup-0.4.0-beta.3-x64.exe` | Instalador para Windows 10/11 x64 |
| `NANO-Android-0.4.0-beta.3.apk` | Android 7 ou mais recente (instalação manual) |
| `SHA256SUMS.txt` | Somas de verificação dos dois ficheiros |

O instalador ainda **não está assinado**, por isso o SmartScreen do Windows
mostra "O Windows protegeu o seu PC" e "Editor desconhecido". Instala só um
ficheiro descarregado desta página e com a soma de verificação certa; depois
escolhe **Mais informações → Executar mesmo assim**. Não desligues o SmartScreen
nem o Defender para instalar o NANO.

### O que sai do teu computador

Nada de novo. O "Ei Nano" ouve e transcreve **neste PC**: nenhum áudio é
enviado. O modelo de voz é descarregado uma vez do Hugging Face quando ligas a
voz. O resto continua como descrito nas notas da Beta 4 e em
[PRIVACY.md](https://github.com/coelho26101009-source/Nano_Assistant/blob/main/PRIVACY.md).

### Limitações conhecidas

As mesmas da Beta 4: o instalador não está assinado, as atualizações automáticas
e o telemóvel fora de casa ainda não foram testados em muitos PCs e telemóveis
reais, e a app Android é uma versão de teste instalada à mão. O "Ei Nano"
percebe melhor português.
