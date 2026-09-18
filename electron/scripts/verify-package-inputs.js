#!/usr/bin/env node
/**
 * Everything electron-builder is about to package, checked before it runs.
 *
 * WHY THIS EXISTS. electron-builder does not fail when an `extraResources`
 * source is missing -- it logs and carries on -- so a build started before the
 * frontend was exported, or before the embedded Python runtime was prepared,
 * produced a perfectly valid installer for an application that cannot start.
 * `findPython()` in main.js throws "esta instalação está incompleta" at the
 * user instead, which is the latest possible moment to discover it.
 *
 * The second half is a privacy gate. `extraResources` used to copy the whole of
 * `../runtime`, which on a developer machine also holds `runtime/benchmarks`
 * (raw model output for that person's own prompts) and
 * `runtime/speech_benchmark` (recordings of their voice). Both are gitignored
 * because they are private, and neither belongs in a file handed to strangers.
 * The config now names `runtime/python` explicitly; this asserts the result.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const ELECTRON_DIR = path.join(__dirname, '..');
const REPO = path.join(ELECTRON_DIR, '..');

const problems = [];

/** A file that must exist, and why the build is pointless without it. */
const REQUIRED_FILES = [
  ['version.json', 'core/version.py reads it; without it Nano reports an unknown version'],
  ['core/main.py', 'the backend entry point'],
  ['config/settings.yaml', 'the default configuration'],
  ['frontend/out/index.html', 'the exported UI -- run `npm run build` in frontend/ first'],
  ['runtime/python/python.exe', 'the embedded interpreter -- run scripts/prepare_windows_runtime.ps1 first'],
  ['runtime/python/python312._pth', 'the relocatable embedded interpreter search path'],
  ['runtime/python/Lib/site-packages/eel/__init__.py', 'the backend web bridge dependency'],
  ['electron/main.js', 'the desktop shell entry point'],
  ['electron/preload.js', 'the renderer bridge'],
  ['electron/assets/icon.ico', 'the application and installer icon'],
];

for (const [rel, why] of REQUIRED_FILES) {
  if (!fs.existsSync(path.join(REPO, rel))) problems.push(`missing ${rel} -- ${why}`);
}

/**
 * Trees that are copied into the package AND are Nano's own, so anything
 * private found in them arrived from this machine.
 *
 * `runtime/python/Lib/site-packages` is deliberately excluded. Its contents are
 * third-party packages installed by pip, and they legitimately contain files
 * that match every pattern below: certifi ships `cacert.pem`, which is the CA
 * bundle TLS depends on, and gevent, future and pygame ship .pem fixtures and
 * .wav samples in their own test and example folders. Scanning them produced 33
 * findings, none of them real, which is the fastest way to teach someone to
 * ignore a security check. Dependency bloat is pruned in
 * scripts/prepare_windows_runtime.ps1 instead, where it belongs.
 */
const PACKAGED_TREES = ['core', 'plugins', 'config', 'frontend/out',
  'electron/lib', 'electron/overlay', 'electron/assets'];

/** Names that must never appear inside a packaged tree, whatever the reason. */
const FORBIDDEN = [
  { test: (n) => n === '.env' || n.startsWith('.env.'), label: 'environment file (may hold API keys)' },
  { test: (n) => /\.(db|sqlite|sqlite3)(-(wal|shm|journal))?$/.test(n), label: 'database (may hold conversations)' },
  { test: (n) => /\.log(?:\.\d+)?$/.test(n), label: 'developer log' },
  { test: (n) => ['secrets.dat', 'user_settings.json', 'permission_policies.json', 'desktop-state.json'].includes(n), label: 'durable user profile' },
  { test: (n) => ['.git', '.agents', '.vscode', 'tests', 'test', '_probe.json', 'credentials.json', 'secrets.json', 'settings.user.json'].includes(n), label: 'private or development-only file/directory' },
  { test: (n) => n.endsWith('.pem') || n.endsWith('.key'), label: 'private key' },
  { test: (n) => n.endsWith('.wav') || n.endsWith('.mp3'), label: 'audio recording' },
];

function walk(dir, onFile, onDirectory = () => true) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (error) {
    if (error.code !== 'ENOENT') problems.push(`cannot inspect ${path.relative(REPO, dir)}`);
    return;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) {
      problems.push(`${path.relative(REPO, full)} is a link; packaged inputs must be regular files/directories`);
    } else if (entry.isDirectory()) {
      if (entry.name === '__pycache__') continue; // Excluded by builder filters.
      if (onDirectory(full, entry.name) !== false) walk(full, onFile, onDirectory);
    } else onFile(full, entry.name);
  }
}

