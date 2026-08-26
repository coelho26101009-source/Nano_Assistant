#!/usr/bin/env node
/**
 * MEASURE the built Nano UI, at the desktop sizes it has to work at.
 *
 *     npx electron test/render-check.js            # JSON to stdout
 *
 * WHY THIS EXISTS
 * The user's report was "I had to zoom the browser out to about 80% because the
 * right-hand side felt cut off". A previous static audit could not reproduce a
 * global horizontal overflow by reading the CSS -- which is exactly the limit of
 * reading CSS. This renders the real production bundle in the real Chromium
 * that Electron ships, at the real target resolutions, and measures:
 *
 *   * whether the document scrolls horizontally at all
 *   * which specific elements stick out past the viewport
 *   * whether the layout is being rescued by a zoom factor (it must not be)
 *
 * It also checks the browser fallback: the identical bundle is loaded WITHOUT
 * the desktop preload, and must render with no title bar and no uncaught error.
 *
 * The page is served from a throwaway static server over frontend/out, so this
 * needs no Python backend and is deterministic enough to run from pytest.
 */
'use strict';

const { app, BrowserWindow, ipcMain } = require('electron');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const OUT_DIR = path.join(ROOT, 'frontend', 'out');
const PRELOAD = path.join(__dirname, '..', 'preload.js');

/** The desktop resolutions Nano must be comfortable at, as CSS pixels.
 *
 *  The last entry is not a monitor: it is the SMALLEST window Electron will
 *  allow (windowState.MIN_WIDTH x MIN_HEIGHT). The user can drag the window to
 *  it, so it has to hold together there too -- that is where the exterior
 *  spacing of the floating shell is under the most pressure. */
const VIEWPORTS = [
  { name: '1920x1080', width: 1920, height: 1040 },
  { name: '1600x900', width: 1600, height: 860 },
  { name: '1366x768', width: 1366, height: 728 },
  { name: '1280x720', width: 1280, height: 680 },
  { name: '940x620-min', width: 940, height: 620 },
];

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.ico': 'image/x-icon',
};

function serve() {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const url = decodeURIComponent((req.url || '/').split('?')[0]);
      let filePath = path.join(OUT_DIR, url === '/' ? 'index.html' : url);
      if (fs.existsSync(filePath) && fs.statSync(filePath).isDirectory()) {
        filePath = path.join(filePath, 'index.html');
      }
      if (!filePath.startsWith(OUT_DIR) || !fs.existsSync(filePath)) {
        res.writeHead(404).end('not found');
        return;
      }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream' });
      fs.createReadStream(filePath).pipe(res);
    });
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

