#!/usr/bin/env node
/**
 * Make and check NANO's signed update feeds. Used by the release workflows;
 * the format and every rule are in electron/lib/update-feed.js and
 * docs/UPDATES.md.
 *
 *   node scripts/update_feed.mjs sign --product nano-desktop --version 0.4.0-beta.1 \
 *       --url https://github.com/.../Nano-Setup-0.4.0-beta.1-x64.exe \
 *       --file electron/dist-electron/Nano-Setup-0.4.0-beta.1-x64.exe \
 *       [--notes <url>] [--channel beta] [--version-code 42] [--commit <sha>] \
 *       --out nano-update.json
 *
 *   node scripts/update_feed.mjs verify nano-update.json [--file <installer>]
 *
 * `sign` reads the private key from the NANO_UPDATE_SIGNING_KEY environment
 * variable (a PKCS#8 PEM, or that PEM in base64), hashes the file itself, and
 * then verifies what it wrote against the PUBLIC keys in the repository, so a
 * secret that does not match the shipped keys fails the release instead of
 * publishing a feed every app would reject.
 *
 * `sign` exits 3, writing nothing, when the secret is not set: the workflow
 * treats that as "no automatic update for this build" rather than a failure.
 */
import { createHash } from 'node:crypto';
import { createReadStream, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const feed = require('../electron/lib/update-feed.js');

function parseArgs(argv) {
  const options = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg.startsWith('--')) {
      const key = arg.slice(2);
      const value = argv[i + 1];
      if (value === undefined || value.startsWith('--')) throw new Error(`--${key} needs a value`);
      options[key] = value;
      i += 1;
    } else {
      options._.push(arg);
    }
  }
  return options;
}

function sha256(file) {
  return new Promise((resolve, reject) => {
    const hash = createHash('sha256');
    createReadStream(file).on('data', (chunk) => hash.update(chunk)).on('error', reject)
      .on('end', () => resolve(hash.digest('hex')));
  });
}

function signingKey() {
  const raw = (process.env.NANO_UPDATE_SIGNING_KEY || '').trim();
  if (!raw) return null;
  if (raw.includes('-----BEGIN')) return raw;
  return Buffer.from(raw, 'base64').toString('utf8');
}

function required(options, name) {
  if (!options[name]) throw new Error(`--${name} is required`);
  return options[name];
}

async function sign(options) {
  const key = signingKey();
  if (!key) {
    console.log('NANO_UPDATE_SIGNING_KEY is not set: no update feed is made for this build.');
    return 3;
  }
  const product = required(options, 'product');
  const file = required(options, 'file');
  const payload = {
    product,
    channel: options.channel || 'beta',
    version: required(options, 'version'),
    url: required(options, 'url'),
    sha256: await sha256(file),
    size: statSync(file).size,
    published: new Date().toISOString(),
  };
  if (options.notes) payload.notes = options.notes;
  if (options['version-code']) payload.versionCode = Number(options['version-code']);
  if (options.commit) payload.commit = options.commit;
  if (product === 'nano-android' && !Number.isSafeInteger(payload.versionCode)) {
    throw new Error('--version-code is required for nano-android');
  }

  const text = feed.signFeed(payload, key);
  // Checked with the repository's PUBLIC keys -- what the apps will do.
  const checked = feed.verifyFeed(text, { product });
  writeFileSync(required(options, 'out'), text, 'utf8');
  console.log(`Signed ${product} ${checked.version} (${checked.size} bytes, sha256 ${checked.sha256}).`);
  return 0;
}

async function verify(options) {
  const target = options._[1];
  if (!target) throw new Error('verify needs the feed file');
  const payload = feed.verifyFeed(readFileSync(target, 'utf8'), {});
  if (options.file) {
    if (statSync(options.file).size !== payload.size || (await sha256(options.file)) !== payload.sha256) {
      throw new Error('the file does not match the feed');
    }
  }
  console.log(`OK: ${payload.product} ${payload.version}${payload.versionCode ? ` (${payload.versionCode})` : ''}`);
  return 0;
}

const options = parseArgs(process.argv.slice(2));
const command = options._[0];
const handler = { sign, verify }[command];
if (!handler) {
  console.error('usage: update_feed.mjs sign|verify ...');
  process.exit(2);
}
handler(options).then((code) => process.exit(code), (err) => {
  console.error(`update_feed: ${err.code || ''} ${err.message}`.trim());
  process.exit(1);
});
