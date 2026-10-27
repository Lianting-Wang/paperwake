/* Draft rendering uses the authenticated, read-only native preview endpoint. */
(() => {
  'use strict';
  document.querySelectorAll('[data-screen-preview]').forEach(preview => {
    const image = preview.querySelector('[data-preview-image]');
    const message = preview.querySelector('[data-preview-message]');
    const show = text => { message.textContent = text; message.hidden = !text; };
    image.addEventListener('error', () => show(preview.dataset.error));
    image.addEventListener('load', () => show(''));
    if (image.complete && !image.naturalWidth) show(preview.dataset.error);
    const form = document.querySelector('[data-content-editor]');
    if (!form) return;
    let timer, controller, objectUrl, generation = 0;
    const render = async current => {
      controller = new AbortController();
      preview.setAttribute('aria-busy', 'true');
      show(preview.dataset.loading);
      let nextUrl;
      try {
        const response = await fetch(preview.dataset.url, {
          method: 'POST', body: new FormData(form), credentials: 'same-origin', signal: controller.signal,
        });
        if (!response.ok || !response.headers.get('Content-Type')?.startsWith('image/png')) {
          if (current === generation) show(response.status === 400 ? preview.dataset.invalid : preview.dataset.error);
          return;
        }
        nextUrl = URL.createObjectURL(await response.blob());
        const next = new Image();
        next.src = nextUrl;
        await next.decode();
        if (current !== generation) { URL.revokeObjectURL(nextUrl); return; }
        const previous = objectUrl;
        objectUrl = nextUrl;
        image.src = objectUrl;
        preview.querySelectorAll('[data-preview-open]').forEach(link => { link.href = objectUrl; });
        if (previous) URL.revokeObjectURL(previous);
        show('');
      } catch (error) {
        if (nextUrl) URL.revokeObjectURL(nextUrl);
        if (error.name !== 'AbortError' && current === generation) show(preview.dataset.error);
      } finally {
        if (current === generation) preview.setAttribute('aria-busy', 'false');
      }
    };
    const queue = () => {
      generation += 1;
      const current = generation;
      preview.setAttribute('aria-busy', 'true');
      clearTimeout(timer);
      controller?.abort();
      timer = setTimeout(() => render(current), 200);
    };
    form.addEventListener('input', event => { if (event.target.name !== 'debug') queue(); });
    form.addEventListener('submit', () => { generation += 1; clearTimeout(timer); controller?.abort(); });
    // Redisplay rejected drafts without changing the saved device configuration.
    if (document.querySelector('[role="alert"]')) queue();
  });
})();