/** Run in the page: report anything that sticks out horizontally. */
const PROBE = `(() => {
  const doc = document.documentElement;
  const viewport = doc.clientWidth;
  const offenders = [];
  for (const el of document.querySelectorAll('body *')) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    // 1 px of tolerance: sub-pixel rounding at fractional device ratios is not
    // a layout bug and must not make this flap.
    if (rect.right > viewport + 1 || rect.left < -1) {
      offenders.push({
        selector: el.tagName.toLowerCase() +
          (el.className && typeof el.className === 'string'
            ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.')
            : ''),
        left: Math.round(rect.left),
        right: Math.round(rect.right),
      });
    }
    if (offenders.length >= 12) break;
  }
  // CLIPPED CONTENT. A box with overflow:hidden whose content is taller than
  // it is has silently cut a line off. This is what the horizontal check
  // missed: the inspector panels were being squeezed by a column flex parent
  // and losing their last line, which reads to a user as "the right side is
  // cut off" -- the exact complaint that started this work.
  const clipped = [];
  for (const el of document.querySelectorAll('body *')) {
    const style = getComputedStyle(el);
    const hides = (axis) => axis === 'hidden' || axis === 'clip';
    if (el.clientHeight > 8 && hides(style.overflowY) && el.scrollHeight > el.clientHeight + 2) {
      clipped.push({
        selector: el.tagName.toLowerCase() +
          (el.className && typeof el.className === 'string'
            ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.')
            : ''),
        clientHeight: el.clientHeight,
        scrollHeight: el.scrollHeight,
        text: (el.textContent || '').trim().slice(0, 48),
      });
    }
    if (clipped.length >= 12) break;
  }

  // THE CAUSE, not just the symptom. A scrolling flex column whose children can
  // shrink does not scroll when it runs out of room -- it squeezes them, and
  // any child with overflow:hidden then loses its last line. The clipping above
  // only shows up when there is enough real content; this shows up always, so
  // it is the check that actually holds the line.
  const shrinkable = [];
  for (const parent of document.querySelectorAll('body *')) {
    const style = getComputedStyle(parent);
    const scrolls = style.overflowY === 'auto' || style.overflowY === 'scroll';
    if (style.display !== 'flex' || style.flexDirection !== 'column' || !scrolls) continue;
    for (const child of parent.children) {
      if (getComputedStyle(child).flexShrink !== '0') {
        shrinkable.push({
          parent: parent.className || parent.tagName.toLowerCase(),
          child: child.className || child.tagName.toLowerCase(),
        });
      }
    }
    if (shrinkable.length >= 12) break;
  }

  // DRAG REGIONS. The frameless window is moved by dragging the shell, and
  // every control has to opt out or it is not clickable at all -- a defect that
  // looks exactly like "the button does nothing". -webkit-app-region is NOT an
  // inherited property in Chromium, so a child reporting 'none' simply defers
  // to the nearest ancestor that declared one; what matters is that the
  // ancestors declare the right thing and that controls say no-drag themselves.
  const appRegion = (selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const style = getComputedStyle(el);
    return style.getPropertyValue('-webkit-app-region').trim() || 'none';
  };
  const dragRegions = {
    shell: appRegion('.shell'),
    app: appRegion('.app'),
    topnavItem: appRegion('.topnav-item'),
    statusPill: appRegion('.status-pill'),
    windowControl: appRegion('.window-control'),
    railToggle: appRegion('.topbar .icon-btn'),
  };

  const app = document.querySelector('.app');
  return {
    clipped,
    shrinkable,
    dragRegions,
    viewport,
    devicePixelRatio: window.devicePixelRatio,
    docScrollWidth: doc.scrollWidth,
    docClientWidth: doc.clientWidth,
    bodyScrollWidth: document.body.scrollWidth,
    horizontalOverflow: doc.scrollWidth > doc.clientWidth || document.body.scrollWidth > doc.clientWidth,
    // The caption merged into the top bar in the redesign, so the thing that
    // must be absent without the desktop shell is the window-control cluster,
    // not a bar of its own. The bar itself renders in a browser too.
    hasWindowControls: Boolean(document.querySelector('.window-controls')),
    hasTopBar: Boolean(document.querySelector('.topbar')),
    hasApp: Boolean(app),
    railWidth: Math.round(document.querySelector('.rail')?.getBoundingClientRect().width || 0),
    stageWidth: Math.round(document.querySelector('.stage')?.getBoundingClientRect().width || 0),
    // The reading column inside the stage. A stage that is wide while the
    // column inside it is a hairline would still be a squeezed layout. The
    // composer's column is measured because it is present in every chat state
    // and is capped by the same --chat-max as the transcript.
    readingColumnWidth: Math.round(
      (document.querySelector('.conversation__inner') || document.querySelector('.composer-inner'))
        ?.getBoundingClientRect().width || 0),
    composerVisible: (() => {
      const c = document.querySelector('.composer');
      if (!c) return false;
      const r = c.getBoundingClientRect();
      return r.top >= 0 && r.bottom <= doc.clientHeight + 1;
    })(),
    offenders,
  };
})()`;

