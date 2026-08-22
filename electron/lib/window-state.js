/**
 * Remembering the Nano window's size and position -- safely.
 *
 * Restoring saved bounds blindly is a classic way to lose an application: the
 * user detaches the laptop from a second monitor, Nano reopens at x=2400, and
 * the window is on a display that no longer exists. Everything here exists to
 * make that impossible, so `sanitizeBounds` is the only way bounds are applied.
 *
 * Pure and Electron-free: displays come in as plain work-area rectangles, so
 * the rules can be tested without a screen.
 */
'use strict';

const MIN_WIDTH = 940;
const MIN_HEIGHT = 620;

/** Enough of the title bar must be reachable to drag the window back. */
const MIN_VISIBLE_WIDTH = 180;
const MIN_VISIBLE_HEIGHT = 40;

const DEFAULT_BOUNDS = Object.freeze({ width: 1320, height: 860 });

function isFiniteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function intersects(bounds, area) {
  const overlapX = Math.min(bounds.x + bounds.width, area.x + area.width) - Math.max(bounds.x, area.x);
  const overlapY = Math.min(bounds.y + bounds.height, area.y + area.height) - Math.max(bounds.y, area.y);
  return overlapX >= MIN_VISIBLE_WIDTH && overlapY >= MIN_VISIBLE_HEIGHT;
}

/**
 * Turn a remembered record into bounds that are safe to open.
 *
 * Returns `{ width, height, x?, y?, maximized }`. A position is only ever
 * returned when it lands on a display that exists right now; otherwise it is
 * dropped and the window is centred, which is always reachable.
 */
function sanitizeBounds(saved, workAreas, defaults = DEFAULT_BOUNDS) {
  const areas = Array.isArray(workAreas) ? workAreas.filter(
    (a) => a && isFiniteNumber(a.x) && isFiniteNumber(a.y) && a.width > 0 && a.height > 0) : [];

  const result = {
    width: defaults.width,
    height: defaults.height,
    maximized: Boolean(saved && saved.maximized),
  };
  if (!saved || typeof saved !== 'object') return result;

  // Never larger than the largest display can show, never below the minimum
  // the layout needs.
  const widest = areas.reduce((max, a) => Math.max(max, a.width), 0) || defaults.width;
  const tallest = areas.reduce((max, a) => Math.max(max, a.height), 0) || defaults.height;

  if (isFiniteNumber(saved.width)) {
    result.width = Math.max(MIN_WIDTH, Math.min(Math.round(saved.width), widest));
  }
  if (isFiniteNumber(saved.height)) {
    result.height = Math.max(MIN_HEIGHT, Math.min(Math.round(saved.height), tallest));
  }

  if (isFiniteNumber(saved.x) && isFiniteNumber(saved.y) && areas.length) {
    const candidate = {
      x: Math.round(saved.x), y: Math.round(saved.y),
      width: result.width, height: result.height,
    };
    if (areas.some((area) => intersects(candidate, area))) {
      result.x = candidate.x;
      result.y = candidate.y;
    }
  }
  return result;
}

/** What to persist. Only ever the *restored* geometry, never the maximised box. */
function captureBounds(window) {
  if (!window || window.isDestroyed()) return null;
  const maximized = window.isMaximized();
  // getNormalBounds() is the un-maximised geometry, which is what should come
  // back when the user later un-maximises. getBounds() while maximised would
  // permanently remember a full-screen rectangle.
  const bounds = maximized ? window.getNormalBounds() : window.getBounds();
  return {
    x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height,
    maximized,
  };
}

/**
 * Where the voice overlay goes: bottom-centre of the *work area*, so it sits
 * above the Windows taskbar instead of behind it, on the display that has the
 * cursor -- which on a multi-monitor desk is the screen the user is looking at.
 */
function overlayPosition(workArea, size, marginBottom = 72) {
  const area = workArea || { x: 0, y: 0, width: size.width, height: size.height };
  return {
    x: Math.round(area.x + (area.width - size.width) / 2),
    y: Math.round(area.y + area.height - size.height - marginBottom),
  };
}

module.exports = {
  DEFAULT_BOUNDS, MIN_HEIGHT, MIN_VISIBLE_HEIGHT, MIN_VISIBLE_WIDTH, MIN_WIDTH,
  captureBounds, overlayPosition, sanitizeBounds,
};
