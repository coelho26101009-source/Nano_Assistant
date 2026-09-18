#!/usr/bin/env node
'use strict';

// Inspect the actual builder output, including the shell archive. This is a
// release gate, not a runtime capability, and never reads a user profile.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const asar = require('@electron/asar');
const crypto = require('crypto');
const root = path.resolve(process.argv[2] || path.join(__dirname, '..', 'dist-electron', 'win-unpacked'));
const canonical = require('../../version.json');
const archive = path.join(root, 'resources', 'app.asar');
const resources = path.join(root, 'resources', 'app');
const forbidden = /(^|\/)(?:\.env(?:\.[^/]*)?|\.git|\.agents|\.vscode|tests?|_probe\.json|secrets\.dat|user_settings\.json|permission_policies\.json|desktop-state\.json|credentials\.json)(\/|$)|\.(?:db|sqlite3?)(?:-(?:wal|shm|journal))?$|\.log(?:\.\d+)?$/i;
const credential = /\b(?:gsk_[A-Za-z0-9]{32,}|sk-(?:proj-)?[A-Za-z0-9_-]{32,}|AIza[A-Za-z0-9_-]{35})\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/;
const files = [];
const failures = [];

function check(name, bytes) {
  const normalized = name.replaceAll('\\', '/');
  if (forbidden.test(normalized)) failures.push(`forbidden path: ${normalized}`);
  if (bytes && /\.(?:py|js|json|ya?ml|html|txt|ini|cfg)$/i.test(normalized) && credential.test(bytes.toString('utf8'))) {
    failures.push(`credential-shaped content (withheld): ${normalized}`);
  }
}

function walk(directory) {
  for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, item.name);
    if (item.isSymbolicLink()) { failures.push(`unexpected link: ${path.relative(root, full)}`); continue; }
    const relative = path.relative(root, full).replaceAll('\\', '/');
    check(relative);
    if (item.isDirectory()) walk(full);
    else {
      files.push({ path: relative, bytes: fs.statSync(full).size });
      if (/\.(?:py|js|json|ya?ml|html|txt|ini|cfg)$/i.test(relative)) check(relative, fs.readFileSync(full));
    }
  }
}

for (const required of ['core/main.py', 'frontend/out/index.html', 'runtime/python/python.exe',
  'runtime/python/python312.dll', 'runtime/python/python312._pth', 'runtime/python/Lib/site-packages/eel/__init__.py',
  'config/settings.yaml', 'version.json', 'LICENSE', 'THIRD_PARTY_NOTICES.md']) {
  assert.ok(fs.existsSync(path.join(resources, required)), `Missing runtime resource: ${required}`);
}
for (const required of ['LICENSE.electron.txt', 'LICENSES.chromium.html']) assert.ok(fs.existsSync(path.join(root, required)), required);
assert.strictEqual(JSON.parse(fs.readFileSync(path.join(resources, 'version.json'))).product, canonical.product);
const packagedManifest = JSON.parse(asar.extractFile(archive, 'package.json'));
assert.strictEqual(packagedManifest.version, canonical.product);
for (const name of ['main.js', 'preload.js', 'lib/startup-health.js']) {
  assert.ok(asar.extractFile(archive, name).equals(fs.readFileSync(path.join(__dirname, '..', name))), `${name} is stale`);
}
for (const entry of asar.listPackage(archive)) {
  const name = entry.replace(/^[/\\]/, '').replaceAll('\\', '/');
  const stat = asar.statFile(archive, name);
  check(`app.asar/${name}`, stat.files ? undefined : asar.extractFile(archive, name));
}
walk(resources);
assert.deepStrictEqual(failures, [], 'Packaged privacy inspection failed');
const manifestDigest = crypto.createHash('sha256').update(JSON.stringify(files)).digest('hex');
console.log(JSON.stringify({ ok: true, version: canonical.product, root,
  resourceFiles: files.length, resourceBytes: files.reduce((sum, file) => sum + file.bytes, 0),
  manifestDigest, shellMatchesSource: true, forbiddenPaths: 0, credentialPatterns: 0,
  note: 'Public dependency CA certificates are retained; no developer credentials or test trees are permitted.' }, null, 2));
