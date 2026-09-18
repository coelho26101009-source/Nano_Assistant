/**
 * Containment for the static servers the render harnesses stand up.
 *
 * Six harnesses in this directory serve the built frontend over a throwaway
 * http server. Before lib/static-root.js existed they each mapped `req.url`
 * onto a file themselves, and the copies had drifted far enough apart that
 * CodeQL found fourteen js/path-injection paths through four of them: three
 * harnesses did no containment check at all, and a fourth ran fs.statSync on
 * the attacker-controlled path BEFORE the check it did have.
 *
 * WHAT THESE TESTS ARE AND ARE NOT. The first two groups call the real
 * resolver and assert on the paths it really returns; the third stands up a
 * real http server over a real temporary directory and asks for the traversal
 * targets over a real socket, then asserts the file outside the root never
 * came back. None of that is a search of the source text -- a resolver that
 * had been quietly reverted would fail here, and a grep for `startsWith`
 * would not.
 *
 * The one structural check is at the bottom, and it guards something the
 * behavioural tests cannot see: that no SEVENTH copy of this logic appears.
 * It reads the harness sources with comments stripped, because the comments
 * describe the very construct being forbidden and would otherwise match it.
 *
 * Everything here runs against a synthetic tree under the OS temp directory.
 * Nothing reads or writes the real frontend build, the real database, or any
 * real conversation.
 */
'use strict';

const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');

const { assert, suite, test } = require('./harness');
const { resolveStaticPath } = require('./lib/static-root');

/* ── A synthetic tree ───────────────────────────────────────────────────── */

/**
 * <tmp>/secret.txt          the file a traversal is trying to reach
 * <tmp>/root-old/leak.txt   a SIBLING whose name starts with the root's, so a
 *                           bare prefix test would accept it as "inside"
 * <tmp>/root/               the served root
 *   index.html
 *   nested/deep/asset.js
 *   _next/static/chunks/main.js
 *
 * realpathSync matters: the temp directory is reached through a short name on
 * Windows and through a symlink on macOS, and path.resolve does not follow
 * either -- so without it the containment comparison would be against a
 * different spelling of the same directory.
 */
const TMP = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'nano-static-')));
const ROOT = path.join(TMP, 'root');

fs.mkdirSync(path.join(ROOT, 'nested', 'deep'), { recursive: true });
fs.mkdirSync(path.join(ROOT, '_next', 'static', 'chunks'), { recursive: true });
fs.mkdirSync(path.join(TMP, 'root-old'), { recursive: true });

fs.writeFileSync(path.join(TMP, 'secret.txt'), 'TOP-SECRET-OUTSIDE-ROOT');
fs.writeFileSync(path.join(TMP, 'root-old', 'leak.txt'), 'TOP-SECRET-SIBLING');
fs.writeFileSync(path.join(ROOT, 'index.html'), '<!doctype html><title>root index</title>');
fs.writeFileSync(path.join(ROOT, 'nested', 'deep', 'asset.js'), 'export const ok = 1;');
fs.writeFileSync(path.join(ROOT, '_next', 'static', 'chunks', 'main.js'), 'console.log(1);');

process.on('exit', () => {
  try { fs.rmSync(TMP, { recursive: true, force: true }); } catch (err) { /* best effort */ }
});

/* ── The requests that must still work ──────────────────────────────────── */

suite('static root — valid requests still resolve');

/** Request path -> the path inside the root it must name, in POSIX spelling. */
const VALID = {
  '/': 'index.html',
  '': 'index.html',
  '/index.html': 'index.html',
  '/nested/deep/asset.js': 'nested/deep/asset.js',
  '/_next/static/chunks/main.js': '_next/static/chunks/main.js',
  '/index.html?v=2': 'index.html',
  '/index.html#top': 'index.html',
  '/nested/deep/asset.js?v=2#x': 'nested/deep/asset.js',
  '//index.html': 'index.html',
  '/%5Fnext/static/chunks/main.js': '_next/static/chunks/main.js',
};

for (const [url, expected] of Object.entries(VALID)) {
  test(`serves ${JSON.stringify(url)}`, () => {
    const resolved = resolveStaticPath(ROOT, url);
    assert.notStrictEqual(resolved, null, `${url} must not be refused`);
    assert.strictEqual(
      path.relative(ROOT, resolved).split(path.sep).join('/'), expected,
      `${url} resolved to the wrong file`,
    );
  });
}

test('a resolved asset is the real file on disk', () => {
  const resolved = resolveStaticPath(ROOT, '/nested/deep/asset.js');
  assert.strictEqual(fs.readFileSync(resolved, 'utf8'), 'export const ok = 1;');
});

/* ── The requests that must be refused ──────────────────────────────────── */

suite('static root — traversal is refused');

/** Every one of these must come back null. The label says what it is. */
const REFUSED = {
  'plain parent traversal': '/../secret.txt',
  'repeated parent traversal': '/../../../../../../etc/passwd',
  'traversal from a real subdirectory': '/nested/../../secret.txt',
  'percent-encoded dots': '/%2e%2e/secret.txt',
  'percent-encoded separator': '/..%2Fsecret.txt',
  'fully encoded traversal': '/%2e%2e%2fsecret.txt',
  'uppercase encoded traversal': '/%2E%2E%2Fsecret.txt',
  'mixed encoded and literal': '/%2e./secret.txt',
  'windows-style traversal': '/..\\secret.txt',
  'encoded windows separator': '/%2e%2e%5csecret.txt',
  'mixed separators': '/nested\\..\\..\\secret.txt',
  'sibling with the root as a name prefix': '/../root-old/leak.txt',
  'traversal hidden behind a query string': '/../secret.txt?v=1',
  'traversal hidden behind a fragment': '/../secret.txt#x',
  'malformed encoding': '/%ZZ',
  'truncated escape sequence': '/index.html%',
  'incomplete multibyte escape': '/%E0%A4%A',
  'embedded NUL': '/index.html%00.png',
  'NUL inside a traversal': '/..%00/secret.txt',
};