function scanForForbidden(absRoot, vendored = false) {
  const check = (full, name) => {
    const normalizedName = name.toLowerCase();
    for (const rule of FORBIDDEN) {
      // Dependency CA bundles, recordings and test data are legitimate upstream
      // content; the runtime preparer prunes tests. Environment files, local DBs,
      // logs and credentials remain forbidden even inside a dependency.
      if (vendored && ['private key', 'audio recording'].includes(rule.label)) continue;
      if (rule.test(normalizedName)) {
        problems.push(`${path.relative(REPO, full).split(path.sep).join('/')} would be packaged -- ${rule.label}`);
      }
    }
    if (fs.statSync(full).isFile() && /\.(js|py|json|ya?ml|html|txt|ini|cfg)$/i.test(name)) {
      const content = fs.readFileSync(full, 'utf8');
      if (/\b(?:gsk_[A-Za-z0-9]{32,}|sk-(?:proj-)?[A-Za-z0-9_-]{32,}|AIza[A-Za-z0-9_-]{35})\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/.test(content)) {
        problems.push(`${path.relative(REPO, full)} contains a credential-shaped value (value withheld)`);
      }
    }
  };
  walk(absRoot, check, (full, name) => {
    check(full, name);
    return true;
  });
}

for (const tree of PACKAGED_TREES) scanForForbidden(path.join(REPO, tree));

// Apply the strict policy outside site-packages and a narrower privacy policy
// to dependencies, where public CA bundles are expected.
const SITE_PACKAGES = path.join(REPO, 'runtime', 'python', 'Lib', 'site-packages');
scanForForbidden(SITE_PACKAGES, true);
for (const rel of ['electron/main.js', 'electron/preload.js']) {
  if (fs.existsSync(path.join(REPO, rel))) {
    // Scan these individual shell entry points without including the private
    // development files beside them in electron/.
    const contents = fs.readFileSync(path.join(REPO, rel), 'utf8');
    if (/\b(?:gsk_[A-Za-z0-9]{32,}|sk-(?:proj-)?[A-Za-z0-9_-]{32,})\b/.test(contents)) {
      problems.push(`${rel} contains a credential-shaped value (value withheld)`);
    }
  }
}
walk(path.join(REPO, 'runtime', 'python'), (full, name) => {
  for (const rule of FORBIDDEN) {
    if (rule.test(name.toLowerCase())) problems.push(`${path.relative(REPO, full)} would be packaged -- ${rule.label}`);
  }
}, (full) => full !== SITE_PACKAGES);

/*
 * The exported frontend must have been built from the CURRENT version.json.
 *
 * `frontend/lib/version.ts` imports version.json at BUILD time, deliberately --
 * the lockup has to show a version before the eel bridge connects, and even if
 * it never connects. The cost is that `frontend/out` freezes whatever the
 * version was when it was exported. Bumping version.json and packaging without
 * re-running the frontend build produced an installer whose title bar said
 * "v1.0" while the shell and the backend both reported 0.1.0-beta.1, and
 * nothing anywhere failed. This reads the built bundle, so it cannot be
 * satisfied by the source being correct.
 */
const versionFile = path.join(REPO, 'version.json');
if (fs.existsSync(versionFile)) {
  const record = JSON.parse(fs.readFileSync(versionFile, 'utf8'));
  const out = path.join(REPO, 'frontend', 'out');
  let found = false;
  walk(out, (full, name) => {
    if (found) return;
    if (!/\.(js|html|txt|json)$/i.test(name)) return;
    if (fs.readFileSync(full, 'utf8').includes(record.display)) found = true;
  });
  if (fs.existsSync(out) && !found) {
    problems.push(
      `frontend/out does not contain "${record.display}" -- the exported UI was built from an ` +
      'older version.json. Re-run `npm run build` in frontend/ before packaging.'
    );
  }
}

// The developer's private measurement folders sit beside runtime/python. They
// are only ever packaged by a config that copies runtime/ wholesale, so this
// asserts the narrowed `from` rather than the folders' absence.
const builderConfig = require(path.join(ELECTRON_DIR, 'package.json')).build;
const allowedResources = new Set(['../core', '../plugins', '../config', '../frontend/out', '../runtime/python', '../version.json', '../LICENSE', '../THIRD_PARTY_NOTICES.md']);
for (const entry of builderConfig.extraResources || []) {
  if (!allowedResources.has(entry.from)) problems.push(`extraResources source is not approved: ${entry.from}`);
}
const runtimeEntries = (builderConfig.extraResources || []).filter((r) => String(r.from).includes('runtime'));
for (const entry of runtimeEntries) {
  if (entry.from !== '../runtime/python') {
    problems.push(
      `extraResources copies "${entry.from}": only ../runtime/python may be packaged, ` +
      'because runtime/benchmarks and runtime/speech_benchmark hold private developer data'
    );
  }
}

if (problems.length) {
  console.error('\nPackaging inputs are not ready:\n');
  for (const p of problems) console.error('  - ' + p);
  console.error('');
  process.exit(1);
}

console.log(`Packaging inputs OK (${REQUIRED_FILES.length} required files, ${PACKAGED_TREES.length} trees scanned).`);
