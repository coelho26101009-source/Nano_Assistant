NANO 0.4.0-beta.2 is an update to "Beta 4": same Beta, a calmer island. Still a pre-release.

[Português mais abaixo](#português)

## What's new

- **The island in sober colours.** The NANO island at the top of the screen now
  uses a soft black instead of pure black, warm off-white text and quiet greys.
  Each state (listening, thinking, a tool, waiting for your approval, success,
  a warning) keeps only a hint of colour, so you can still tell them apart
  without the island shouting. The "ready" dot is a pale blue.

Everything else in Beta 4 is unchanged: see the
[Beta 4 notes](https://github.com/coelho26101009-source/Nano_Assistant/blob/main/docs/RELEASE_NOTES_0.4.0-beta.1.md)
for the full list (the island, the 3D Second Brain, deep research, Memory M3,
the phone's camera, the phone away from home, automatic updates and more).

## Getting it

- **You have 0.4.0-beta.1 installed:** nothing to do. NANO finds this update by
  itself within a few hours, downloads it, checks its signature and installs it
  when you close NANO. To get it now: Settings → About → Updates → **Check now**,
  then **Restart and update**.
- **You have Beta 3 or older:** install `Nano-Setup-0.4.0-beta.2-x64.exe` from
  this page over it once. Your data in `%LOCALAPPDATA%\NanoAssistant` is kept.
- **Android test app:** the app shows a banner when a newer test build exists,
  or install `NANO-Android-0.4.0-beta.2.apk` from this page.

## Downloads

| File | For |
| --- | --- |
| `Nano-Setup-0.4.0-beta.2-x64.exe` | Windows 10/11 x64 installer |
| `NANO-Android-0.4.0-beta.2.apk` | Android 7 or newer (install by hand) |
| `SHA256SUMS.txt` | Checksums of both files |

Check a download in PowerShell with
`Get-FileHash .\Nano-Setup-0.4.0-beta.2-x64.exe -Algorithm SHA256` and compare it
with the line in `SHA256SUMS.txt`.

The installer is **not code-signed** yet, so Windows SmartScreen shows "Windows
protected your PC" and "Unknown publisher". Only install a file you downloaded
from this page and whose checksum matches; then choose **More info → Run
anyway**. Do not turn off SmartScreen or Defender to install NANO.

## What leaves your computer

Nothing new. This update only changes colours. Everything is as described in
the Beta 4 notes and in [PRIVACY.md](https://github.com/coelho26101009-source/Nano_Assistant/blob/main/PRIVACY.md).

## Known limitations

The same as Beta 4: the installer is unsigned, automatic updates and the phone
away from home have not yet been tested on many real PCs and phones, and the
Android app is a test build installed by hand.

---

## Português

O NANO 0.4.0-beta.2 é uma atualização da "Beta 4": a mesma Beta, com uma ilha
mais calma. Continua a ser uma pré-versão.

### O que há de novo

- **A ilha com cores sóbrias.** A ilha do NANO no topo do ecrã usa agora um
  preto suave em vez de preto puro, texto branco-quente e cinzentos discretos.
  Cada estado (a ouvir, a pensar, uma ferramenta, à espera da tua autorização,
  sucesso, um aviso) fica só com um toque de cor, por isso continuas a
  distingui-los sem a ilha gritar. O ponto de "pronto" é azul-claro.

O resto da Beta 4 fica igual: vê as
[notas da Beta 4](https://github.com/coelho26101009-source/Nano_Assistant/blob/main/docs/RELEASE_NOTES_0.4.0-beta.1.md)
para a lista completa (a ilha, o Segundo Cérebro em 3D, a investigação
profunda, a Memória M3, a câmara do telemóvel, o telemóvel fora de casa, as
atualizações automáticas e mais).

### Como obter

- **Tens a 0.4.0-beta.1 instalada:** não precisas de fazer nada. O NANO encontra
  esta atualização sozinho em poucas horas, descarrega-a, confirma a assinatura
  e instala-a quando o fechas. Para a ter já: Definições → Sobre →
  Atualizações → **Procurar agora**, depois **Reiniciar e atualizar**.
- **Tens a Beta 3 ou mais antiga:** instala `Nano-Setup-0.4.0-beta.2-x64.exe`
  desta página por cima, uma vez. Os teus dados em
  `%LOCALAPPDATA%\NanoAssistant` mantêm-se.
- **App Android de teste:** a app mostra um aviso quando há uma versão de teste
  nova, ou instala `NANO-Android-0.4.0-beta.2.apk` desta página.

### Transferências

| Ficheiro | Para |
| --- | --- |
| `Nano-Setup-0.4.0-beta.2-x64.exe` | Instalador para Windows 10/11 x64 |
| `NANO-Android-0.4.0-beta.2.apk` | Android 7 ou mais recente (instalação manual) |
| `SHA256SUMS.txt` | Somas de verificação dos dois ficheiros |

O instalador ainda **não está assinado**, por isso o SmartScreen do Windows
mostra "O Windows protegeu o seu PC" e "Editor desconhecido". Instala só um
ficheiro descarregado desta página e com a soma de verificação certa; depois
escolhe **Mais informações → Executar mesmo assim**. Não desligues o SmartScreen
nem o Defender para instalar o NANO.

### O que sai do teu computador

Nada de novo. Esta atualização só muda cores. Tudo continua como descrito nas
notas da Beta 4 e em [PRIVACY.md](https://github.com/coelho26101009-source/Nano_Assistant/blob/main/PRIVACY.md).

### Limitações conhecidas

As mesmas da Beta 4: o instalador não está assinado, as atualizações automáticas
e o telemóvel fora de casa ainda não foram testados em muitos PCs e telemóveis
reais, e a app Android é uma versão de teste instalada à mão.
