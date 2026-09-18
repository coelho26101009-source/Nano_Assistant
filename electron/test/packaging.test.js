/**
 * The packaging contract: what goes into a Nano installer, and what must not.
 *
 * Every assertion here corresponds to something that was actually wrong before
 * the packaging pass, and each one failed silently rather than loudly -- the
 * build succeeded and produced an installer that was broken, dishonest or
 * carried private files.
 *
 * The input verifier is exercised by RUNNING it against real directory trees
 * rather than by reading its source, so a check that stopped working would be
 * caught here instead of being taken on faith.
 */
'use strict';

const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { assert, suite, test } = require('./harness');

const ELECTRON_DIR = path.join(__dirname, '..');
const REPO = path.join(ELECTRON_DIR, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(ELECTRON_DIR, 'package.json'), 'utf8'));
const build = pkg.build;

const readJson = (p) => JSON.parse(fs.readFileSync(p, 'utf8'));

suite('packaging: one version, everywhere');

test('version.json, the Electron package and the frontend package agree', () => {
  const canonical = readJson(path.join(REPO, 'version.json'));
  const frontend = readJson(path.join(REPO, 'frontend', 'package.json'));

  assert.strictEqual(pkg.version, canonical.product,
    'electron-builder stamps the installer from electron/package.json; it must be the canonical version');
  assert.strictEqual(frontend.version, canonical.product,
    'a different frontend version implies an independent product release that does not exist');
  assert.ok(!/^8\./.test(pkg.version) && !/^8\./.test(frontend.version),
    'the legacy 8.1.0 must not come back');
});

test('the version record still declares a channel, and it is not a false "stable"', () => {
  const canonical = readJson(path.join(REPO, 'version.json'));
  assert.ok(canonical.channel, 'Settings renders VERSION.channel; it cannot be empty');
  assert.notStrictEqual(canonical.channel, 'stable',
    'Nano has never had a public release, and Settings shows this string to the user');
});

test('the lockfiles carry the canonical version too, so npm ci stays reproducible', () => {
  const canonical = readJson(path.join(REPO, 'version.json'));
  for (const rel of ['electron/package-lock.json', 'frontend/package-lock.json']) {
    const lock = readJson(path.join(REPO, rel));
    assert.strictEqual(lock.version, canonical.product, `${rel} root version`);
    assert.strictEqual(lock.packages[''].version, canonical.product, `${rel} packages[""] version`);
  }
});

suite('packaging: build configuration');

test('the output directory is the one the workflow verifies and uploads', () => {
  // `../dist-electron` wrote to the REPOSITORY root while
  // .github/workflows/build-windows.yml looked in `electron/dist-electron`, so
  // the workflow could never find an artifact.
  assert.strictEqual(build.directories.output, 'dist-electron',
    'output must resolve inside electron/, where build-windows.yml looks');

  const workflow = fs.readFileSync(
    path.join(REPO, '.github', 'workflows', 'build-windows.yml'), 'utf8');
  assert.ok(workflow.includes('electron/dist-electron'),
    'the workflow must reference the same directory the builder writes to');
});

test('installer artifact names carry the version and contain no spaces', () => {
  for (const target of ['nsis', 'msi']) {
    const name = build[target].artifactName;
    assert.ok(name, `${target} must name its artifact rather than take a default`);
    assert.ok(name.includes('${version}'), `${target} artifact name must carry the version`);
    assert.ok(!name.includes(' '),
      `${target} artifact name must not contain a space: it breaks shell globs and upload patterns`);
  }
});

test('installers stay per-user and never silently request elevation', () => {
  assert.strictEqual(build.win.requestedExecutionLevel, 'asInvoker');
  assert.strictEqual(build.nsis.perMachine, false);
  assert.strictEqual(build.msi.perMachine, false);
  assert.strictEqual(build.nsis.oneClick, false,
    'a one-click installer gives the user no chance to see what is happening');
});

test('the uninstaller does not delete the user\'s data', () => {
  // electron-builder wipes app.getPath('userData') on uninstall only when
  // `deleteAppDataOnUninstall` is true. Nano's conversations, memories,
  // settings and permissions live in %LOCALAPPDATA%\NanoAssistant, which the
  // uninstaller never touches either. Making this explicit means turning
  // destruction on becomes a deliberate, reviewable edit.
  assert.strictEqual(build.nsis.deleteAppDataOnUninstall, false,
    'uninstalling must not destroy user data without asking');
});

suite('packaging: what is inside the package');

