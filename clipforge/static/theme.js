/* ClipForge 2.0 appearance controller.
 * Changes are visual only: no media/job/publishing state is ever modified.
 */
(() => {
  'use strict';
  const KEY = 'clipforge.appearance.theme.v2';
  const themes = ['precision', 'editorial', 'control'];
  const root = document.documentElement;
  const allowed = (value) => themes.includes(value) ? value : 'precision';
  const read = () => {
    try { return allowed(localStorage.getItem(KEY)); }
    catch { return 'precision'; }
  };
  const apply = (name, persist = true) => {
    const theme = allowed(name);
    root.dataset.theme = theme;
    root.style.colorScheme = theme === 'editorial' ? 'light' : 'dark';
    if (persist) {
      try { localStorage.setItem(KEY, theme); } catch { /* private mode */ }
    }
    for (const select of document.querySelectorAll('[data-theme-select]')) {
      select.value = theme;
    }
    for (const button of document.querySelectorAll('[data-theme-choice]')) {
      const selected = button.dataset.themeChoice === theme;
      button.setAttribute('aria-pressed', String(selected));
      button.classList.toggle('is-current', selected);
    }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      apply(read(), false);
      document.querySelectorAll('[data-theme-select]').forEach((select) => {
        select.addEventListener('change', () => apply(select.value));
      });
      document.querySelectorAll('[data-theme-choice]').forEach((button) => {
        button.addEventListener('click', () => apply(button.dataset.themeChoice));
      });
      const reset = document.getElementById('theme-reset');
      if (reset) reset.addEventListener('click', () => apply('precision'));
    }, {once: true});
  } else {
    apply(read(), false);
  }
})();
