/**
 * ONE Nano data directory, agreed by both halves of the application.
 *
 * This used to be two. Electron pointed NANO_DATA_DIR at its own
 * `app.getPath('userData')/data` under %APPDATA%, while core/app_paths.py
 * defaults to %LOCALAPPDATA%\NanoAssistant. Whichever way Nano was started
 * decided where the encrypted Groq key, the settings, the permission policies,
 * the task queue and the conversation database lived -- so launching the
 * desktop app after using NANO.bat presented an empty profile and looked like a
 * fresh install.
 *
 * The rule now: Python owns the definition, Electron mirrors it exactly and
 * passes it back down explicitly. `canonicalDataDir()` below is a
 * transliteration of `core.app_paths.data_root()`, and a test asserts the two
 * produce the same string on this machine rather than trusting this comment.
 *
 * Recovery of data left in the old locations is core/data_migration.py's job:
 * it copies in only when the destination is empty, never overwrites and never
 * deletes the source.
 */
'use strict';

const path = require('path');
const os = require('os');

/**
 * The canonical Nano data directory. Mirrors core.app_paths.data_root().
 *
 * @param {object} env - process.env, injectable for tests.
 */
function canonicalDataDir(env = process.env) {
  const configured = env.NANO_DATA_DIR || env.HELIOS_DATA_DIR;
  if (configured) return path.resolve(configured);
  if (process.platform === 'win32') {
    const local = env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
    return path.join(local, 'NanoAssistant');
  }
  const xdg = env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share');
  return path.join(xdg, 'NanoAssistant');
}

/** Desktop-shell state (window bounds, overlay preference). Never user data. */
function shellStateFile(userDataDir) {
  return path.join(userDataDir, 'desktop-state.json');
}

module.exports = { canonicalDataDir, shellStateFile };
