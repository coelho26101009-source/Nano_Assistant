/**
 * The overlay renderer: apply a state, and nothing else.
 *
 * There is no timer here that advances a state, and no optimistic transition.
 * Every view object arrives from the main process, which builds it from a real
 * backend event (lib/overlay-state.js). If the backend goes quiet, the overlay
 * keeps showing the last thing that actually happened -- which is the truth --
 * rather than pretending to make progress.
 */
'use strict';

(function () {
  const panel = document.getElementById('panel');
  const label = document.getElementById('label');
  const detail = document.getElementById('detail');

  function render(view) {
    if (!view || typeof view !== 'object') return;
    const visible = view.visible !== false;

    panel.dataset.state = typeof view.state === 'string' ? view.state : 'idle';
    panel.dataset.visible = visible ? 'true' : 'false';

    if (visible) {
      label.textContent = typeof view.label === 'string' ? view.label : '';
      detail.textContent = typeof view.detail === 'string' ? view.detail : '';
    }
  }

  if (window.nanoOverlay && typeof window.nanoOverlay.onState === 'function') {
    window.nanoOverlay.onState(render);
  }
})();
