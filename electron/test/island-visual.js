#!/usr/bin/env node
/**
 * Play the NANO island through every state, in real Electron, for review.
 *
 *   npx electron test/island-visual.js <output-directory> [en|pt] [cover.jpg]
 *
 * The REAL overlay page, preload and view builders (lib/island.js) are used.
 * Only two things are staged: the window is opaque with a demo wallpaper
 * behind the island (a transparent window cannot be screenshotted on a Linux
 * display without a compositor), and hovering is simulated with mouseenter.
 * Every state is saved as a PNG; record the display alongside to get a video.
 */
'use strict';

const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const path = require('path');
const island = require('../lib/island');
const overlayState = require('../lib/overlay-state');

// Arguments after the script's own path (Chromium flags may come before it).
const ARGS = process.argv.slice(process.argv.findIndex((arg) => arg.endsWith('island-visual.js')) + 1);
const OUT = ARGS[0] && path.resolve(ARGS[0]);
const LANG = ARGS[1] === 'en' ? 'en' : 'pt';
const COVER = ARGS[2] && fs.existsSync(ARGS[2])
  ? `data:image/jpeg;base64,${fs.readFileSync(ARGS[2]).toString('base64')}` : '';
const SIZE = { width: 1280, height: 720 };
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const BACKDROP = `
  html, body { background: radial-gradient(1200px 700px at 30% 110%, #3b2a6b 0%, #14121f 55%, #0b0b10 100%) !important; }
  body::before {
    content: ""; position: fixed; left: 150px; top: 120px; width: 980px; height: 540px; border-radius: 12px;
    background: linear-gradient(#202024 0 38px, #f4f4f5 38px); box-shadow: 0 30px 80px -20px rgba(0,0,0,.7);
  }
  body::after {
    content: "Relatório PAP — robô.docx"; position: fixed; left: 172px; top: 131px;
    font: 13px "Segoe UI", system-ui, sans-serif; color: #d4d4d8;
  }`;

