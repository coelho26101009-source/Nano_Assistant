/**
 * NANO update feeds: the signed file that says "version X exists, here is
 * where to get it and what its bytes hash to".
 *
 * PURE. No network, no filesystem, no Electron. The desktop updater
 * (lib/updater.js), the release workflow's signing script
 * (scripts/update_feed.mjs) and the tests all use this one implementation, so
 * what CI signs and what the app accepts can never drift apart.
 *
 * THE FORMAT (docs/UPDATES.md has the full story)
 *
 *     { "format": 1, "keyId": "<16 hex>", "payload": "<base64 JSON>",
 *       "signature": "<base64 ECDSA P-256 / SHA-256, IEEE P1363 r||s>" }
 *
 * The signature covers the exact payload BYTES, never a re-serialised object,
 * so no JSON canonicalisation question can ever arise. ECDSA P-256 rather than
 * Ed25519 because it is the one curve every verifier NANO has can check without
 * a new dependency: Node's crypto here, WebCrypto in the Android WebView, and
 * the JDK on every Android version.
 *
 * WHAT A FEED MUST SATISFY BEFORE ANYTHING IS DOWNLOADED
 *   * signed by a key in update-keys.json (unknown key id -> rejected);
 *   * for this product (a phone feed can never update the desktop);
 *   * a download URL on https://github.com under this repository's releases
 *     (under its current or its former account name, see REPO_PATHS);
 *   * a sha256 of 64 hex characters and a plausible size.
 * Being NEWER than what is installed is checked separately (isNewer), because
 * an older signed feed is valid, just not interesting -- that is what defeats
 * replaying an old feed to push a downgrade.
 */
'use strict';

const crypto = require('crypto');
const KEYS = require('./update-keys.json');

const FORMAT = 1;
/**
 * The GitHub account was renamed (coelho26101009-source -> coelho97sc, 2026-11-25).
 * Apps installed before that accept ONLY the old path, and GitHub redirects it to
 * the new one, so the release workflows still sign old-path URLs (a test holds
 * that). This app trusts both, so a later Beta can switch the workflows to the
 * new name. The mobile app (updates.ts) and its Java plugin must list the same
 * paths; a test compares all three.
 */
const REPO_PATHS = [
  '/coelho97sc/Nano_Assistant/releases/download/',
  '/coelho26101009-source/Nano_Assistant/releases/download/',
];
/** The installer is ~150 MB; anything past this is not an installer of ours. */
const MAX_SIZE = { 'nano-desktop': 1024 * 1024 * 1024, 'nano-android': 300 * 1024 * 1024 };

class FeedError extends Error {
  constructor(code, message) {
    super(message || code);
    this.code = code;
  }
}

function publicKeys(keys = KEYS.keys) {
  const map = new Map();
  for (const entry of keys) {
    map.set(entry.id, crypto.createPublicKey({
      key: Buffer.from(entry.spki, 'base64'), format: 'der', type: 'spki',
    }));
  }
  return map;
}

/** The key id is the first 16 hex characters of SHA-256 over the SPKI bytes. */
function keyIdFor(publicKey) {
  const der = publicKey.export({ type: 'spki', format: 'der' });
  return crypto.createHash('sha256').update(der).digest('hex').slice(0, 16);
}

function isHex(value, length) {
  return typeof value === 'string' && value.length === length && /^[0-9a-f]+$/.test(value);
}

/** Only HTTPS downloads from this repository's GitHub releases. */
function isAllowedDownloadUrl(value) {
  let url;
  try { url = new URL(value); } catch (_) { return false; }
  return url.protocol === 'https:'
    && url.hostname === 'github.com'
    && !url.username && !url.password && !url.port
    && REPO_PATHS.some((repoPath) => url.pathname.startsWith(repoPath))
    && !url.pathname.includes('..')
    && !url.search && !url.hash;
}

/**
 * Where GitHub may send a release download. github.com answers with a redirect
 * to its asset storage; nothing else, and never plain HTTP.
 */
function isAllowedRedirect(value) {
  let url;
  try { url = new URL(value); } catch (_) { return false; }
  if (url.protocol !== 'https:' || url.username || url.password) return false;
  return url.hostname === 'github.com'
    || url.hostname === 'objects.githubusercontent.com'
    || url.hostname === 'release-assets.githubusercontent.com'
    || url.hostname === 'github-releases.githubusercontent.com';
}

/**
 * Verify a feed's signature and shape. Returns the payload object, or throws
 * a FeedError whose `code` says what was wrong (never the raw input).
 */
