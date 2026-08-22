#!/usr/bin/env node
/**
 * The Electron shell's test suite.
 *
 *     cd electron && npm test
 *
 * pytest also runs this (tests/test_desktop_shell.py) so the whole project is
 * still verified by one command. Exits non-zero on any failure.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { run } = require('./harness');

const here = __dirname;
const files = fs.readdirSync(here)
  .filter((name) => name.endsWith('.test.js'))
  .sort();

if (!files.length) {
  console.error('No test files found in electron/test.');
  process.exit(1);
}

for (const file of files) require(path.join(here, file));

run().then(() => {
  if (process.exitCode) console.error('\nElectron shell tests FAILED.');
});