/** Click a top-bar section by its label and let the stage settle. */
const OPEN_SECTION = (label) => `(() => {
  for (const el of document.querySelectorAll('.topnav-item')) {
    if ((el.textContent || '').trim().startsWith(${JSON.stringify(label)})) { el.click(); return true; }
  }
  return false;
})()`;

async function measure(window, url, viewport) {
  window.setSize(viewport.width, viewport.height);
  await window.loadURL(url);
  // Let React mount and the fonts settle. The shell renders immediately; the
  // 12 s bridge timeout is deliberately not waited for.
  await new Promise((r) => setTimeout(r, 1800));
  window.webContents.setZoomFactor(1);
  const result = await window.webContents.executeJavaScript(PROBE);
  return { viewport: viewport.name, requested: viewport, ...result };
}

/**
 * Measure one section without reloading.
 *
 * The chat is empty here -- there is no backend in this harness -- but the
 * pages are not: PC, Ferramentas and Definicoes render their full structure
 * from null data, and they are the densest layouts in the app. Measuring only
 * the chat would have left them unchecked.
 */
async function measureSection(window, viewport, label) {
  await window.webContents.executeJavaScript(OPEN_SECTION(label));
  await new Promise((r) => setTimeout(r, 500));
  const result = await window.webContents.executeJavaScript(PROBE);
  return { viewport: viewport.name, section: label, ...result };
}

async function main() {
  if (!fs.existsSync(path.join(OUT_DIR, 'index.html'))) {
    console.log(JSON.stringify({ ok: false, error: 'frontend/out is not built' }));
    app.exit(2);
    return;
  }

  const server = await serve();
  const url = `http://127.0.0.1:${server.address().port}/index.html`;
  const report = { ok: true, desktop: [], sections: [], browser: null, consoleErrors: [] };

  // The real preload asks the main process for the window state. Answer it, so
  // the title bar renders in its normal state rather than in an error path.
  ipcMain.handle('nano:window-state', () => (
    { maximized: false, fullScreen: false, focused: true, platform: process.platform }));

  /** Console messages that are genuinely the page's fault. */
  const isPageError = (message) => !message.includes('Electron Security Warning');

  let desktopWindow = null;
  let browserWindow = null;
  try {
    /* Desktop: with the preload, so window.nanoApp exists and the title bar
       is part of the layout being measured. */
    desktopWindow = new BrowserWindow({
      show: false, frame: false, width: 1280, height: 720,
      webPreferences: {
        preload: PRELOAD, contextIsolation: true, nodeIntegration: false, sandbox: true,
      },
    });
    desktopWindow.webContents.on('console-message', (_e, level, message) => {
      if (level >= 2 && isPageError(message)) report.consoleErrors.push(message);
    });
    for (const viewport of VIEWPORTS) {
      report.desktop.push(await measure(desktopWindow, url, viewport));
      for (const section of ['Ferramentas', 'PC', 'Mem', 'Defini']) {
        report.sections.push(await measureSection(desktopWindow, viewport, section));
      }
    }

    /* Browser fallback: the SAME bundle with no preload at all. It must render
       the app shell, omit the native title bar, and throw nothing. */
    const fallbackErrors = [];
    browserWindow = new BrowserWindow({
      show: false, width: 1366, height: 768,
      webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
    });
    browserWindow.webContents.on('console-message', (_e, level, message) => {
      if (level >= 2 && isPageError(message)) fallbackErrors.push(message);
    });
    report.browser = {
      ...(await measure(browserWindow, url, { name: '1366x768', width: 1366, height: 768 })),
      errors: fallbackErrors,
    };
  } catch (err) {
    report.ok = false;
    report.error = err.message;
  } finally {
    for (const window of [desktopWindow, browserWindow]) {
      if (window && !window.isDestroyed()) window.destroy();
    }
    server.close();
  }

  console.log(JSON.stringify(report, null, 2));
  app.exit(report.ok ? 0 : 1);
}

app.disableHardwareAcceleration();
app.whenReady().then(main);
