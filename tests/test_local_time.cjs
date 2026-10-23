'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/server/static/local-time.js'), 'utf8');
const originalIso = '2026-10-22T17:42:38+00:00';

function browser(zone, language = 'zh-CN') {
  // Supply the browser's default zone without changing this test process's TZ.
  function DateTimeFormat(locale, options = {}) {
    return new Intl.DateTimeFormat(locale, {timeZone: zone, ...options});
  }
  const time = {dateTime: originalIso, textContent: 'Enable JavaScript'};
  const label = {};
  const input = {value: ''};
  const document = {
    documentElement: {lang: language},
    body: {dataset: {timezoneDescription: 'Browser local time', timezoneUnavailable: 'Unavailable'}},
    querySelectorAll(selector) {
      if (selector === 'time[datetime]') return [time];
      if (selector === '[data-timezone-label]') return [label];
      if (selector === 'input[name="timezone"]') return [input];
      throw new Error('Unexpected DOM query: ' + selector);
    },
  };
  const context = {Intl: {DateTimeFormat}, Date, document, window: {}};
  vm.runInNewContext(source, context);
  return {api: context.window.EpaperTime, time, label, input};
}

test('New York uses local time, preserves the ISO instant and fills the deletion zone', () => {
  const {api, time, label, input} = browser('America/New_York');
  assert.equal(api.zone, 'America/New_York');
  assert.equal(time.textContent, '2026/10/22 13:42:38');
  assert.equal(time.dateTime, originalIso);
  assert.match(time.title, /America\/New_York.*GMT-4/);
  assert.match(label.textContent, /^America\/New York · GMT-/);
  assert.equal(input.value, 'America/New_York');
  assert.ok(Object.isFrozen(api));
});

test('Shanghai rolls over to the next local day', () => {
  const {api, time, input} = browser('Asia/Shanghai');
  assert.equal(time.textContent, '2026/10/23 01:42:38');
  assert.equal(api.offset(originalIso), 'GMT+8');
  assert.equal(input.value, 'Asia/Shanghai');
});

test('fractional-hour zones retain their 45-minute offset', () => {
  const {api, time, input} = browser('Asia/Katmandu');
  assert.equal(time.textContent, '2026/10/22 23:27:38');
  assert.equal(api.offset(originalIso), 'GMT+5:45');
  assert.equal(input.value, api.zone);
});

test('the repeated autumn hour keeps the correct offset for each instant', () => {
  const {api} = browser('America/New_York');
  const early = '2026-11-01T05:30:00Z';
  const late = '2026-11-01T06:30:00Z';
  assert.equal(api.format(early), '2026/11/01 01:30:00');
  assert.equal(api.format(late), '2026/11/01 01:30:00');
  assert.equal(api.offset(early), 'GMT-4');
  assert.equal(api.offset(late), 'GMT-5');
});

test('the spring gap skips the nonexistent hour and chart axes use the local date', () => {
  const {api} = browser('America/New_York');
  assert.equal(api.format('2026-03-08T06:59:59Z'), '2026/03/08 01:59:59');
  assert.equal(api.format('2026-03-08T07:00:00Z'), '2026/03/08 03:00:00');
  assert.match(api.axis(Date.parse('2026-10-23T02:00:00Z'), 86400000), /10\/22.*22:00/);
});

test('English uses a 24-hour local clock and invalid values stay empty', () => {
  const {api, time} = browser('America/New_York', 'en');
  assert.match(api.format('2026-10-22T04:00:00Z'), /00:00:00/);
  assert.equal(api.format('not-a-date'), '—');
  assert.equal(api.axis(NaN, 86400000), '—');
  time.dateTime = 'not-a-date';
  api.refresh();
  assert.equal(time.textContent, '—');
  assert.equal(time.dateTime, 'not-a-date');
});
