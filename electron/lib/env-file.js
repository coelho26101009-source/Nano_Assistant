/**
 * Read a `.env` file the way the Python side (python-dotenv) reads it.
 *
 * The shell used to keep everything after `=` verbatim, so
 * `GROQ_API_KEY="gsk_..."` reached the engine WITH the quotes, and
 * `NANO_PORT=8765  # dev` with the comment: a key that looked right in the
 * file and failed at every request. Supported, as in python-dotenv:
 *
 *   KEY=value              plain, trailing `# comment` removed
 *   KEY="value"            double quotes; \n \t \" \\ are unescaped
 *   KEY='value'            single quotes; taken literally
 *   export KEY=value       the `export ` prefix is ignored
 *
 * Keys must look like environment variable names; anything else is skipped.
 */
'use strict';

const KEY_RE = /^[A-Za-z_][A-Za-z0-9_.]*$/;

function unescapeDouble(value) {
  return value.replace(/\\([\\nrt"'])/g, (_, ch) => (
    { n: '\n', r: '\r', t: '\t', '\\': '\\', '"': '"', "'": "'" }[ch]));
}

/** One value, after the `=`. */
function parseValue(raw) {
  const value = raw.trim();
  const quote = value[0];
  if (quote === '"' || quote === "'") {
    // The closing quote is the first one not escaped by a backslash.
    let end = -1;
    for (let i = 1; i < value.length; i += 1) {
      if (value[i] === '\\' && quote === '"') { i += 1; continue; }
      if (value[i] === quote) { end = i; break; }
    }
    if (end > 0) {
      const inner = value.slice(1, end);
      return quote === '"' ? unescapeDouble(inner) : inner;
    }
  }
  // Unquoted: an inline comment starts at whitespace followed by `#`.
  const comment = value.search(/\s#/);
  return (comment >= 0 ? value.slice(0, comment) : value).trim();
}

/** `{ KEY: value }` for every assignment in `text`. */
function parseEnvText(text) {
  const out = {};
  for (const line of String(text || '').split(/\r?\n/)) {
    let trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    if (trimmed.startsWith('export ')) trimmed = trimmed.slice(7).trim();
    const index = trimmed.indexOf('=');
    if (index <= 0) continue;
    const key = trimmed.slice(0, index).trim();
    if (!KEY_RE.test(key)) continue;
    out[key] = parseValue(trimmed.slice(index + 1));
  }
  return out;
}

module.exports = { parseEnvText, parseValue };
