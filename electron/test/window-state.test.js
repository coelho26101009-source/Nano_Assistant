/** Remembered window geometry must never put Nano somewhere unreachable. */
'use strict';

const { assert, suite, test } = require('./harness');
const ws = require('../lib/window-state');

const LAPTOP = { x: 0, y: 0, width: 1920, height: 1040 };
const SECOND = { x: 1920, y: 0, width: 1920, height: 1040 };

suite('window bounds');

test('no saved state gives the default size and no position', () => {
  const bounds = ws.sanitizeBounds(null, [LAPTOP]);
  assert.strictEqual(bounds.width, ws.DEFAULT_BOUNDS.width);
  assert.strictEqual(bounds.height, ws.DEFAULT_BOUNDS.height);
  assert.strictEqual(bounds.x, undefined, 'with no position the window is centred');
});

test('a position on a display that still exists is restored', () => {
  const bounds = ws.sanitizeBounds({ x: 2100, y: 120, width: 1400, height: 900 }, [LAPTOP, SECOND]);
  assert.strictEqual(bounds.x, 2100);
  assert.strictEqual(bounds.y, 120);
});

test('a position on a display that is gone is dropped', () => {
  // The whole reason this module exists: unplug the second monitor and the
  // remembered x=2100 would open Nano where nobody can see or reach it.
  const bounds = ws.sanitizeBounds({ x: 2100, y: 120, width: 1400, height: 900 }, [LAPTOP]);
  assert.strictEqual(bounds.x, undefined);
  assert.strictEqual(bounds.y, undefined);
  assert.strictEqual(bounds.width, 1400, 'the size is still worth keeping');
});

test('a window barely peeking onto a display is treated as offscreen', () => {
  const bounds = ws.sanitizeBounds({ x: 1900, y: 1030, width: 1200, height: 800 }, [LAPTOP]);
  assert.strictEqual(bounds.x, undefined,
    'a few visible pixels are not enough to grab the title bar');
});

test('a size below the layout minimum is raised, not honoured', () => {
  const bounds = ws.sanitizeBounds({ x: 10, y: 10, width: 320, height: 200 }, [LAPTOP]);
  assert.strictEqual(bounds.width, ws.MIN_WIDTH);
  assert.strictEqual(bounds.height, ws.MIN_HEIGHT);
});

test('a size larger than any display is clamped', () => {
  const bounds = ws.sanitizeBounds({ width: 9000, height: 9000 }, [LAPTOP]);
  assert.strictEqual(bounds.width, LAPTOP.width);
  assert.strictEqual(bounds.height, LAPTOP.height);
});

test('nonsense values cannot reach a BrowserWindow', () => {
  const bounds = ws.sanitizeBounds(
    { x: NaN, y: 'left', width: Infinity, height: null }, [LAPTOP]);
  assert.ok(Number.isFinite(bounds.width) && bounds.width >= ws.MIN_WIDTH);
  assert.ok(Number.isFinite(bounds.height) && bounds.height >= ws.MIN_HEIGHT);
  assert.strictEqual(bounds.x, undefined);
});

test('the maximised flag is carried through', () => {
  assert.strictEqual(ws.sanitizeBounds({ maximized: true }, [LAPTOP]).maximized, true);
  assert.strictEqual(ws.sanitizeBounds({ maximized: false }, [LAPTOP]).maximized, false);
});

suite('overlay position');

test('the overlay sits bottom-centre inside the work area', () => {
  const size = { width: 320, height: 96 };
  const position = ws.overlayPosition(LAPTOP, size, 72);
  assert.strictEqual(position.x, (1920 - 320) / 2);
  assert.strictEqual(position.y, 1040 - 96 - 72);
});

test('the overlay respects the work area, not the raw display', () => {
  // workArea already excludes the Windows taskbar; using display bounds here
  // would put the panel behind it.
  const area = { x: 0, y: 0, width: 1920, height: 1040 };
  const full = { x: 0, y: 0, width: 1920, height: 1080 };
  assert.notStrictEqual(
    ws.overlayPosition(area, { width: 320, height: 96 }).y,
    ws.overlayPosition(full, { width: 320, height: 96 }).y,
  );
});

test('the overlay lands on a secondary monitor when that is the active one', () => {
  const position = ws.overlayPosition(SECOND, { width: 320, height: 96 }, 72);
  assert.strictEqual(position.x, 1920 + (1920 - 320) / 2);
});
