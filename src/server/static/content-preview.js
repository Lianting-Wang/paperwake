/* Local preview only; content is sent to the device after an explicit save. */
(() => {
  'use strict';
  const form = document.querySelector('[data-content-editor]');
  const preview = document.querySelector('[data-screen-preview]');
  if (!form || !preview) return;
  const update = () => {
    preview.querySelector('[data-preview-title]').textContent = form.elements.title.value || '—';
    preview.querySelector('[data-preview-status]').textContent = form.elements.status.value || '—';
    const rows = [];
    for (let i = 0; i < 6; i += 1) {
      const label = form.elements[`label_${i}`].value;
      const value = form.elements[`value_${i}`].value;
      if (!label && !value) continue;
      const row = document.createElement('div');
      const name = document.createElement('dt');
      const content = document.createElement('dd');
      name.textContent = label;
      content.textContent = value;
      row.append(name, content);
      rows.push(row);
    }
    preview.querySelector('[data-preview-rows]').replaceChildren(...rows);
  };
  form.addEventListener('input', update);
  update();
})();
