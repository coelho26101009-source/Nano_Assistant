/**
 * The Python backend, as owned by the Electron main process.
 *
 * Responsibilities, and nothing beyond them:
 *   * spawn the Nano backend as a child process
 *   * carry control requests to it, and events back, over the stdio pipe
 *   * know when it is genuinely ready
 *   * stop it cleanly, and make sure it is actually gone
 *
 * It is NOT an execution surface. `request()` can only send operation names the
 * Python side has explicitly registered in `DESKTOP_OPERATIONS`; there is no
 * "run this command" path here, and adding one would be a security regression,
 * not a feature. See core/desktop_bridge.py for the trust model.
 *
 * `spawnFn` and `now` are injectable purely so the lifecycle can be tested
 * against a fake child process, with no Python and no Electron.
 */
'use strict';

const EventEmitter = require('events');
const { LineReader, encode } = require('./ipc-protocol');

/** A control request that gets no answer must reject, never hang a menu item. */
const REQUEST_TIMEOUT_MS = 15000;

class NanoBackend extends EventEmitter {
  constructor({ spawnFn, log } = {}) {
    super();
    this._spawn = spawnFn || require('child_process').spawn;
    this._log = log || (() => {});
    this._child = null;
    this._reader = null;
    this._pending = new Map();
    this._nextId = 1;
    this._exited = false;
    this._stopping = false;
    this.port = null;
    this.lastExitCode = null;
  }

  get running() {
    return Boolean(this._child) && !this._exited;
  }

  get pid() {
    return this._child ? this._child.pid : null;
  }

  /**
   * Start the backend. Resolves once the process is up and has answered a
   * control ping -- i.e. it is genuinely alive, not merely spawned.
   */
  start({ command, args, options }) {
    if (this._child) throw new Error('O motor do Nano já está a correr.');
    this._exited = false;
    this._stopping = false;
    this.lastExitCode = null;

    const child = this._spawn(command, args, options);
    this._child = child;

    this._reader = new LineReader({
      onMessage: (message) => this._onMessage(message),
      onLog: (line) => { this._log(line); this.emit('log', line); },
    });
    if (child.stdout) child.stdout.on('data', (chunk) => this._reader.push(chunk));
    if (child.stderr) child.stderr.on('data', (chunk) => this._reader.push(chunk));

    child.on('error', (err) => {
      this._exited = true;
      this._rejectAll(err);
      this.emit('failed', err);
    });
    child.on('exit', (code, signal) => {
      this._exited = true;
      this.lastExitCode = code;
      this._rejectAll(new Error(`O motor do Nano terminou (${code})`));
      this.emit('exit', { code, signal, expected: this._stopping });
    });

    return child;
  }

  _onMessage(message) {
    if (message.event) {
      if (message.event === 'backend_started' && message.payload && message.payload.port) {
        this.port = Number(message.payload.port);
      }
      this.emit('event', message.event, message.payload || {});
      return;
    }
    const pending = this._pending.get(message.id);
    if (!pending) return;
    this._pending.delete(message.id);
    clearTimeout(pending.timer);
    pending.resolve(message);
  }

  _rejectAll(error) {
    for (const [, pending] of this._pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this._pending.clear();
  }

  /**
   * Send one control operation and await its answer.
   *
   * Resolves with the backend's reply envelope `{ ok, result }` -- including
   * `{ ok: false, error: 'unknown_operation' }`, which is a legitimate answer,
   * not a transport failure. Rejects only when the channel itself failed.
   */
  request(op, args = {}, { timeoutMs = REQUEST_TIMEOUT_MS } = {}) {
    return new Promise((resolve, reject) => {
      if (!this.running || !this._child.stdin || this._child.stdin.destroyed) {
        reject(new Error('O motor do Nano não está disponível.'));
        return;
      }
      const id = String(this._nextId++);
      const timer = setTimeout(() => {
        this._pending.delete(id);
        reject(new Error(`Sem resposta do motor do Nano (${op}).`));
      }, timeoutMs);
      this._pending.set(id, { resolve, reject, timer });
      try {
        this._child.stdin.write(encode({ id, op, args }));
      } catch (err) {
        this._pending.delete(id);
        clearTimeout(timer);
        reject(err);
      }
    });
  }

  /**
   * Wait until the backend answers a ping, or give up.
   *
   * This is the readiness gate Part 17 asks for: the desktop window is not
   * created until this resolves, so it can never open against a backend that
   * is not there. It fails loudly rather than retrying forever.
   */
  async waitUntilAlive({ timeoutMs = 60000, intervalMs = 400 } = {}) {
    const deadline = Date.now() + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
      if (this._exited) {
        throw new Error(`O motor do Nano terminou durante o arranque (código ${this.lastExitCode}).`);
      }
      try {
        const reply = await this.request('ping', {}, { timeoutMs: Math.min(3000, intervalMs * 6) });
        if (reply && reply.ok) return true;
      } catch (err) {
        lastError = err;
      }
      await new Promise((r) => setTimeout(r, intervalMs));
    }
    throw new Error(`O motor do Nano não ficou pronto a tempo.${lastError ? ` (${lastError.message})` : ''}`);
  }

  /**
   * Stop the backend. Asks politely first so PortAudio and SQLite close
   * properly, then makes sure by killing the process tree.
   */
  async stop({ graceMs = 4000, killTree } = {}) {
    if (!this._child) return;
    this._stopping = true;
    const child = this._child;

    const exited = new Promise((resolve) => {
      if (this._exited) { resolve(); return; }
      child.once('exit', () => resolve());
    });

    try {
      // Best effort: a backend that is wedged will not answer, and that is
      // exactly the case the hard kill below exists for.
      await this.request('shutdown', {}, { timeoutMs: 1500 }).catch(() => {});
    } catch (_) { /* falls through to the kill */ }

    const timedOut = await Promise.race([
      exited.then(() => false),
      new Promise((resolve) => setTimeout(() => resolve(true), graceMs)),
    ]);

    if (timedOut && !this._exited && child.pid) {
      if (typeof killTree === 'function') killTree(child.pid);
      else { try { child.kill(); } catch (_) { /* already gone */ } }
      await Promise.race([exited, new Promise((r) => setTimeout(r, 2000))]);
    }

    this._child = null;
    this._rejectAll(new Error('O motor do Nano foi encerrado.'));
  }
}

module.exports = { NanoBackend, REQUEST_TIMEOUT_MS };
