/**
 * The shell reads `.env` the way python-dotenv does (lib/env-file.js).
 */
'use strict';

const { assert, suite, test } = require('./harness');
const { parseEnvText } = require('../lib/env-file');

suite('.env file');

test('quotes are removed, not passed on as part of the value', () => {
  const env = parseEnvText('GROQ_API_KEY="gsk_abc"\nGEMINI_API_KEY=\'AIza xyz\'\n');
  assert.strictEqual(env.GROQ_API_KEY, 'gsk_abc');
  assert.strictEqual(env.GEMINI_API_KEY, 'AIza xyz');
});

test('an inline comment is not part of an unquoted value', () => {
  const env = parseEnvText('NANO_PORT=8765  # dev only\nURL=http://x/#anchor\n');
  assert.strictEqual(env.NANO_PORT, '8765');
  assert.strictEqual(env.URL, 'http://x/#anchor');
});

test('a # inside quotes stays, and double-quoted escapes are read', () => {
  const env = parseEnvText('A="pass#word" # note\nB="line1\\nline2 \\"q\\""\n');
  assert.strictEqual(env.A, 'pass#word');
  assert.strictEqual(env.B, 'line1\nline2 "q"');
});

test('export prefixes, blank lines, comments and junk are handled', () => {
  const env = parseEnvText('# comment\n\nexport NANO_MODE=desktop\n=nokey\nnot a line\n1BAD=x\nEMPTY=\n');
  assert.deepStrictEqual(env, { NANO_MODE: 'desktop', EMPTY: '' });
});
