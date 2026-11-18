/**
 * Reminder notifications: the backend event becomes a bounded, plain-text toast.
 */
'use strict';

const { assert, suite, test } = require('./harness');
const notifications = require('../lib/notifications');

suite('reminder notifications');

test('a due reminder becomes a notification with its text', () => {
  const view = notifications.fromBackendEvent('reminder_due', { id: 7, text: 'ligar à avó', due_label: '18:30' });
  assert.strictEqual(view.title, 'Lembrete · 18:30');
  assert.strictEqual(view.body, 'ligar à avó');
  assert.strictEqual(view.reminderId, 7);
});

test('other backend events never produce a notification', () => {
  assert.strictEqual(notifications.fromBackendEvent('voice_phase', { text: 'x' }), null);
  assert.strictEqual(notifications.fromBackendEvent('something_new', { text: 'x' }), null);
});

test('an empty or malformed reminder shows nothing', () => {
  assert.strictEqual(notifications.fromBackendEvent('reminder_due', {}), null);
  assert.strictEqual(notifications.fromBackendEvent('reminder_due', null), null);
  assert.strictEqual(notifications.fromBackendEvent('reminder_due', { text: 42 }), null);
  assert.strictEqual(notifications.fromBackendEvent('reminder_due', { text: '\u0000\n\t' }), null);
});

test('control characters are stripped and long text is bounded', () => {
  const view = notifications.fromBackendEvent('reminder_due', { text: 'a\u0007b\r\nc' + 'x'.repeat(1000) });
  assert.ok(!/[\u0000-\u001f]/.test(view.body));
  assert.ok(view.body.startsWith('a b c'));
  assert.ok(view.body.length <= notifications.MAX_BODY);
  assert.ok(view.body.endsWith('…'));
});

test('a non-integer id is not passed through', () => {
  assert.strictEqual(notifications.fromBackendEvent('reminder_due', { id: '7; rm', text: 'x' }).reminderId, null);
});
