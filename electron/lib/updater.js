/**
 * Automatic updates for the installed Windows app.
 *
 * WHAT IT DOES
 *   1. A while after start, and then every few hours, it reads the signed
 *      update feed from GitHub (lib/update-feed.js checks the signature).
 *   2. If the feed names a NEWER version, it downloads that installer in the
 *      background into <data dir>/updates, checking the size and the SHA-256
 *      the signed feed promised. A file that does not match is deleted.
 *   3. When the download is ready it tells the person (notification, tray,
 *      Settings) and installs when NANO is closed -- or straight away if they
 *      press "Restart and update". The installer is the same NSIS installer
 *      people download by hand, run silently over the existing install, so
 *      settings, keys and conversations (all in the data directory) stay.
 *   4. On the next start it checks whether the version really changed and says
 *      so. A failed install is reported, the installer is kept for another try,
 *      and after two failures it stops trying on its own and points to the
 *      download page instead.
 *
 * WHAT IT NEVER DOES
 *   * install anything the signed feed did not describe byte for byte;
 *   * downgrade (an older signed feed is ignored);
 *   * install while Windows is shutting down (a half-written install is the
 *     one way an update could leave NANO unable to start);
 *   * act for the model: this lives in the desktop shell, not in the tool
 *     pipeline, and no tool or conversation can start it.
 *
 * Running from the project folder (NANO_DESKTOP.bat), it only checks and says
 * that a new version exists: there is no installer to replace there.
 *
 * Everything with a side effect (fetch, spawn, fs, clock) is injected, so the
 * whole flow is exercised by electron/test/updater.test.js without a network.
 */
'use strict';

const crypto = require('crypto');
const path = require('path');
const feed = require('./update-feed');

const FEED_URL = 'https://github.com/coelho26101009-source/Nano_Assistant/releases/download/desktop-update/nano-update.json';
const RELEASES_URL = 'https://github.com/coelho26101009-source/Nano_Assistant/releases';
const PRODUCT = 'nano-desktop';

const FIRST_CHECK_DELAY_MS = 90 * 1000;
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;
const MIN_MANUAL_INTERVAL_MS = 15 * 1000;
const FEED_TIMEOUT_MS = 20 * 1000;
const STALL_TIMEOUT_MS = 60 * 1000;
const MAX_FEED_BYTES = 64 * 1024;
const MAX_INSTALL_ATTEMPTS = 2;
/** Room for the installer to unpack next to the download. */
const FREE_SPACE_MARGIN = 300 * 1024 * 1024;

const PENDING_FILE = 'pending-update.json';

function installerName(version) {
  return `Nano-Setup-${String(version).replace(/[^0-9A-Za-z.-]/g, '')}-x64.exe`;
}

class Updater {
  /**
   * @param {object} options
   * @param {string} options.currentVersion  app.getVersion()
   * @param {boolean} options.canInstall     packaged Windows build
   * @param {string} options.dataDir         canonical data directory
   * @param {Function} options.fetch         WHATWG fetch (Electron net.fetch)
   * @param {Function} options.spawn         child_process.spawn
   * @param {object} options.fs              node:fs
   * @param {Function} [options.onChange]    called with the public state
   * @param {Function} [options.log]
   * @param {Function} [options.now]
   * @param {object} [options.timers]        { setTimeout, clearTimeout }
   * @param {Array} [options.keys]           override the trusted keys (tests)
   * @param {string} [options.feedUrl]
   */
  constructor(options) {
    this.currentVersion = options.currentVersion;
    this.canInstall = Boolean(options.canInstall);
    this.dir = path.join(options.dataDir, 'updates');
    this.fetch = options.fetch;
    this.spawn = options.spawn;
    this.fs = options.fs;
    this.onChange = options.onChange || (() => {});
    this.log = options.log || (() => {});
    this.now = options.now || (() => Date.now());
    this.timers = options.timers || { setTimeout, clearTimeout };
    this.keys = options.keys;
    this.feedUrl = options.feedUrl || FEED_URL;

    this.enabled = true;
    this.timer = null;
    this.busy = null;
    this.lastManualCheck = 0;
    this.offer = null;          // verified payload of a newer version
    this.installerPath = null;  // verified installer on disk
    this.installAttempts = 0;
    this.state = {
      status: 'idle',
      mode: this.canInstall ? 'installed' : 'source',
      currentVersion: this.currentVersion,
      available: null,
      progress: 0,
      lastCheck: null,
      error: null,
      lastResult: null,
      releasesUrl: RELEASES_URL,
    };
  }

