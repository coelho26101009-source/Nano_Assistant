/**
 * Automatic updates: the signed feed and the updater's whole flow, with a fake
 * network, a fake installer launcher and a real temporary directory.
 */
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { assert, suite, test } = require('./harness');
const feed = require('../lib/update-feed');
const { Updater, installerName, MAX_INSTALL_ATTEMPTS } = require('../lib/updater');

/* A throwaway key for the tests; the real public key is in update-keys.json. */
const { privateKey, publicKey } = crypto.generateKeyPairSync('ec', { namedCurve: 'P-256' });
const TEST_KEYS = [{
  id: feed.keyIdFor(publicKey),
  spki: publicKey.export({ type: 'spki', format: 'der' }).toString('base64'),
}];
const DOWNLOAD = 'https://github.com/coelho26101009-source/Nano_Assistant/releases/download/v0.4.0-beta.1/Nano-Setup-0.4.0-beta.1-x64.exe';
const INSTALLER = Buffer.from('MZ fake installer bytes '.repeat(64));
const SHA = crypto.createHash('sha256').update(INSTALLER).digest('hex');

function payload(extra = {}) {
  return {
    product: 'nano-desktop', channel: 'beta', version: '0.4.0-beta.1',
    url: DOWNLOAD, sha256: SHA, size: INSTALLER.length,
    notes: 'https://github.com/coelho26101009-source/Nano_Assistant/releases/tag/v0.4.0-beta.1',
    ...extra,
  };
}

function signed(extra) { return feed.signFeed(payload(extra), privateKey); }

/* ── Feed ─────────────────────────────────────────────────────────────── */

suite('update feed');

test('a feed signed by a trusted key verifies and returns its payload', () => {
  const result = feed.verifyFeed(signed(), { product: 'nano-desktop', keys: TEST_KEYS });
  assert.strictEqual(result.version, '0.4.0-beta.1');
  assert.strictEqual(result.sha256, SHA);
});

test('a feed with a changed payload is rejected', () => {
  const envelope = JSON.parse(signed());
  const changed = JSON.parse(Buffer.from(envelope.payload, 'base64').toString());
  changed.url = DOWNLOAD.replace('v0.4.0', 'v9.9.9');
  envelope.payload = Buffer.from(JSON.stringify(changed)).toString('base64');
  assert.throws(() => feed.verifyFeed(JSON.stringify(envelope), { keys: TEST_KEYS }), /feed_bad_signature/);
});

test('a feed signed by another key is rejected, even with a known key id', () => {
  const other = crypto.generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey;
  const envelope = JSON.parse(feed.signFeed(payload(), other));
  assert.throws(() => feed.verifyFeed(JSON.stringify(envelope), { keys: TEST_KEYS }), /feed_unknown_key/);
  envelope.keyId = TEST_KEYS[0].id;
  assert.throws(() => feed.verifyFeed(JSON.stringify(envelope), { keys: TEST_KEYS }), /feed_bad_signature/);
});

test('the real trusted key list loads and is not the test key', () => {
  const keys = feed.publicKeys();
  assert.ok(keys.size >= 1);
  assert.ok(!keys.has(TEST_KEYS[0].id));
  for (const [id, key] of keys) assert.strictEqual(feed.keyIdFor(key), id, 'key id must match its key');
});

