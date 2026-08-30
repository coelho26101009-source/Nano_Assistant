/**
 * Verify the production Content Security Policy against the REAL built bundle.
 *
 * A CSP is the one security control that fails in both directions: too loose
 * and it protects nothing, too strict and it silently breaks the application
 * for every user while the developer's console stays closed. Reading the policy
 * and reasoning about it is exactly how both mistakes get shipped.
 *
 * So this loads frontend/out in Electron's own Chromium with the SAME policy
 * main.js installs, and reports:
 *
 *   * every CSP violation the page actually triggered, and
 *   * whether the app still rendered (nav, brand, stage all present).
 *
 * A violation here is a real bug in either the policy or the page -- not a
 * warning to be tuned away by widening the policy until it stops complaining.
 *
 *     npx electron test/csp-check.js      # JSON on stdout, log on stderr
 */
'use strict';

const { app, BrowserWindow, session } = require('electron');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const OUT_DIR = path.join(ROOT, 'frontend', 'out');

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.ico': 'image/x-icon',
  '.png': 'image/png',
};

function serve() {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      let rel = decodeURIComponent(req.url.split('?')[0]);
      if (rel === '/') rel = '/index.html';
      let file = path.join(OUT_DIR, rel);
      if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        const html = file + '.html';
        file = fs.existsSync(html) ? html : path.join(OUT_DIR, 'index.html');
      }
      try {
        const body = fs.readFileSync(file);
        res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' });
        res.end(body);
      } catch (err) { res.writeHead(404); res.end('not found'); }
    });
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

/* The policy under test. Kept byte-identical to main.js by requiring the same
   module rather than restating it -- a copy here could drift and then this
   would be testing a policy nobody ships. */
const { contentSecurityPolicy } = require('../main.js');

app.disableHardwareAcceleration();

app.whenReady().then(async () => {
  const server = await serve();
  const port = server.address().port;
  const policy = contentSecurityPolicy(port);

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const headers = { ...details.responseHeaders };
    for (const key of Object.keys(headers)) {
      if (key.toLowerCase() === 'content-security-policy') delete headers[key];
    }
    headers['Content-Security-Policy'] = [policy];
    callback({ responseHeaders: headers });
  });

  const win = new BrowserWindow({
    width: 1366, height: 768, show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });

  const violations = [];
  const consoleErrors = [];
  win.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    if (/Content Security Policy|Refused to/i.test(message)) {
      violations.push({ message, sourceId, line });
    } else if (level >= 2) {
      consoleErrors.push(message);
    }
  });

  await win.loadURL(`http://127.0.0.1:${port}/`);
  await new Promise((r) => setTimeout(r, 2500));

  const rendered = await win.webContents.executeJavaScript(`
    (() => ({
      hasTopbar: !!document.querySelector('.topbar'),
      navCount: document.querySelectorAll('.topnav-item').length,
      hasBrand: !!document.querySelector('.topbar__brand'),
      hasStage: !!document.querySelector('.app'),
      stylesApplied: getComputedStyle(document.body).backgroundColor,
      textLength: (document.body.textContent || '').trim().length,
    }))();
  `);

  /* Snapshot the page's OWN violations before the negative control runs.
     The control below deliberately trips the policy, so anything recorded
     after this line is the probe's doing, not the application's. Filtering by
     message text instead would be guesswork -- the probe's inline-script
     violation reads exactly like a real one would. */
  const pageViolations = violations.slice();

  /* NEGATIVE CONTROL: prove the policy is ENFORCING, not merely present.
     A policy that is set but not applied would produce zero violations and a
     perfectly rendered page -- indistinguishable from success. So deliberately
     do the two things it must forbid, and require that both fail. */
  const enforcement = await win.webContents.executeJavaScript(`
    (async () => {
      const result = { inlineScriptBlocked: false, externalScriptBlocked: false };

      // 1. An inline script must not execute under script-src 'self'.
      try {
        const el = document.createElement('script');
        el.textContent = 'window.__cspInlineRan = true;';
        document.head.appendChild(el);
      } catch (_) { /* ignore */ }
      result.inlineScriptBlocked = window.__cspInlineRan !== true;

      // 2. A cross-origin fetch must not be permitted under connect-src.
      try {
        await fetch('https://example.com/nano-csp-probe', { mode: 'no-cors' });
        result.externalScriptBlocked = false;
      } catch (_) {
        result.externalScriptBlocked = true;
      }
      return result;
    })();
  `);

  // The app must have rendered AND produced no violation AND the policy must
  // demonstrably block what it claims to. A page that renders because the
  // policy was too loose is not a pass, and a policy with no violations
  // because nothing loaded is not either.
  const renderedOk = rendered.hasTopbar && rendered.navCount === 5
    && rendered.hasBrand && rendered.hasStage && rendered.textLength > 200;
  const enforcingOk = enforcement.inlineScriptBlocked && enforcement.externalScriptBlocked;
  const ok = renderedOk && enforcingOk && pageViolations.length === 0;

  console.error('policy: ' + policy);
  console.error('rendered: ' + JSON.stringify(rendered));
  console.error('enforcement (negative control): ' + JSON.stringify(enforcement));
  console.error('page violations: ' + pageViolations.length);
  for (const v of pageViolations) console.error('  ' + v.message);
  console.error('other console errors (eel.js absent here is expected): ' + consoleErrors.length);
  console.error('RESULT: ' + (ok ? 'PASS' : 'FAIL'));

  process.stdout.write(JSON.stringify({
    ok, policy, rendered, renderedOk, enforcing: enforcingOk, enforcement,
    pageViolations, allViolations: violations, consoleErrors,
  }, null, 2));

  server.close();
  app.exit(ok ? 0 : 1);
}).catch((err) => { console.error(err); app.exit(1); });