  /* ── Public state ───────────────────────────────────────────────────── */

  publicState() {
    return { ...this.state, enabled: this.enabled };
  }

  set(patch) {
    this.state = { ...this.state, ...patch };
    try { this.onChange(this.publicState()); } catch (err) { this.log('update onChange failed:', err.message); }
  }

  /* ── Scheduling ─────────────────────────────────────────────────────── */

  start({ enabled = true } = {}) {
    this.enabled = Boolean(enabled);
    this.readInstallOutcome();
    this.schedule(FIRST_CHECK_DELAY_MS);
  }

  stop() {
    if (this.timer) this.timers.clearTimeout(this.timer);
    this.timer = null;
  }

  setEnabled(enabled) {
    this.enabled = Boolean(enabled);
    if (this.enabled) this.schedule(FIRST_CHECK_DELAY_MS);
    else this.stop();
    this.set({});
    return this.enabled;
  }

  schedule(delay) {
    this.stop();
    if (!this.enabled) return;
    this.timer = this.timers.setTimeout(() => {
      this.timer = null;
      this.check({ manual: false }).finally(() => this.schedule(CHECK_INTERVAL_MS));
    }, delay);
    if (this.timer && typeof this.timer.unref === 'function') this.timer.unref();
  }

  /* ── Check ──────────────────────────────────────────────────────────── */

  /**
   * Read the feed and, when installing is possible, download a newer version.
   * Never throws: every failure ends as a state with an error code.
   */
  check({ manual = false } = {}) {
    if (this.busy) return this.busy;
    if (manual) {
      if (this.now() - this.lastManualCheck < MIN_MANUAL_INTERVAL_MS) return Promise.resolve(this.publicState());
      this.lastManualCheck = this.now();
    }
    if (this.state.status === 'ready') return Promise.resolve(this.publicState());
    this.busy = this.runCheck(manual).finally(() => { this.busy = null; });
    return this.busy;
  }

  async runCheck(manual) {
    this.set({ status: 'checking', error: null });
    let payload;
    try {
      const text = await this.fetchText(this.feedUrl);
      if (text === null) {
        // No feed published yet: not an error the person can do anything about.
        this.set({ status: 'up-to-date', lastCheck: new Date(this.now()).toISOString(), available: null });
        return this.publicState();
      }
      payload = feed.verifyFeed(text, { product: PRODUCT, keys: this.keys });
    } catch (err) {
      this.log('Verificação de atualizações falhou:', err.code || err.message);
      this.set({ status: 'error', error: err.code || 'check_failed', lastCheck: new Date(this.now()).toISOString() });
      return this.publicState();
    }

    const lastCheck = new Date(this.now()).toISOString();
    if (!feed.isNewer(payload.version, this.currentVersion)) {
      this.offer = null;
      this.set({ status: 'up-to-date', lastCheck, available: null });
      return this.publicState();
    }

    this.offer = payload;
    const available = { version: payload.version, notes: payload.notes || null, size: payload.size };
    if (!this.canInstall) {
      this.set({ status: 'available', lastCheck, available });
      return this.publicState();
    }
    if (this.installAttempts >= MAX_INSTALL_ATTEMPTS && this.state.lastResult
        && this.state.lastResult.version === payload.version && !manual) {
      // Two silent installs of this version failed; wait for the person.
      this.set({ status: 'error', error: 'install_failed', lastCheck, available });
      return this.publicState();
    }
    this.set({ status: 'downloading', lastCheck, available, progress: 0 });
    try {
      this.installerPath = await this.download(payload);
      this.set({ status: 'ready', progress: 1 });
    } catch (err) {
      this.log('Download da atualização falhou:', err.code || err.message);
      this.installerPath = null;
      this.set({ status: 'error', error: err.code || 'download_failed', progress: 0 });
    }
    return this.publicState();
  }

  /* ── Network ────────────────────────────────────────────────────────── */