test('the phone app trusts exactly the same keys', () => {
  const ts = fs.readFileSync(path.join(__dirname, '..', '..', 'mobile', 'src', 'lib', 'updateKeys.ts'), 'utf8');
  for (const entry of require('../lib/update-keys.json').keys) {
    assert.ok(ts.includes(entry.id) && ts.includes(entry.spki), `mobile is missing key ${entry.id}`);
  }
  const count = (ts.match(/spki: "/g) || []).length;
  assert.strictEqual(count, require('../lib/update-keys.json').keys.length);
});

test('a phone feed can never update the desktop', () => {
  const text = signed({ product: 'nano-android' });
  assert.throws(() => feed.verifyFeed(text, { product: 'nano-desktop', keys: TEST_KEYS }), /feed_wrong_product/);
});

test('downloads must come from this repository over HTTPS', () => {
  for (const url of [
    'http://github.com/coelho26101009-source/Nano_Assistant/releases/download/v1/x.exe',
    'https://evil.example/coelho26101009-source/Nano_Assistant/releases/download/v1/x.exe',
    'https://github.com/someone-else/Nano_Assistant/releases/download/v1/x.exe',
    'https://github.com/coelho26101009-source/Nano_Assistant/releases/download/../../x.exe',
    'https://user:pw@github.com/coelho26101009-source/Nano_Assistant/releases/download/v1/x.exe',
    'https://github.com:8443/coelho26101009-source/Nano_Assistant/releases/download/v1/x.exe',
  ]) {
    assert.ok(!feed.isAllowedDownloadUrl(url), url);
    assert.throws(() => feed.verifyFeed(signed({ url }), { keys: TEST_KEYS }), /feed_bad_url/, url);
  }
  assert.ok(feed.isAllowedDownloadUrl(DOWNLOAD));
});

test('malformed hashes, sizes and versions are rejected', () => {
  assert.throws(() => feed.verifyFeed(signed({ sha256: 'abc' }), { keys: TEST_KEYS }), /feed_format/);
  assert.throws(() => feed.verifyFeed(signed({ size: 0 }), { keys: TEST_KEYS }), /feed_format/);
  assert.throws(() => feed.verifyFeed(signed({ size: 5e12 }), { keys: TEST_KEYS }), /feed_format/);
  assert.throws(() => feed.verifyFeed(signed({ version: 'latest' }), { keys: TEST_KEYS }), /feed_format/);
  assert.throws(() => feed.verifyFeed('not json', { keys: TEST_KEYS }), /feed_not_json/);
  assert.throws(() => feed.verifyFeed('{"format":2}', { keys: TEST_KEYS }), /feed_format/);
});

test('versions compare by SemVer, pre-releases below the release', () => {
  const order = ['0.3.0-beta.1', '0.3.0-beta.2', '0.3.0-beta.10', '0.3.0-rc.1', '0.3.0', '0.3.1', '0.4.0-beta.1', '1.0.0'];
  for (let i = 0; i < order.length - 1; i += 1) {
    assert.strictEqual(feed.compareVersions(order[i], order[i + 1]), -1, `${order[i]} < ${order[i + 1]}`);
    assert.strictEqual(feed.compareVersions(order[i + 1], order[i]), 1);
  }
  assert.strictEqual(feed.compareVersions('0.3.0-beta.1', '0.3.0-beta.1'), 0);
  assert.ok(!feed.isNewer('0.2.0-beta.1', '0.3.0-beta.1'), 'never a downgrade');
  assert.ok(!feed.isNewer('garbage', '0.3.0-beta.1'));
});

/* ── Updater ──────────────────────────────────────────────────────────── */

suite('desktop updater');

function tmpDataDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'nano-update-test-'));
}

/** A fetch that serves the feed and the installer, like GitHub would. */
function fakeFetch({ feedText = signed(), installer = INSTALLER, feedStatus = 200, finalUrl } = {}) {
  const calls = [];
  const fetch = async (url) => {
    calls.push(url);
    if (url.endsWith('nano-update.json')) {
      return {
        ok: feedStatus === 200, status: feedStatus, url,
        text: async () => feedText,
      };
    }
    const chunks = [installer.subarray(0, 100), installer.subarray(100)];
    return {
      ok: true, status: 200,
      url: finalUrl || 'https://release-assets.githubusercontent.com/github-production-release-asset/1/x',
      body: {
        getReader() {
          let i = 0;
          return { read: async () => (i < chunks.length ? { done: false, value: new Uint8Array(chunks[i++]) } : { done: true }) };
        },
      },
    };
  };
  fetch.calls = calls;
  return fetch;
}

function makeUpdater(options = {}) {
  const spawned = [];
  const states = [];
  const dataDir = options.dataDir || tmpDataDir();
  const updater = new Updater({
    currentVersion: options.currentVersion || '0.3.0-beta.1',
    canInstall: options.canInstall !== false,
    dataDir,
    fetch: options.fetch || fakeFetch(),
    spawn: (file, args, opts) => { spawned.push({ file, args, opts }); return { unref() {}, on() {} }; },
    fs,
    keys: TEST_KEYS,
    onChange: (state) => states.push(state),
    timers: { setTimeout: () => null, clearTimeout: () => {} },
  });
  return { updater, spawned, states, dataDir };
}