test('version.json is packaged, or the installed backend reports an unknown version', () => {
  const entries = build.extraResources.map((r) => r.from);
  assert.ok(entries.includes('../version.json'),
    'core/version.py reads <app root>/version.json; without it Nano reports "desconhecida"');
});

test('only the embedded interpreter is taken from runtime/', () => {
  // `../runtime` copied wholesale also shipped runtime/benchmarks (raw model
  // output for the developer's own prompts) and runtime/speech_benchmark
  // (recordings of their voice). Both are gitignored because they are private.
  const runtimeEntries = build.extraResources.filter((r) => String(r.from).includes('runtime'));
  assert.ok(runtimeEntries.length > 0, 'the embedded runtime must be packaged');
  for (const entry of runtimeEntries) {
    assert.strictEqual(entry.from, '../runtime/python',
      'nothing under runtime/ except the interpreter may be packaged');
  }
});

test('python bytecode caches are excluded from every packaged source tree', () => {
  for (const rel of ['../core', '../plugins']) {
    const entry = build.extraResources.find((r) => r.from === rel);
    assert.ok(entry, `${rel} must be packaged`);
    assert.ok((entry.filter || []).includes('!**/__pycache__/**'),
      `${rel} must exclude __pycache__`);
  }
});

test('the asar allowlist ships the shell and nothing else', () => {
  assert.ok(build.files.includes('main.js'));
  assert.ok(build.files.includes('preload.js'));
  for (const forbidden of ['test/**/*', 'scripts/**/*', 'node_modules/**/*']) {
    assert.ok(!build.files.includes(forbidden),
      `${forbidden} must not be listed: files is an allowlist and these do not ship`);
  }
});

suite('packaging: the input verifier actually verifies');

/** Build a minimal tree that satisfies every requirement, then break one thing. */
function fixture(mutate) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'nano-pkg-fixture-'));
  const write = (rel, body) => {
    const full = path.join(dir, rel);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, body);
  };
  write('version.json', JSON.stringify({ product: '9.9.9', display: 'v9.9.9', name: 'N', channel: 'beta' }));
  write('core/main.py', '# main');
  write('config/settings.yaml', 'a: 1');
  write('frontend/out/index.html', '<html>v9.9.9</html>');
  write('runtime/python/python.exe', 'stub');
  write('runtime/python/python312._pth', 'python312.zip\n.\nLib\\site-packages\nimport site\n');
  write('runtime/python/Lib/site-packages/eel/__init__.py', '# stub');
  write('electron/main.js', '// main');
  write('electron/preload.js', '// preload');
  write('electron/assets/icon.ico', 'stub');
  // The verifier reads the builder config from the electron/ dir it lives in.
  write('electron/package.json', JSON.stringify({
    build: { extraResources: [{ from: '../runtime/python', to: 'app/runtime/python' }] },
  }));
  fs.mkdirSync(path.join(dir, 'electron', 'scripts'), { recursive: true });
  fs.copyFileSync(
    path.join(ELECTRON_DIR, 'scripts', 'verify-package-inputs.js'),
    path.join(dir, 'electron', 'scripts', 'verify-package-inputs.js'));
  if (mutate) mutate(dir, write);
  return dir;
}