  /**
   * GET over HTTPS. Redirects are followed by the network stack (GitHub sends
   * release downloads to its asset storage), and where the request finally
   * landed must be on the allow-list. What makes a download trustworthy is
   * still the signed SHA-256, not the host; this is the second fence.
   */
  async request(url, { signal } = {}) {
    if (!String(url).startsWith('https://')) throw new feed.FeedError('bad_redirect');
    const response = await this.fetch(url, {
      redirect: 'follow', signal, headers: { 'User-Agent': `NanoAssistant/${this.currentVersion}` },
    });
    if (response.url && !feed.isAllowedRedirect(response.url)) throw new feed.FeedError('bad_redirect');
    return response;
  }

  /** The feed as text, or null when it does not exist (404). */
  async fetchText(url) {
    const controller = new AbortController();
    const timer = this.timers.setTimeout(() => controller.abort(), FEED_TIMEOUT_MS);
    try {
      const response = await this.request(url, { signal: controller.signal });
      if (response.status === 404) return null;
      if (!response.ok) throw new feed.FeedError('feed_http_' + response.status);
      const text = await response.text();
      if (text.length > MAX_FEED_BYTES) throw new feed.FeedError('feed_too_large');
      return text;
    } catch (err) {
      if (err instanceof feed.FeedError) throw err;
      throw new feed.FeedError(controller.signal.aborted ? 'timeout' : 'offline');
    } finally {
      this.timers.clearTimeout(timer);
    }
  }

  /** Free bytes on the volume holding `dir`, or Infinity when unknown. */
  freeSpace(dir) {
    try {
      const stats = this.fs.statfsSync(dir);
      return Number(stats.bavail) * Number(stats.bsize);
    } catch (_) {
      return Infinity;
    }
  }