test('a newer signed version is downloaded, verified and made ready', async () => {
  const { updater, dataDir } = makeUpdater();
  const state = await updater.check({ manual: true });
  assert.strictEqual(state.status, 'ready');
  assert.strictEqual(state.available.version, '0.4.0-beta.1');
  const file = path.join(dataDir, 'updates', installerName('0.4.0-beta.1'));
  assert.ok(fs.readFileSync(file).equals(INSTALLER));
  assert.ok(!fs.existsSync(file + '.part'));
});

test('the same or an older version is not downloaded', async () => {
  for (const version of ['0.3.0-beta.1', '0.2.0-beta.1']) {
    const fetch = fakeFetch({ feedText: signed({ version }) });
    const { updater } = makeUpdater({ fetch });
    const state = await updater.check({ manual: true });
    assert.strictEqual(state.status, 'up-to-date', version);
    assert.strictEqual(fetch.calls.length, 1, 'only the feed was read');
  }
});

test('no feed published yet reads as up to date, not as an error', async () => {
  const { updater } = makeUpdater({ fetch: fakeFetch({ feedStatus: 404 }) });
  assert.strictEqual((await updater.check({ manual: true })).status, 'up-to-date');
});

test('a forged feed stops before any download', async () => {
  const other = crypto.generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey;
  const envelope = JSON.parse(feed.signFeed(payload(), other));
  envelope.keyId = TEST_KEYS[0].id;
  const fetch = fakeFetch({ feedText: JSON.stringify(envelope) });
  const { updater } = makeUpdater({ fetch });
  const state = await updater.check({ manual: true });
  assert.strictEqual(state.status, 'error');
  assert.strictEqual(state.error, 'feed_bad_signature');
  assert.strictEqual(fetch.calls.length, 1);
});

test('an installer whose bytes do not match is deleted and never offered', async () => {
  const tampered = Buffer.from(INSTALLER);
  tampered[5] ^= 1;
  const { updater, dataDir } = makeUpdater({ fetch: fakeFetch({ installer: tampered }) });
  const state = await updater.check({ manual: true });
  assert.strictEqual(state.status, 'error');
  assert.strictEqual(state.error, 'download_bad_hash');
  assert.deepStrictEqual(fs.readdirSync(path.join(dataDir, 'updates')), []);
  assert.strictEqual(await updater.install({ relaunch: true }), false);
});

test('a download that lands outside GitHub is refused', async () => {
  const { updater } = makeUpdater({ fetch: fakeFetch({ finalUrl: 'https://evil.example/x.exe' }) });
  const state = await updater.check({ manual: true });
  assert.strictEqual(state.error, 'bad_redirect');
});

test('a short download is incomplete, a long one is cut off', async () => {
  let { updater } = makeUpdater({ fetch: fakeFetch({ installer: INSTALLER.subarray(0, 500) }) });
  assert.strictEqual((await updater.check({ manual: true })).error, 'download_incomplete');
  ({ updater } = makeUpdater({ fetch: fakeFetch({ installer: Buffer.concat([INSTALLER, Buffer.from('x')]) }) }));
  assert.strictEqual((await updater.check({ manual: true })).error, 'download_too_large');
});

test('not enough disk space stops before downloading', async () => {
  const fetch = fakeFetch();
  const { updater } = makeUpdater({ fetch });
  updater.freeSpace = () => 10;
  assert.strictEqual((await updater.check({ manual: true })).error, 'no_disk_space');
  assert.strictEqual(fetch.calls.length, 1);
});

test('the project folder only says a version exists, it downloads nothing', async () => {
  const fetch = fakeFetch();
  const { updater } = makeUpdater({ fetch, canInstall: false });
  const state = await updater.check({ manual: true });
  assert.strictEqual(state.status, 'available');
  assert.strictEqual(state.mode, 'source');
  assert.strictEqual(fetch.calls.length, 1);
  assert.strictEqual(await updater.install({ relaunch: true }), false);
});