function runVerifier(dir) {
  try {
    const stdout = execFileSync(process.execPath,
      [path.join(dir, 'electron', 'scripts', 'verify-package-inputs.js')],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
    return { code: 0, output: stdout };
  } catch (err) {
    return { code: err.status, output: (err.stdout || '') + (err.stderr || '') };
  }
}

test('a complete input set passes', () => {
  const dir = fixture();
  try {
    const r = runVerifier(dir);
    assert.strictEqual(r.code, 0, `expected a pass, got:\n${r.output}`);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('a missing embedded interpreter fails the build, not the user', () => {
  const dir = fixture((d) => fs.rmSync(path.join(d, 'runtime'), { recursive: true, force: true }));
  try {
    const r = runVerifier(dir);
    assert.notStrictEqual(r.code, 0, 'a build with no Python runtime must not proceed');
    assert.ok(/runtime[\\/]python[\\/]python\.exe/.test(r.output), r.output);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('a stale frontend export fails the build', () => {
  const dir = fixture((d, write) => write('frontend/out/index.html', '<html>v1.0</html>'));
  try {
    const r = runVerifier(dir);
    assert.notStrictEqual(r.code, 0, 'an export built from an older version.json must not ship');
    assert.ok(/older version\.json/.test(r.output), r.output);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('a stray .env inside a packaged tree fails the build', () => {
  const dir = fixture((d, write) => write('core/.env', 'GROQ_API_KEY=secret'));
  try {
    const r = runVerifier(dir);
    assert.notStrictEqual(r.code, 0, 'an API key must never be packaged');
    assert.ok(/environment file/.test(r.output), r.output);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('a conversation database inside a packaged tree fails the build', () => {
  const dir = fixture((d, write) => write('core/nano.db', 'sqlite'));
  try {
    const r = runVerifier(dir);
    assert.notStrictEqual(r.code, 0, 'user conversations must never be packaged');
    assert.ok(/database/.test(r.output), r.output);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('widening the runtime back to the whole folder fails the build', () => {
  const dir = fixture((d, write) => write('electron/package.json', JSON.stringify({
    build: { extraResources: [{ from: '../runtime', to: 'app/runtime' }] },
  })));
  try {
    const r = runVerifier(dir);
    assert.notStrictEqual(r.code, 0,
      'copying runtime/ wholesale would ship the developer\'s benchmarks and voice recordings');
    assert.ok(/only \.\.\/runtime\/python may be packaged/.test(r.output), r.output);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('the CA bundle and library test fixtures do not trip the privacy scan', () => {
  // certifi's cacert.pem is required for TLS, and gevent, future and pygame
  // ship .pem and .wav files in their own trees. Scanning vendored packages
  // produced 33 findings, none real, which is how a security check gets
  // ignored. The scan covers Nano's own trees plus the runtime outside
  // site-packages.
  const dir = fixture((d, write) => {
    write('runtime/python/Lib/site-packages/certifi/cacert.pem', 'CA bundle');
    write('runtime/python/Lib/site-packages/pygame/examples/data/boom.wav', 'RIFF');
  });
  try {
    const r = runVerifier(dir);
    assert.strictEqual(r.code, 0, `vendored dependency files must not fail the build:\n${r.output}`);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('a secret dropped beside python.exe is still caught', () => {
  const dir = fixture((d, write) => write('runtime/python/.env', 'GROQ_API_KEY=secret'));
  try {
    const r = runVerifier(dir);
    assert.notStrictEqual(r.code, 0, 'the runtime root is still scanned, only site-packages is not');
    assert.ok(/environment file/.test(r.output), r.output);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('privacy checks include shell assets, SQLite sidecars and case-insensitive environment names', () => {
  for (const relative of ['electron/assets/.ENV', 'electron/lib/conversations.sqlite-wal',
    'electron/overlay/startup.log', 'core/.git/config', 'config/credentials.json', 'core/tests/private.txt',
    'core/secrets.dat', 'config/user_settings.json', 'plugins/permission_policies.json',
    'runtime/python/Lib/site-packages/stray/secrets.dat', 'electron/assets/nano.log.1']) {
    const dir = fixture((d, write) => write(relative, 'synthetic private fixture'));
    try {
      const result = runVerifier(dir);
      assert.notStrictEqual(result.code, 0, `${relative} must not ship`);
    } finally { fs.rmSync(dir, { recursive: true, force: true }); }
  }
});

test('vendored libraries cannot smuggle environment files despite the public CA exception', () => {
  const dir = fixture((d, write) => write('runtime/python/Lib/site-packages/stray/.env', 'synthetic'));
  try {
    const result = runVerifier(dir);
    assert.notStrictEqual(result.code, 0);
    assert.ok(/environment file/.test(result.output), result.output);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('credential-shaped source values block packaging without being printed', () => {
  const syntheticKey = 'gsk_' + 'Z'.repeat(48);
  const dir = fixture((d, write) => write('config/settings.yaml', `api_key: "${syntheticKey}"`));
  try {
    const result = runVerifier(dir);
    assert.notStrictEqual(result.code, 0);
    assert.ok(result.output.includes('value withheld'));
    assert.ok(!result.output.includes(syntheticKey), 'the error must not disclose the credential');
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('an unapproved resource source is rejected', () => {
  const dir = fixture((d, write) => write('electron/package.json', JSON.stringify({
    build: { extraResources: [{ from: '../data', to: 'app/data' }] },
  })));
  try {
    const result = runVerifier(dir);
    assert.notStrictEqual(result.code, 0);
    assert.ok(result.output.includes('source is not approved'));
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('a bare Python interpreter without backend dependencies is rejected', () => {
  const dir = fixture((d) => fs.rmSync(path.join(d, 'runtime/python/Lib'), { recursive: true, force: true }));
  try {
    const result = runVerifier(dir);
    assert.notStrictEqual(result.code, 0);
    assert.ok(result.output.includes('eel/__init__.py'));
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
