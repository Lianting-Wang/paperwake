/* All management timestamps use the browser's IANA time zone. Stored ISO values stay intact. */
(() => {
  'use strict';
  const locale = document.documentElement.lang === 'en' ? 'en-US' : 'zh-CN';
  const settings = document.body.dataset;
  let zone = null;
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch (_) { /* Keep the explicit unavailable state. */ }
  const validDate = value => {
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date : null;
  };
  const formatter = options => new Intl.DateTimeFormat(locale, {...options, ...(zone ? {timeZone: zone} : {})});
  const offset = value => {
    const date = validDate(value === undefined ? Date.now() : value);
    if (!date) return '';
    try { return formatter({timeZoneName: 'shortOffset'}).formatToParts(date).find(part => part.type === 'timeZoneName')?.value || ''; }
    catch (_) { return ''; }
  };
  const format = value => {
    const date = validDate(value);
    if (!date) return '—';
    return formatter({year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'}).format(date);
  };
  const axis = (ms, span, compact = false) => {
    const date = validDate(ms);
    if (!date) return '—';
    return formatter(compact && span < 172800000 ? {hour: '2-digit', minute: '2-digit', hourCycle: 'h23'} : {
      month: '2-digit', ...(compact && span > 30000000000 ? {} : {day: '2-digit'}),
      ...(span > 30000000000 ? {year: 'numeric'} : {}),
      ...(!compact && span < 172800000 ? {hour: '2-digit', minute: '2-digit', hourCycle: 'h23'} : {}),
    }).format(date);
  };
  const refresh = (scope = document) => {
    scope.querySelectorAll('time[datetime]').forEach(element => {
      element.textContent = format(element.dateTime);
      element.title = [format(element.dateTime), zone, offset(element.dateTime)].filter(Boolean).join(' · ');
    });
    scope.querySelectorAll('[data-timezone-label]').forEach(element => {
      element.textContent = zone ? [zone.replaceAll('_', ' '), offset()].filter(Boolean).join(' · ') : settings.timezoneUnavailable;
      element.title = settings.timezoneDescription || '';
    });
    scope.querySelectorAll('input[name="timezone"]').forEach(element => { element.value = zone || ''; });
  };
  window.EpaperTime = Object.freeze({zone, format, axis, offset, refresh});
  refresh();
})();