for (const [label, url] of Object.entries(REFUSED)) {
  test(`refuses ${label} — ${JSON.stringify(url)}`, () => {
    assert.strictEqual(
      resolveStaticPath(ROOT, url), null,
      `${url} was resolved instead of refused`,
    );
  });
}

test('an absolute path cannot replace the root', () => {
  /* path.resolve honours an absolute second argument, so a request that looks
     absolute must never reach it unstripped. On POSIX these land inside the
     root and 404; on Windows the drive letter escapes and is refused. Either
     way the one thing that must never happen is reading the named file. */
  for (const url of ['/etc/passwd', '//etc/passwd', '/C:/Windows/win.ini', '/%2Fetc%2Fpasswd']) {
    const resolved = resolveStaticPath(ROOT, url);
    if (resolved === null) continue;
    const relative = path.relative(ROOT, resolved);
    assert.ok(
      relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative),
      `${url} escaped the root, resolving to ${resolved}`,
    );
  }
});

/* ── The property that has to hold for anything, not just the list above ── */

suite('static root — containment as a property');

test('nothing the resolver returns is ever outside the root', () => {
  const inputs = [
    ...Object.keys(VALID), ...Object.values(REFUSED),
    '/.', '/..', '..', '../', './/../', '/./././../secret.txt',
    '/nested/./deep/../deep/asset.js', '/%2e/%2e%2e/secret.txt',
    '/....//secret.txt', '/..;/secret.txt', '/\\\\server\\share\\x',
    '/' + 'a/'.repeat(200) + '../secret.txt', null, undefined, 42,
  ];
  for (const url of inputs) {
    const resolved = resolveStaticPath(ROOT, url);
    if (resolved === null) continue;
    assert.ok(path.isAbsolute(resolved), `${url} produced a relative path`);
    const relative = path.relative(ROOT, resolved);
    assert.ok(
      relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative),
      `${url} escaped the root, resolving to ${resolved}`,
    );
  }
});

test('a non-string request URL is treated as the root index, not a crash', () => {
  for (const url of [null, undefined, 42, {}, []]) {
    assert.strictEqual(
      path.relative(ROOT, resolveStaticPath(ROOT, url)), 'index.html',
      `${String(url)} should fall back to the index`,
    );
  }
});

/* ── The same thing, over a real socket ─────────────────────────────────── */

suite('static root — over a real http server');

/** The server the harnesses build, reduced to the part under test. */
function serve() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const file = resolveStaticPath(ROOT, req.url);
      if (file === null || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        res.writeHead(404); res.end('not found'); return;
      }
      res.writeHead(200); res.end(fs.readFileSync(file));
    });
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

/** Ask for a path EXACTLY as written -- node's client does not normalise it. */
function get(port, requestPath) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path: requestPath }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body }));
    });
    req.on('error', reject);
  });
}

test('the server is reachable only on the loopback address', async () => {
  const server = await serve();
  try {
    assert.strictEqual(server.address().address, '127.0.0.1',
      'the harness server must never be reachable off this machine');
  } finally { server.close(); }
});

test('real requests serve the root and never the file outside it', async () => {
  const server = await serve();
  const { port } = server.address();
  try {
    const index = await get(port, '/');
    assert.strictEqual(index.status, 200);
    assert.ok(index.body.includes('root index'), 'the root index should still be served');

    const asset = await get(port, '/nested/deep/asset.js');
    assert.strictEqual(asset.status, 200, 'a nested asset should still be served');

    for (const url of Object.values(REFUSED)) {
      const res = await get(port, url);
      assert.strictEqual(res.status, 404, `${url} was not refused over http`);
      assert.ok(!res.body.includes('TOP-SECRET'),
        `${url} leaked a file from outside the served root`);
    }
  } finally { server.close(); }
});

/* ── No seventh copy ────────────────────────────────────────────────────── */

suite('static root — every harness server uses it');

/** Strip comments so the prose ABOUT the forbidden construct cannot match it. */
function withoutComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/^\s*\/\/.*$/gm, ' ');
}

test('no static server in this directory maps a URL to a path on its own', () => {
  const offenders = [];
  for (const name of fs.readdirSync(__dirname)) {
    if (!name.endsWith('.js') || name === path.basename(__filename)) continue;
    const code = withoutComments(fs.readFileSync(path.join(__dirname, name), 'utf8'));
    if (!code.includes('http.createServer')) continue;
    // Readiness unit tests only return literal HTTP responses (or deliberately
    // hang); there is no filesystem there and no URL-to-path mapping to guard.
    if (name === 'startup-health.test.js') {
      assert.ok(!/\bfs\b|readFile|createReadStream/.test(code),
        'the readiness test exemption must never gain filesystem access');
      continue;
    }
    if (code.includes('decodeURIComponent')) {
      offenders.push(`${name} decodes a request URL itself`);
    }
    if (!code.includes("require('./lib/static-root')")) {
      offenders.push(`${name} serves files without the shared resolver`);
    }
  }
  assert.deepStrictEqual(offenders, [],
    'a static server here must resolve request paths through lib/static-root.js');
});