test('install runs the verified installer silently and records the attempt', async () => {
  const { updater, spawned, dataDir } = makeUpdater();
  await updater.check({ manual: true });
  assert.strictEqual(await updater.install({ relaunch: true }), true);
  assert.strictEqual(spawned.length, 1);
  assert.deepStrictEqual(spawned[0].args, ['--updated', '/S', '--force-run']);
  assert.strictEqual(spawned[0].opts.detached, true);
  const pending = JSON.parse(fs.readFileSync(path.join(dataDir, 'updates', 'pending-update.json'), 'utf8'));
  assert.strictEqual(pending.version, '0.4.0-beta.1');
  assert.strictEqual(pending.attempts, 1);
});

test('install on quit does not reopen NANO', async () => {
  const { updater, spawned } = makeUpdater();
  await updater.check({ manual: true });
  await updater.install({ relaunch: false });
  assert.deepStrictEqual(spawned[0].args, ['--updated', '/S']);
});

test('an installer changed on disk after download is not run', async () => {
  const { updater, spawned, dataDir } = makeUpdater();
  await updater.check({ manual: true });
  fs.writeFileSync(path.join(dataDir, 'updates', installerName('0.4.0-beta.1')), 'swapped');
  assert.strictEqual(await updater.install({ relaunch: true }), false);
  assert.strictEqual(spawned.length, 0);
  assert.strictEqual(updater.publicState().error, 'download_bad_hash');
});

test('after a successful update the next start says so and cleans up', async () => {
  const first = makeUpdater();
  await first.updater.check({ manual: true });
  await first.updater.install({ relaunch: true });
  const second = makeUpdater({ dataDir: first.dataDir, currentVersion: '0.4.0-beta.1' });
  second.updater.readInstallOutcome();
  assert.deepStrictEqual(second.updater.publicState().lastResult, { ok: true, version: '0.4.0-beta.1', from: '0.3.0-beta.1' });
  assert.deepStrictEqual(fs.readdirSync(path.join(first.dataDir, 'updates')), []);
});

test('a failed update is reported, retried, then left for the person', async () => {
  const { dataDir } = makeUpdater();
  for (let attempt = 1; attempt <= MAX_INSTALL_ATTEMPTS; attempt += 1) {
    const run = makeUpdater({ dataDir });
    run.updater.readInstallOutcome();
    const state = await run.updater.check({ manual: false });
    assert.strictEqual(state.status, 'ready', `attempt ${attempt} is offered again`);
    await run.updater.install({ relaunch: false });
  }
  const after = makeUpdater({ dataDir });
  after.updater.readInstallOutcome();
  assert.strictEqual(after.updater.publicState().lastResult.ok, false);
  assert.strictEqual(after.updater.publicState().lastResult.attempts, MAX_INSTALL_ATTEMPTS);
  const automatic = await after.updater.check({ manual: false });
  assert.strictEqual(automatic.status, 'error');
  assert.strictEqual(automatic.error, 'install_failed');
  const manual = await after.updater.check({ manual: true });
  assert.strictEqual(manual.status, 'ready', 'the person can still ask for it');
});

test('a network failure is an error state, never an exception', async () => {
  const { updater } = makeUpdater({ fetch: async () => { throw new Error('ENOTFOUND'); } });
  const state = await updater.check({ manual: true });
  assert.strictEqual(state.status, 'error');
  assert.strictEqual(state.error, 'offline');
});

test('switching automatic updates off stops the schedule', () => {
  const scheduled = [];
  const updater = new Updater({
    currentVersion: '0.3.0-beta.1', canInstall: true, dataDir: tmpDataDir(), fetch: fakeFetch(),
    spawn: () => ({}), fs, keys: TEST_KEYS,
    timers: { setTimeout: (fn, ms) => { scheduled.push(ms); return { ms }; }, clearTimeout: () => {} },
  });
  updater.start({ enabled: false });
  assert.strictEqual(scheduled.length, 0);
  updater.setEnabled(true);
  assert.strictEqual(scheduled.length, 1);
  assert.strictEqual(updater.publicState().enabled, true);
});