  /**
   * Download the installer the payload describes. Resolves with the path of a
   * verified file; rejects with a FeedError and leaves nothing half-written.
   */
  async download(payload) {
    const fs = this.fs;
    fs.mkdirSync(this.dir, { recursive: true });
    const target = path.join(this.dir, installerName(payload.version));

    // Already downloaded and still intact (a previous run, or a failed install).
    if (fs.existsSync(target) && (await this.hashFile(target)) === payload.sha256) {
      this.cleanup([target]);
      return target;
    }
    this.cleanup([]);

    if (this.freeSpace(this.dir) < payload.size + FREE_SPACE_MARGIN) {
      throw new feed.FeedError('no_disk_space');
    }

    const partial = target + '.part';
    const controller = new AbortController();
    let stall = null;
    const touch = () => {
      if (stall) this.timers.clearTimeout(stall);
      stall = this.timers.setTimeout(() => controller.abort(), STALL_TIMEOUT_MS);
    };
    let fd = null;
    try {
      touch();
      const response = await this.request(payload.url, { signal: controller.signal });
      if (!response.ok || !response.body) throw new feed.FeedError('download_http_' + response.status);
      const hash = crypto.createHash('sha256');
      let received = 0;
      let lastReport = 0;
      fd = fs.openSync(partial, 'w');
      const reader = response.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        touch();
        received += value.length;
        if (received > payload.size) {
          controller.abort();
          throw new feed.FeedError('download_too_large');
        }
        hash.update(value);
        fs.writeSync(fd, Buffer.from(value.buffer, value.byteOffset, value.byteLength));
        const progress = received / payload.size;
        if (progress - lastReport >= 0.02) { lastReport = progress; this.set({ progress }); }
      }
      fs.closeSync(fd);
      fd = null;
      if (received !== payload.size) throw new feed.FeedError('download_incomplete');
      if (hash.digest('hex') !== payload.sha256) throw new feed.FeedError('download_bad_hash');
      fs.renameSync(partial, target);
      return target;
    } catch (err) {
      if (fd !== null) { try { fs.closeSync(fd); } catch (_) { /* already closed */ } }
      try { fs.rmSync(partial, { force: true }); } catch (_) { /* nothing to remove */ }
      if (err instanceof feed.FeedError) throw err;
      throw new feed.FeedError(controller.signal.aborted ? 'download_stalled' : 'download_failed');
    } finally {
      if (stall) this.timers.clearTimeout(stall);
    }
  }

  hashFile(file) {
    return new Promise((resolve) => {
      const hash = crypto.createHash('sha256');
      const stream = this.fs.createReadStream(file);
      stream.on('data', (chunk) => hash.update(chunk));
      stream.on('error', () => resolve(null));
      stream.on('end', () => resolve(hash.digest('hex')));
    });
  }

  /** Delete old installers and partial downloads, keeping `keep`. */
  cleanup(keep) {
    let names = [];
    try { names = this.fs.readdirSync(this.dir); } catch (_) { return; }
    for (const name of names) {
      const full = path.join(this.dir, name);
      if (keep.includes(full) || name === PENDING_FILE) continue;
      if (/^Nano-Setup-.*\.exe(\.part)?$/.test(name)) {
        try { this.fs.rmSync(full, { force: true }); } catch (_) { /* in use; next time */ }
      }
    }
  }

  /* ── Install ────────────────────────────────────────────────────────── */

  isReady() {
    return this.state.status === 'ready' && Boolean(this.installerPath && this.offer);
  }

  /**
   * Start the installer, detached, so it can replace NANO once NANO exits.
   * The caller quits the app right after. Returns true when it was started.
   *
   * @param {object} options
   * @param {boolean} options.relaunch  open NANO again when the install ends
   */
  async install({ relaunch }) {
    if (!this.canInstall || !this.isReady()) return false;
    const file = this.installerPath;
    // Re-check the bytes we are about to run: the file sat on disk since.
    if ((await this.hashFile(file)) !== this.offer.sha256) {
      this.installerPath = null;
      try { this.fs.rmSync(file, { force: true }); } catch (_) { /* gone */ }
      this.set({ status: 'error', error: 'download_bad_hash' });
      return false;
    }
    const pending = this.readPending();
    const attempts = pending && pending.version === this.offer.version ? (pending.attempts || 0) + 1 : 1;
    try {
      this.fs.writeFileSync(path.join(this.dir, PENDING_FILE), JSON.stringify({
        version: this.offer.version,
        from: this.currentVersion,
        installer: path.basename(file),
        attempts,
        at: new Date(this.now()).toISOString(),
      }), 'utf8');
    } catch (err) {
      this.log('Não foi possível registar a atualização pendente:', err.message);
    }
    // electron-builder's NSIS installer: /S silent, --updated marks an update
    // (keeps the install folder and shortcuts), --force-run opens NANO after.
    const args = ['--updated', '/S'];
    if (relaunch) args.push('--force-run');
    try {
      const child = this.spawn(file, args, { detached: true, stdio: 'ignore', windowsHide: false });
      if (child && typeof child.on === 'function') child.on('error', (err) => this.log('Instalador falhou:', err.message));
      if (child && typeof child.unref === 'function') child.unref();
      this.log(`Atualização ${this.currentVersion} -> ${this.offer.version} iniciada.`);
      return true;
    } catch (err) {
      this.log('Não foi possível iniciar o instalador:', err.message);
      this.set({ status: 'error', error: 'install_failed' });
      return false;
    }
  }

  readPending() {
    try {
      return JSON.parse(this.fs.readFileSync(path.join(this.dir, PENDING_FILE), 'utf8'));
    } catch (_) {
      return null;
    }
  }

  /**
   * After an update ran, find out whether it worked: the running version is
   * the only honest answer.
   */
  readInstallOutcome() {
    const pending = this.readPending();
    if (!pending || typeof pending.version !== 'string') return;
    let installed = false;
    try { installed = feed.compareVersions(this.currentVersion, pending.version) >= 0; } catch (_) { installed = false; }
    if (installed) {
      try { this.fs.rmSync(path.join(this.dir, PENDING_FILE), { force: true }); } catch (_) { /* fine */ }
      this.cleanup([]);
      this.installAttempts = 0;
      this.set({ lastResult: { ok: true, version: pending.version, from: pending.from || null } });
      this.log(`Atualização para ${pending.version} concluída.`);
    } else {
      this.installAttempts = Number(pending.attempts) || 1;
      this.set({ lastResult: { ok: false, version: pending.version, attempts: this.installAttempts } });
      this.log(`A atualização para ${pending.version} não ficou instalada (tentativa ${this.installAttempts}).`);
    }
  }
}

module.exports = {
  Updater, FEED_URL, RELEASES_URL, PRODUCT, installerName,
  FIRST_CHECK_DELAY_MS, CHECK_INTERVAL_MS, MAX_INSTALL_ATTEMPTS,
};
