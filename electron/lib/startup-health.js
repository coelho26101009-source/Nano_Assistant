'use strict';

const http = require('http');

const ERROR_CODES = new Set([
  'backend_start_failed', 'backend_exited', 'frontend_load_failed', 'renderer_gone',
]);

/** A shareable report built from explicit fields, never logs or settings. */
function diagnostics({ version, packaged, backend, frontendReady, lastErrorCode,
  platform = process.platform, arch = process.arch, versions = process.versions } = {}) {
  return {
    schema: 1,
    version: String(version || 'unknown'),
    platform,
    arch,
    versions: { electron: versions.electron || null, chrome: versions.chrome || null },
    packaged: Boolean(packaged),
    backend: {
      running: Boolean(backend && backend.running),
      lastExitCode: backend && Number.isInteger(backend.lastExitCode) ? backend.lastExitCode : null,
    },
    frontendReady: Boolean(frontendReady),
    lastErrorCode: ERROR_CODES.has(lastErrorCode) ? lastErrorCode : null,
  };
}

/** One request at a time, with a deadline covering hung responses too. */
function waitForHttp(port, timeoutMs = 45000, { alive = () => true, intervalMs = 300 } = {}) {
  return new Promise((resolve, reject) => {
    let request;
    let retry;
    let finished = false;
    const finish = (error) => {
      if (finished) return;
      finished = true;
      clearTimeout(deadline);
      clearTimeout(retry);
      if (request) request.destroy();
      if (error) reject(error); else resolve();
    };
    const deadline = setTimeout(() => finish(new Error('A interface do Nano não ficou disponível a tempo.')), timeoutMs);
    const attempt = () => {
      if (finished) return;
      if (!alive()) { finish(new Error('O motor do Nano parou durante o arranque.')); return; }
      let scheduled = false;
      const again = () => {
        if (finished || scheduled) return;
        scheduled = true;
        retry = setTimeout(attempt, intervalMs);
      };
      request = http.get({ host: '127.0.0.1', port, path: '/index.html', timeout: 2000 }, (res) => {
        res.resume();
        if (res.statusCode === 200) finish(); else again();
      });
      request.once('error', again);
      request.once('timeout', () => { request.destroy(); again(); });
    };
    attempt();
  });
}

/** Transient failures get two retries; permanent failures require a decision. */
function loadRecovery({ reload, report, closed = () => false, schedule = setTimeout,
  cancel = clearTimeout, delayMs = 1500 } = {}) {
  let failures = 0;
  let timer = null;
  let reported = false;
  return {
    failed() {
      if (closed() || reported || timer !== null) return;
      failures += 1;
      if (failures > 2) { reported = true; report(); return; }
      timer = schedule(() => { timer = null; if (!closed()) reload(); }, delayMs);
    },
    ready() { failures = 0; reported = false; if (timer !== null) cancel(timer); timer = null; },
    dispose() { if (timer !== null) cancel(timer); timer = null; reported = true; },
  };
}

module.exports = { diagnostics, waitForHttp, loadRecovery };