async function run() {
  if (!OUT) throw new Error('Pass an output directory');
  fs.mkdirSync(OUT, { recursive: true });

  const window = new BrowserWindow({
    ...SIZE, x: 0, y: 0, show: true, frame: false, resizable: false, backgroundColor: '#0b0b10',
    webPreferences: {
      preload: path.join(__dirname, '..', 'overlay', 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    },
  });
  const loaded = new Promise((resolve) => window.webContents.once('did-finish-load', resolve));
  window.loadFile(path.join(__dirname, '..', 'overlay', 'overlay.html'));
  await loaded;
  // "plain": wallpaper only, for documentation images.
  await window.webContents.insertCSS(ARGS[3] === 'plain' ? BACKDROP.replace(/body::(before|after) \{[^}]*\}/g, '') : BACKDROP);
  await wait(600);

  const stack = new island.IslandStack();
  const t0 = Date.now();
  const timeline = [];
  const show = (name) => {
    const view = island.translate(stack.current({ alwaysVisible: true }), LANG);
    // Nubi, the mascot, as the shell sends her (on by default). NANO_ISLAND_MASCOT=0 shows the plain N.
    if (view) view.mascot = process.env.NANO_ISLAND_MASCOT !== '0';
    window.webContents.send('nano:overlay-state', view);
    timeline.push({ name, at: (Date.now() - t0) / 1000, state: view && view.state });
  };
  const hover = (inside) => window.webContents.executeJavaScript(
    `document.getElementById('island').dispatchEvent(new MouseEvent('${inside ? 'mouseenter' : 'mouseleave'}'))`);
  const snap = async (name) => {
    const image = await window.webContents.capturePage({ x: 340, y: 0, width: 600, height: 230 });
    fs.writeFileSync(path.join(OUT, `${name}.png`), image.toPNG());
  };
  const step = async (name, ms = 1600) => { show(name); await wait(Math.min(ms, 900)); await snap(name); await wait(Math.max(0, ms - 900)); };
  const voice = (view) => { stack.set('voice', island.fromVoiceView(view).view); };

  window.webContents.send('nano:overlay-state', overlayState.HIDDEN);
  await wait(500);

  await step('01-idle', 1800);
  await hover(true); await wait(900); await snap('02-idle-hover'); await wait(700); await hover(false);
  voice(overlayState.fromPhase('COMMAND_LISTENING')); await step('03-listening', 3200);
  voice(overlayState.fromPhase('TRANSCRIBING')); await step('04-transcribing', 1500);
  voice(overlayState.fromPhase('PROCESSING')); await step('05-thinking', 2000);
  voice(overlayState.fromPhase('SPEAKING')); await step('06-speaking', 2200);
  voice(overlayState.fromTurnEnd({ ok: true })); await step('07-done', 1300);
  stack.clear('voice'); await step('08-back-to-idle', 1200);

  stack.set('chat', island.fromChatActivity({ phase: 'thinking' }).view); await step('09-chat-thinking', 1600);
  stack.set('chat', island.fromChatActivity({ phase: 'working', tools: ['web_search'] }).view); await step('10-working-tool', 2200);
  stack.set('chat', island.fromChatActivity({ phase: 'done' }).view); await step('11-chat-done', 1200);
  stack.clear('chat');

  stack.approvalRequested('r1', { action: 'FECHAR JANELA', target: 'Discord — #geral' });
  await step('12-approval', 2800);
  stack.approvalResolved('r1'); await step('13-idle', 900);

  stack.set('notice', island.fromReminder({ text: 'Treino de robótica para a competição', due_label: '18:30' }).view);
  await step('14-reminder', 2600);
  stack.set('notice', island.fromNotice({ kind: 'battery', title: LANG === 'en' ? 'Low battery' : 'Bateria fraca',
    text: LANG === 'en' ? 'The battery is at 18%. Plug in the charger.' : 'A bateria está em 18%. Convém ligar o carregador.' }).view);
  await step('15-battery', 2400);
  stack.set('notice', island.fromNotice({ kind: 'download', title: LANG === 'en' ? 'Download finished' : 'Download terminado',
    text: LANG === 'en' ? 'A new file arrived in Downloads: setup_jogo.exe' : 'Chegou um ficheiro novo às Transferências: setup_jogo.exe' }).view);
  await step('16-download', 2400);
  stack.clear('notice');

  const song = { status: 'playing', title: 'Luzes da Cidade', artist: 'Banda de Demonstração', app: 'Spotify',
    position: 74, duration: 213, position_at: Date.now(), art: COVER, key: 'demo' };
  stack.set('media', island.fromMedia(song).view);
  stack.set('notice', island.trackCard(song).view);
  await step('17-new-song', 3000);
  stack.clear('notice'); await step('18-music-pill', 2200);
  await hover(true); await wait(1000); await snap('19-player-hover'); await wait(1400); await hover(false); await wait(500);
  stack.set('hud', island.fromVolume({ level: 35, muted: false }).view); await step('20-volume', 700);
  stack.set('hud', island.fromVolume({ level: 60, muted: false }).view); await step('21-volume-up', 1100);
  stack.set('hud', island.fromVolume({ level: 60, muted: true }).view); await step('22-muted', 1200);
  stack.clear('hud');
  stack.set('media', island.fromMedia({ ...song, status: 'paused' }).view); await step('23-paused', 1600);
  stack.clear('media'); await step('24-idle-end', 1500);
  window.webContents.send('nano:overlay-state', overlayState.HIDDEN); await wait(600);

  fs.writeFileSync(path.join(OUT, 'timeline.json'), JSON.stringify(timeline, null, 2));
  console.log(JSON.stringify({ frames: timeline.length, seconds: (Date.now() - t0) / 1000 }));
  app.exit(0);
}

app.whenReady().then(run).catch((error) => {
  console.error(error);
  app.exit(1);
});
