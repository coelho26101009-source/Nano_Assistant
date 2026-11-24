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

test('a proactive notice becomes a notification with its own title', () => {
  const view = notifications.fromBackendEvent('nano_notice', { title: 'Resumo da manhã', text: 'Bom dia!\n• 09:00 Reunião' });
  assert.strictEqual(view.title, 'Resumo da manhã');
  assert.strictEqual(view.body, 'Bom dia! • 09:00 Reunião');
  assert.strictEqual(view.reminderId, null);
  assert.strictEqual(notifications.fromBackendEvent('nano_notice', { title: 'x' }), null);
  assert.strictEqual(notifications.fromBackendEvent('nano_notice', { text: 'olá' }).title, 'NANO');
});

test('a silent notice (quiet hours, a game, a suggestion) makes no Windows toast', () => {
  assert.strictEqual(notifications.fromBackendEvent('nano_notice', { title: 'Já é tarde', text: '01:10', silent: true }), null);
  assert.ok(notifications.fromBackendEvent('nano_notice', { title: 'x', text: 'y', silent: false }));
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
