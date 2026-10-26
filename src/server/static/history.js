/* Local, dependency-free history charts. No device payload is interpreted as HTML. */
(() => {
  'use strict';
  const root = document.getElementById('history-insights');
  if (!root) return;
  const labels = JSON.parse(root.dataset.labels);
  const locale = root.dataset.language === 'en' ? 'en-US' : 'zh-CN';
  const svg = document.getElementById('history-chart');
  const activity = document.getElementById('history-activity');
  const metricKeys = ['battery_percent', 'battery_voltage', 'temperature_c', 'wifi_rssi', 'http_latency_ms', 'elapsed_seconds'];
  const units = {battery_percent: '%', battery_voltage: 'V', temperature_c: '°C', wifi_rssi: 'dBm', http_latency_ms: 'ms', elapsed_seconds: 's'};
  let metric = 'battery_percent';
  let range = '30';
  let data = null;
  let selected = -1;
  let points = [];
  let chartX = null;
  let controller = null;
  let focusGroup = null;
  let chartWidth = 1040;
  let plotBounds = {top: 16, bottom: 258};
  const compact = () => window.matchMedia('(max-width: 760px)').matches;
  const valid = value => typeof value === 'number' && Number.isFinite(value);
  const fmt = (value, digits = 1) => valid(value) ? value.toLocaleString(locale, {maximumFractionDigits: digits}) : '—';
  const fullTime = value => {
    const formatted = window.EpaperTime.format(value);
    return formatted === '—' ? formatted : formatted + ' ' + window.EpaperTime.offset(value);
  };
  const set = (name, value) => {
    const node = root.querySelector('[data-value="' + name + '"]');
    if (node) node.textContent = value;
  };
  const measurement = (name, value, unit) => {
    const element = root.querySelector('[data-value="' + name + '"]');
    element.textContent = value;
    if (value !== '—') {
      const small = document.createElement('small'); small.textContent = unit; element.append(small);
    }
  };
  const clear = () => {
    data = null; points = []; selected = -1; chartX = null; focusGroup = null;
    svg.replaceChildren(); activity.replaceChildren();
    for (const name of ['battery', 'voltage', 'change', 'change-span', 'duration', 'cycles', 'failures', 'reading', 'reading-time', 'coverage', 'report-count', 'sampling']) set(name, '—');
    root.querySelector('[data-value="samples-table"]').replaceChildren();
  };
  const node = (tag, attrs = {}, text = null) => {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [name, value] of Object.entries(attrs)) element.setAttribute(name, String(value));
    if (text !== null) element.textContent = text;
    return element;
  };
  const axisTime = (ms, span, short = false) => window.EpaperTime.axis(ms, span, short);
  const extent = series => {
    const stamps = series.map(point => Date.parse(point.at)).filter(Number.isFinite);
    if (!stamps.length) return [Date.now() - 86400000, Date.now()];
    const first = Math.min(...stamps), last = Math.max(...stamps);
    const pad = Math.max((last - first) * .025, last === first ? 1800000 : 1000);
    return [first - pad, last + pad];
  };
  function summary() {
    const s = data.summary || {};
    measurement('battery', valid(s.latest_battery_percent) ? fmt(s.latest_battery_percent) : '—', '%');
    set('voltage', valid(s.latest_voltage) ? labels.battery_voltage + ' ' + fmt(s.latest_voltage, 3) + ' V' : '—');
    measurement('change', valid(s.battery_change_pp) ? (s.battery_change_pp > 0 ? '+' : '') + fmt(s.battery_change_pp) : '—', labels.points);
    set('change-span', valid(s.battery_change_hours) && valid(s.battery_change_pp) ? fmt(s.battery_change_hours) + ' ' + labels.hours : labels.noComparison);
    measurement('duration', valid(s.median_cycle_seconds) ? fmt(s.median_cycle_seconds) : '—', 's');
    set('cycles', fmt(s.cycles || 0, 0));
    set('failures', s.failure_cycles ? fmt(s.failure_cycles, 0) + ' ' + labels.failureDescription : labels.noFailures);
    const c = data.coverage || {};
    set('coverage', c.start && c.end ? labels.coverage + ' · ' + fullTime(c.start) + ' → ' + fullTime(c.end) : labels.noCoverage);
    set('report-count', fmt(s.reports || 0, 0) + ' ' + labels.reports);
    const seconds = data.bucket_seconds;
    const interval = seconds >= 86400 ? fmt(seconds / 86400) + ' ' + labels.days : seconds >= 3600 ? fmt(seconds / 3600) + ' ' + labels.hours : seconds >= 60 ? fmt(seconds / 60) + ' ' + labels.minutes : fmt(seconds) + ' ' + labels.seconds;
    set('sampling', seconds ? labels.bucketInterval + ' ' + interval + ' · ' + labels.bucket : labels.exact);
  }
  function estimate() {
    const value = data.estimate || {};
    const panel = root.querySelector('[data-estimate-panel]');
    if (!panel) return;
    const text = (key, content) => { panel.querySelector('[data-estimate="' + key + '"]').textContent = content; };
    const interpolate = (message, values) => message.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? '—'));
    text('daily', fmt(value.daily_consumption_pp));
    text('remaining', fmt(value.remaining_days));
    const timestamp = (name, iso) => {
      const element = panel.querySelector('[data-estimate="' + name + '"]');
      if (iso && Number.isFinite(Date.parse(iso))) {
        const time = document.createElement('time');
        time.dateTime = iso;
        element.replaceChildren(time);
        window.EpaperTime.refresh(element);
      } else element.textContent = '—';
    };
    timestamp('until', value.estimated_until);
    timestamp('latest', value.latest_at);
    const statuses = {ready: labels.estimateReady, insufficient: labels.estimateInsufficient,
      flat: labels.estimateFlat, charging: labels.estimateCharging, stale: labels.estimateStale, gap: labels.estimateGap};
    text('status', value.status === 'ready' && valid(value.remaining_days) && value.remaining_days <= 0 ? labels.estimateAtReserve : statuses[value.status] || labels.estimateInsufficient);
    let basis = value.sample_count && valid(value.span_hours) ? interpolate(labels.estimateBasis,
      {window: value.window_days || 7, count: fmt(value.sample_count, 0), hours: fmt(value.span_hours)}) : labels.estimateWindow;
    if (valid(value.observed_drop_pp) && value.observed_drop_pp > 0) basis += ' ' + interpolate(labels.estimateDrop, {drop: fmt(value.observed_drop_pp)});
    text('basis', basis);
    text('voltage', value.voltage_sample_count >= 2 && valid(value.first_voltage) && valid(value.latest_voltage)
      ? interpolate(labels.estimateVoltageBasis, {first: fmt(value.first_voltage, 3),
        last: fmt(value.latest_voltage, 3), count: fmt(value.voltage_sample_count, 0)}) : '');
  }
  function grid(low, high, start, end) {
    const mobile = compact();
    chartWidth = mobile ? Math.max(240, Math.round(svg.clientWidth)) : 1040;
    const height = mobile ? 250 : 300;
    svg.setAttribute('viewBox', '0 0 ' + chartWidth + ' ' + height);
    const left = mobile ? 41 : 56, right = chartWidth - (mobile ? 8 : 16), top = 16, bottom = height - 42;
    plotBounds = {top, bottom};
    const x = ms => left + (ms - start) / (end - start) * (right - left);
    const y = value => bottom - (value - low) / (high - low) * (bottom - top);
    for (let i = 0; i < 5; i++) {
      const value = low + (high - low) * i / 4;
      const py = y(value);
      svg.append(node('line', {x1: left, x2: right, y1: py, y2: py, class: 'chart-grid'}));
      svg.append(node('text', {x: left - 12, y: py + 3, 'text-anchor': 'end', class: 'chart-axis'}, fmt(value, metric === 'battery_voltage' ? 2 : 1)));
    }
    const steps = mobile ? 2 : 4;
    for (let i = 0; i <= steps; i++) {
      const ms = start + (end - start) * i / steps;
      svg.append(node('text', {x: x(ms), y: height - 14, 'text-anchor': i === 0 ? 'start' : i === steps ? 'end' : 'middle', class: 'chart-axis'}, axisTime(ms, end - start, mobile)));
    }
    svg.append(node('text', {x: left - 12, y: 5, 'text-anchor': 'end', class: 'chart-axis-unit'}, units[metric]));
    return {x, y, top, bottom};
  }
  function reading(index) {
    if (!data || !data.series.length) return;
    selected = Math.max(0, Math.min(data.series.length - 1, index));
    const point = data.series[selected];
    set('reading', valid(point[metric]) ? fmt(point[metric], metric === 'battery_voltage' ? 3 : 1) + ' ' + units[metric] : '—');
    set('reading-time', fullTime(point.at) + (point.cycles > 1 ? ' · ' + fmt(point.cycles, 0) + ' ' + labels.cycleUnit : ''));
    if (focusGroup) focusGroup.remove();
    focusGroup = node('g', {'aria-hidden': 'true'});
    const plotted = points[selected];
    if (plotted) {
      focusGroup.append(node('line', {x1: plotted.x, x2: plotted.x, y1: plotBounds.top, y2: plotBounds.bottom, class: 'chart-cursor'}));
      if (valid(plotted.y)) focusGroup.append(node('circle', {cx: plotted.x, cy: plotted.y, r: 5, class: 'chart-focus'}));
      svg.append(focusGroup);
    }
    svg.setAttribute('aria-label', labels[metric] + ' · ' + fullTime(point.at) + ' · ' + (valid(point[metric]) ? fmt(point[metric]) + ' ' + units[metric] : labels.missing));
  }
  function chart() {
    svg.replaceChildren();
    focusGroup = null;
    set('metric-label', labels[metric]);
    points = [];
    if (!data) return;
    const series = data.series;
    const values = series.map(point => point[metric]).filter(valid);
    const [start, end] = extent(series);
    let low = values.length ? Math.min(...values) : 0;
    let high = values.length ? Math.max(...values) : 100;
    if (metric === 'battery_percent') {low = 0; high = 100;}
    else {
      const pad = Math.max((high - low) * .17, Math.abs(high) * .025, metric === 'battery_voltage' ? .04 : 1);
      low -= pad; high += pad;
      if (metric === 'http_latency_ms' || metric === 'elapsed_seconds' || metric === 'battery_voltage') low = Math.max(0, low);
    }
    const axes = grid(low, high, start, end);
    chartX = axes.x;
    set('chart-status', !series.length ? labels.empty : !values.length ? labels.missing : '');
    const defs = node('defs');
    const gradient = node('linearGradient', {id: 'history-fill', x1: 0, y1: 0, x2: 0, y2: 1});
    gradient.append(node('stop', {offset: '0%', 'stop-color': '#8eaf79', 'stop-opacity': .26}), node('stop', {offset: '100%', 'stop-color': '#8eaf79', 'stop-opacity': .015}));
    defs.append(gradient); svg.append(defs);
    let segment = [];
    const drawSegment = () => {
      if (!segment.length) return;
      const path = segment.map((point, i) => (i ? 'L' : 'M') + point.x + ',' + point.y).join(' ');
      if (segment.length > 1) {
        svg.append(node('path', {d: path + ' L' + segment.at(-1).x + ',' + axes.bottom + ' L' + segment[0].x + ',' + axes.bottom + ' Z', class: 'chart-area'}));
        svg.append(node('path', {d: path, class: 'chart-line'}));
      }
      if (series.length < 100) for (const point of segment) svg.append(node('circle', {cx: point.x, cy: point.y, r: segment.length === 1 ? 4 : 2.5, class: 'chart-point'}));
      else if (segment.length === 1) svg.append(node('circle', {cx: segment[0].x, cy: segment[0].y, r: 3, class: 'chart-point'}));
      segment = [];
    };
    for (let i = 0; i < series.length; i++) {
      const point = series[i];
      const x = axes.x(Date.parse(point.at));
      const y = valid(point[metric]) ? axes.y(point[metric]) : null;
      points.push({x, y});
      const previous = series[i - 1];
      if (previous && data.bucket_seconds && Date.parse(point.at) - Date.parse(previous.at) > data.bucket_seconds * 1500) drawSegment();
      if (valid(y)) segment.push({x, y}); else drawSegment();
    }
    drawSegment();
    if (series.length) {
      const latest = series.findLastIndex(point => valid(point[metric]));
      reading(latest < 0 ? series.length - 1 : latest);
    } else {
      set('reading', '—'); set('reading-time', labels.noCoverage);
      svg.setAttribute('aria-label', labels[metric] + ' · ' + labels.empty);
    }
  }
  function activityChart() {
    activity.replaceChildren();
    const series = data.series;
    const mobile = compact();
    const width = mobile ? Math.max(240, Math.round(activity.clientWidth)) : 1040;
    const left = mobile ? 41 : 56, right = width - (mobile ? 8 : 16), span = right - left;
    activity.setAttribute('viewBox', '0 0 ' + width + ' 106');
    activity.append(node('line', {x1: left, x2: right, y1: 76, y2: 76, class: 'chart-grid'}));
    if (!series.length) return;
    const [start, end] = extent(series);
    const buckets = Array.from({length: 64}, () => ({cycles: 0, failures: 0}));
    for (const point of series) {
      const index = Math.max(0, Math.min(63, Math.floor((Date.parse(point.at) - start) / (end - start) * 64)));
      buckets[index].cycles += point.cycles || 0;
      buckets[index].failures += point.failures || 0;
    }
    const maximum = Math.max(1, ...buckets.map(item => item.cycles));
    for (let i = 0; i < buckets.length; i++) {
      const item = buckets[i], x = left + i * span / 64, barWidth = Math.max(2, Math.min(9, span / 64 * .65));
      if (!item.cycles) continue;
      const height = Math.max(3, item.cycles / maximum * 62);
      const rect = node('rect', {x, y: 76 - height, width: barWidth, height, rx: 2, class: 'activity-bar'});
      rect.append(node('title', {}, fullTime(start + (end - start) * i / 64) + ' · ' + labels.cycles + ': ' + item.cycles + ' · ' + labels.failures + ': ' + item.failures));
      activity.append(rect);
      if (item.failures) activity.append(node('rect', {x, y: 76 - item.failures / maximum * 62, width: barWidth, height: Math.max(3, item.failures / maximum * 62), rx: 2, class: 'activity-failure'}));
    }
    for (let i = 0; i < 3; i++) activity.append(node('text', {x: left + i * span / 2, y: 100, 'text-anchor': i === 0 ? 'start' : i === 2 ? 'end' : 'middle', class: 'chart-axis'}, axisTime(start + (end - start) * i / 2, end - start, mobile)));
    activity.setAttribute('aria-label', labels.activity + ' · ' + labels.activityDescription + ' · ' + labels.cycles + ' ' + (data.summary.cycles || 0) + ' · ' + labels.failures + ' ' + (data.summary.failure_cycles || 0));
  }
  function sampleTable() {
    const body = root.querySelector('[data-value="samples-table"]');
    const fragment = document.createDocumentFragment();
    for (const point of data.series) {
      const row = document.createElement('tr');
      for (const value of [fullTime(point.at), ...metricKeys.map(key => fmt(point[key], key === 'battery_voltage' ? 3 : 1))]) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      fragment.append(row);
    }
    body.replaceChildren(fragment);
  }
  async function load() {
    if (controller) controller.abort();
    controller = new AbortController();
    const current = controller;
    root.setAttribute('aria-busy', 'true');
    clear();
    set('chart-status', labels.loading);
    try {
      const url = new URL(root.dataset.url, window.location.origin); url.searchParams.set('days', range);
      const response = await fetch(url, {signal: current.signal, credentials: 'same-origin', headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error('History request failed');
      const result = await response.json();
      if (!Array.isArray(result.series) || !result.summary) throw new Error('Invalid history response');
      result.series = result.series.filter(point => point && Number.isFinite(Date.parse(point.at))).sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
      if (current !== controller) return;
      data = result; summary(); estimate(); chart(); activityChart(); sampleTable();
    } catch (error) {
      if (error.name === 'AbortError') return;
      clear();
      set('chart-status', labels.loadError);
      const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = labels.retry; retry.addEventListener('click', load);
      root.querySelector('[data-value="chart-status"]').append(retry);
    } finally {
      if (current === controller) root.setAttribute('aria-busy', 'false');
    }
  }
  root.querySelectorAll('[data-days]').forEach(button => button.addEventListener('click', () => {
    range = button.dataset.days;
    root.querySelectorAll('[data-days]').forEach(item => {item.classList.toggle('active', item === button); item.setAttribute('aria-pressed', String(item === button));});
    load();
  }));
  root.querySelectorAll('[data-metric]').forEach(button => button.addEventListener('click', () => {
    metric = button.dataset.metric;
    root.querySelectorAll('[data-metric]').forEach(item => {item.classList.toggle('active', item === button); item.setAttribute('aria-pressed', String(item === button));});
    chart();
  }));
  svg.addEventListener('pointermove', event => {
    if (!points.length || !chartX) return;
    const bounds = svg.getBoundingClientRect();
    const px = (event.clientX - bounds.left) / bounds.width * chartWidth;
    let nearest = 0;
    for (let i = 1; i < points.length; i++) if (Math.abs(points[i].x - px) < Math.abs(points[nearest].x - px)) nearest = i;
    reading(nearest);
  });
  svg.addEventListener('keydown', event => {
    if (!data || !data.series.length) return;
    let index = selected;
    if (event.key === 'ArrowLeft') index--;
    else if (event.key === 'ArrowRight') index++;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = data.series.length - 1;
    else return;
    event.preventDefault(); reading(index);
  });
  const retention = document.getElementById('retention-days');
  const custom = document.querySelector('.custom-retention');
  const customInput = document.getElementById('custom-days');
  const retentionState = () => {
    const isCustom = retention.value === 'custom';
    custom.hidden = !isCustom; customInput.disabled = !isCustom; customInput.required = isCustom;
  };
  retentionState();
  retention.addEventListener('change', retentionState);
  const selection = document.getElementById('history-selection');
  const boxes = [...selection.querySelectorAll('input[name="event_ids"]')];
  const all = document.getElementById('select-all-cycles');
  const selectionState = () => {
    const count = boxes.filter(box => box.checked).length;
    all.checked = Boolean(boxes.length) && count === boxes.length;
    all.indeterminate = count > 0 && count < boxes.length;
    document.getElementById('delete-selected').disabled = count === 0;
    const countLabel = document.querySelector('[data-value="selected-count"]');
    countLabel.textContent = count ? '(' + count + ')' : '';
  };
  all.disabled = boxes.length === 0;
  all.addEventListener('change', () => {boxes.forEach(box => {box.checked = all.checked;}); selectionState();});
  boxes.forEach(box => box.addEventListener('change', selectionState));
  let resizeFrame = null;
  window.addEventListener('resize', () => {
    if (resizeFrame) cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {if (data) {chart(); activityChart();}});
  });
  selectionState(); load();
})();
