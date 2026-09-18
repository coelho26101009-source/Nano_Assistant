#!/usr/bin/env node
'use strict';

// Developer-only real-package smoke. Starts the supplied executable twice with
// a NEW temporary profile, no inherited API keys and no developer .env. The
// loopback debugging port exists only for this explicitly invoked test.
// Usage: node test/packaged-smoke.js C:\path\Nano.exe
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const { spawn, execFileSync } = require('child_process');

const executable = path.resolve(process.argv[2] || '');
assert.ok(process.argv[2] && fs.statSync(executable).isFile(), 'Supply the packaged executable');
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nano-packaged-smoke-'));
const report = { executable, profile, phases: [], ok: false };
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function connect(port, child) {
  const deadline = Date.now() + 150000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error('Packaged application exited before readiness');
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const target = targets.find((entry) => /^http:\/\/127\.0\.0\.1:\d+\/index.html/.test(entry.url));
      if (target) {
        const socket = new WebSocket(target.webSocketDebuggerUrl);
        await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
        let nextId = 0;
        const pending = new Map();
        socket.onmessage = (event) => {
          const message = JSON.parse(event.data);
          const entry = pending.get(message.id);
          if (!entry) return;
          pending.delete(message.id); clearTimeout(entry.timer);
          if (message.error) entry.reject(new Error(message.error.message)); else entry.resolve(message.result);
        };
        const call = (method, params = {}) => new Promise((resolve, reject) => {
          const id = ++nextId;
          const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 20000);
          pending.set(id, { resolve, reject, timer });
          socket.send(JSON.stringify({ id, method, params }));
        });
        return { call, socket, evaluate: async (expression) => {
          const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
          if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
          return result.result.value;
        } };
      }
    } catch (error) { if (child.exitCode !== null) throw error; }
    await sleep(250);
  }
  throw new Error('Packaged renderer did not become available within 150 seconds');
}

function family(pid) {
  if (process.platform !== 'win32') return [pid];
  const script = `$all = @(Get-CimInstance Win32_Process); $ids = @(${pid}); do { $new = @($all | Where-Object { $_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids } | Select-Object -ExpandProperty ProcessId); $ids += $new } while ($new.Count); @($all | Where-Object { $_.ProcessId -in $ids } | Select-Object ProcessId,Name) | ConvertTo-Json -Compress`;
  const result = execFileSync('powershell.exe', ['-NoProfile', '-Command', script], { encoding: 'utf8', windowsHide: true });
  const processes = [].concat(JSON.parse(result));
  // Ollama is deliberately detached/shared and survives normal Nano shutdown.
  // Record this explicitly; only Nano's owned processes must all terminate.
  report.retainedSharedServices = processes.filter((entry) => /^ollama(?: app)?\.exe$/i.test(entry.Name));
  return processes.filter((entry) => !/^ollama(?: app)?\.exe$/i.test(entry.Name)).map((entry) => entry.ProcessId);
}
function alive(pid) { try { process.kill(pid, 0); return true; } catch (_) { return false; } }

async function phase(restart) {
  const port = await freePort();
  const env = { ...process.env, NANO_DATA_DIR: path.join(profile, 'data'), NANO_SKIP_DOTENV: '1' };
  for (const key of Object.keys(env)) {
    if (/API_KEY|TOKEN|SECRET|ELECTRON_RUN_AS_NODE/i.test(key)) delete env[key];
  }
  const child = spawn(executable, [`--user-data-dir=${path.join(profile, 'shell')}`, `--remote-debugging-port=${port}`],
    { env, stdio: ['ignore', 'ignore', 'ignore'], windowsHide: true });
  let client;
  let ids = [child.pid];
  try {
    client = await connect(port, child);
    let ready = false;
    for (let i = 0; i < 100; i++) {
      ready = await client.evaluate("Boolean(window.nanoApp && window.eel && window.eel.get_onboarding_status && document.querySelector('.topbar'))");
      if (ready) break;
      await sleep(100);
    }
    assert.ok(ready, 'preload, backend bridge and production UI initialize');
    const status = await client.evaluate('window.nanoApp.getDesktopStatus()');
    assert.strictEqual(status.packaged, true);
    assert.strictEqual(status.version, '0.1.0-beta.1');
    assert.strictEqual(status.backend.running, true);
    assert.strictEqual(status.frontendReady, true);
    const onboarding = await client.evaluate('window.eel.get_onboarding_status()()');
    assert.strictEqual(onboarding.completed, restart);
    if (!restart) {
      for (let i = 0; i < 100 && !await client.evaluate("Boolean(document.querySelector('#first-run-title'))"); i++) await sleep(100);
      assert.ok(await client.evaluate("Boolean(document.querySelector('#first-run-title'))"), 'new profile sees the guide');
      const image = await client.call('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(profile, 'first-run.png'), Buffer.from(image.data, 'base64'));
      const configured = await client.evaluate("(() => { const button = [...document.querySelectorAll('button')].find(b => b.textContent === 'Configurar IA'); if (!button) return false; button.click(); return true; })()");
      assert.ok(configured);
      await sleep(700);
      assert.ok(await client.evaluate("document.body.textContent.includes('Groq')"), 'setup opens existing provider settings');
      assert.ok((await client.evaluate("window.eel.create_conversation('Synthetic Beta smoke')()" )).ok);
      assert.ok((await client.evaluate("window.eel.update_setting('onboarding_completed', true)()" )).ok);
      assert.ok((await client.evaluate("window.eel.update_setting('tts_enabled', false)()" )).ok);
    } else {
      const conversations = await client.evaluate("window.eel.list_conversations('', 60, false)()");
      assert.ok(conversations.conversations.some((entry) => entry.title === 'Synthetic Beta smoke'), 'conversation survives restart');
      assert.ok(!await client.evaluate("Boolean(document.querySelector('#first-run-title'))"), 'completed guide does not return');
    }
    ids = family(child.pid);
    report.phases.push({ restart, packaged: status.packaged, version: status.version,
      backendRunning: status.backend.running, frontendReady: status.frontendReady,
      onboardingCompleted: onboarding.completed, processCount: ids.length });
    // Request the same narrow quit operation as the real title/tray control.
    client.evaluate('window.nanoApp.quit()').catch(() => {});
    for (let i = 0; i < 120 && ids.some(alive); i++) await sleep(100);
    assert.deepStrictEqual(ids.filter(alive), [], 'clean shutdown leaves no process in the captured app family');
  } finally {
    if (client) client.socket.close();
    if (alive(child.pid)) {
      if (process.platform === 'win32') execFileSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      else child.kill();
    }
  }
}

(async () => {
  await phase(false);
  await phase(true);
  assert.ok(fs.existsSync(path.join(profile, 'data', 'helios.db')));
  report.ok = true;
})().catch((error) => { report.error = error.message; process.exitCode = 1; }).finally(() => {
  fs.writeFileSync(path.join(profile, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
});
