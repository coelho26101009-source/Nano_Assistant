# Clean Windows validation

The automated suites run on Linux and on a GitHub `windows-latest` runner,
which already has Python, Node, Git and Visual C++ runtimes installed. Neither
proves that the installer works for someone who has none of that. This is the
script for a person to follow on a clean machine before a release. Tick each
line in the release issue, with the build's SHA-256 next to it.

Allow about 45 minutes. Do not skip a step because a test covers it. Every
step here exists because something passed the tests and still broke for a
user.

## 0. The machine

Use one of the following, in order of preference:

* **Windows Sandbox** (Windows 11 Pro/Enterprise: *Turn Windows features on or
  off → Windows Sandbox*). Every launch is a fresh Windows with nothing
  installed, and closing it throws everything away. It cannot test reboots or
  the microphone, so do sections 1 to 7 there and section 8 on a VM or spare
  PC.
* A Hyper-V or VirtualBox VM from Microsoft's Windows 11 evaluation image.
* A spare PC, on a **new local user account** that has never run Nano.

The account must be a **standard user** (not an administrator), with no Python,
Node, Ollama or Git. Check in PowerShell: `python`, `node` and `ollama` should
all be "not recognized". The Microsoft Store `python` alias counts as absent.

## 1. Download and verify

- [ ] Download the `.exe` and `SHA256SUMS.txt` from the release page in Edge.
- [ ] `Get-FileHash .\Nano-Setup-*-x64.exe -Algorithm SHA256` matches the file.
- [ ] Note exactly what SmartScreen and the UAC prompt show (publisher name or
      "Unknown"). Take a screenshot. This goes in the release notes.

## 2. Install and first run

- [ ] The installer runs without administrator rights and finishes.
- [ ] The Start menu entry and the desktop shortcut open Nano.
- [ ] First launch reaches the home screen ("Como posso ajudar?") in under
      20 seconds, with no console window flashing.
- [ ] With no Ollama and no API key, the status shows **not configured** (a
      hollow amber ring), never green, and the first-run guide explains what
      to do.

## 3. Without a model (offline)

- [ ] Turn off the network. Send a message. The error says there is no model
      or network, in Portuguese, and the app stays usable.
- [ ] Turn the network back on. Add a Groq key in *Definições → IA*. A message
      gets an answer, and the AI selector names the provider.

## 4. Features added for this release

- [ ] **Lembretes:** "lembra-me de beber água daqui a 1 minuto". A Windows
      notification titled with the reminder appears, including with Nano
      minimised. Clicking it brings Nano to the front.
- [ ] **Frases e repetição:** "lembra-me amanhã às 9 de ligar ao banco" is
      created for tomorrow at 09:00. "todos os dias às 8" shows "repete todos os
      dias" in Agenda.
- [ ] **Agenda:** create a reminder and an event from the page, delete both, and
      confirm each deletion asks first.
- [ ] **Esclarecimento:** open two Notepad windows and ask "fecha o bloco de
      notas". Nano asks which one and shows a chip for each. Picking a chip
      closes only that one, after the usual confirmation.
- [ ] **Modos:** *PC → Modos → Modo Foco → Ativar*. Every step is listed before
      it runs, closing apps asks each time, and the result lists what was done
      or skipped.
- [ ] **Texto do ecrã:** with the option off, "lê o que está no ecrã" is
      refused and the reply says where to turn it on (*Definições → Privacidade → Ler texto do
      ecrã*). With it on, it asks for
      confirmation, then reads Notepad text correctly. With a password manager
      or a banking site in front, it refuses.
- [ ] Screenshot of monitor 2 and of a region, if the machine has two screens.

## 5. PC Control basics

- [ ] One action that asks (closing an app) and one that does not (volume).
- [ ] Refusing a confirmation really does nothing.

## 6. Voice (if a microphone is available)

- [ ] *Definições → Voz* explains how to install the optional speech
      packages. After installing, one spoken turn works end to end.

## 7. Data and uninstall

- [ ] Close Nano from the tray. No `python.exe` or `Nano Assistant.exe` stays in
      Task Manager.
- [ ] Reopen it. The conversation and reminders are still there.
- [ ] Uninstall from *Definições do Windows → Aplicações*. The program folder is
      gone. `%LOCALAPPDATA%\NanoAssistant` is kept, as documented.
- [ ] Reinstall. History is back. *Definições → Privacidade → Apagar todos os
      dados* empties it.

## 8. Restart (VM or real PC only)

- [ ] With a reminder due in 5 minutes, restart Windows and log in. Nano
      starts if *Iniciar com o Windows* is on, and the reminder fires once.
- [ ] A daily reminder whose time passed while the PC was off fires **once**,
      then moves to tomorrow. It does not fire for every missed day.

## Reporting

For each failed line, open an issue with the step, the build's SHA-256,
Windows version (`winver`) and `%LOCALAPPDATA%\NanoAssistant\logs\nano.log`.
Read the log before attaching it. It must not contain keys or message text,
and if it does, that is a bug of its own.