function verifyFeed(text, { product, keys } = {}) {
  let envelope;
  try { envelope = typeof text === 'string' ? JSON.parse(text) : text; } catch (_) {
    throw new FeedError('feed_not_json');
  }
  if (!envelope || typeof envelope !== 'object' || envelope.format !== FORMAT) {
    throw new FeedError('feed_format');
  }
  const key = publicKeys(keys).get(envelope.keyId);
  if (!key) throw new FeedError('feed_unknown_key');
  if (typeof envelope.payload !== 'string' || typeof envelope.signature !== 'string') {
    throw new FeedError('feed_format');
  }
  const payloadBytes = Buffer.from(envelope.payload, 'base64');
  const signature = Buffer.from(envelope.signature, 'base64');
  let valid = false;
  try {
    valid = crypto.verify('sha256', payloadBytes, { key, dsaEncoding: 'ieee-p1363' }, signature);
  } catch (_) { valid = false; }
  if (!valid) throw new FeedError('feed_bad_signature');

  let payload;
  try { payload = JSON.parse(payloadBytes.toString('utf8')); } catch (_) {
    throw new FeedError('feed_format');
  }
  if (!payload || typeof payload !== 'object') throw new FeedError('feed_format');
  if (product && payload.product !== product) throw new FeedError('feed_wrong_product');
  if (!isAllowedDownloadUrl(payload.url)) throw new FeedError('feed_bad_url');
  if (!isHex(payload.sha256, 64)) throw new FeedError('feed_format');
  const max = MAX_SIZE[payload.product] || 0;
  if (!Number.isSafeInteger(payload.size) || payload.size <= 0 || payload.size > max) {
    throw new FeedError('feed_format');
  }
  if (typeof payload.version !== 'string' || !parseVersion(payload.version)) {
    throw new FeedError('feed_format');
  }
  return payload;
}

/** Sign a payload object (used by CI). Returns the envelope as pretty JSON. */
function signFeed(payload, privateKey) {
  const key = typeof privateKey === 'string' ? crypto.createPrivateKey(privateKey) : privateKey;
  const publicKey = crypto.createPublicKey(key);
  const payloadBytes = Buffer.from(JSON.stringify(payload), 'utf8');
  const signature = crypto.sign('sha256', payloadBytes, { key, dsaEncoding: 'ieee-p1363' });
  return JSON.stringify({
    format: FORMAT,
    keyId: keyIdFor(publicKey),
    payload: payloadBytes.toString('base64'),
    signature: signature.toString('base64'),
  }, null, 2) + '\n';
}

/* ── Versions ──────────────────────────────────────────────────────────── */

/** Semantic version: 1.2.3 or 1.2.3-beta.1. Build metadata is not accepted. */
function parseVersion(value) {
  const match = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/.exec(String(value || '').trim());
  if (!match) return null;
  const pre = match[4] ? match[4].split('.') : [];
  if (pre.some((part) => part === '')) return null;
  return { core: [Number(match[1]), Number(match[2]), Number(match[3])], pre };
}

/** -1, 0 or 1, by the SemVer 2.0 precedence rules (pre-releases sort lower). */
function compareVersions(a, b) {
  const x = parseVersion(a);
  const y = parseVersion(b);
  if (!x || !y) throw new FeedError('bad_version');
  for (let i = 0; i < 3; i += 1) {
    if (x.core[i] !== y.core[i]) return x.core[i] < y.core[i] ? -1 : 1;
  }
  if (!x.pre.length || !y.pre.length) {
    if (x.pre.length === y.pre.length) return 0;
    return x.pre.length ? -1 : 1;
  }
  const length = Math.max(x.pre.length, y.pre.length);
  for (let i = 0; i < length; i += 1) {
    const p = x.pre[i];
    const q = y.pre[i];
    if (p === undefined) return -1;
    if (q === undefined) return 1;
    const pn = /^\d+$/.test(p);
    const qn = /^\d+$/.test(q);
    if (pn && qn) {
      if (Number(p) !== Number(q)) return Number(p) < Number(q) ? -1 : 1;
    } else if (pn !== qn) {
      return pn ? -1 : 1;
    } else if (p !== q) {
      return p < q ? -1 : 1;
    }
  }
  return 0;
}

function isNewer(candidate, current) {
  try { return compareVersions(candidate, current) > 0; } catch (_) { return false; }
}

module.exports = {
  FORMAT, REPO_PATHS, FeedError, verifyFeed, signFeed, keyIdFor, publicKeys,
  isAllowedDownloadUrl, isAllowedRedirect, parseVersion, compareVersions, isNewer,
};
