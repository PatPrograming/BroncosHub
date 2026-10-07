/* Presentation only. Account, registration and payment rules belong on the server. */
(() => {
  const menuButton = document.querySelector('.menu-toggle');
  const navigation = document.querySelector('#site-navigation');
  function closeMenu() {
    navigation?.classList.remove('is-open');
    menuButton?.setAttribute('aria-expanded', 'false');
    menuButton?.setAttribute('aria-label', 'Open navigation');
  }
  menuButton?.addEventListener('click', () => {
    const open = menuButton.getAttribute('aria-expanded') !== 'true';
    navigation.classList.toggle('is-open', open);
    menuButton.setAttribute('aria-expanded', String(open));
    menuButton.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && navigation?.classList.contains('is-open')) {
      closeMenu(); menuButton.focus();
    }
  });
  const desktop = window.matchMedia('(min-width:821px)');
  desktop.addEventListener('change', closeMenu);
  document.addEventListener('click', (event) => {
    document.querySelectorAll('.preview-menu[open]').forEach((menu) => {
      if (!menu.contains(event.target)) menu.removeAttribute('open');
    });
  });

  const dialog = document.querySelector('#confirm-dialog');
  const confirmButton = document.querySelector('#confirm-action');
  let pendingForm = null;
  let trigger = null;
  document.querySelectorAll('[data-confirm-form]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      pendingForm = document.getElementById(button.dataset.confirmForm);
      if (!pendingForm) return;
      trigger = button;
      document.querySelector('#confirm-title').textContent = button.dataset.confirmTitle || 'Are you sure?';
      document.querySelector('#confirm-message').textContent = button.dataset.confirmMessage || 'Please confirm before continuing.';
      confirmButton.textContent = button.dataset.confirmLabel || 'Yes, continue';
      dialog.querySelector('[autofocus]').textContent = button.dataset.confirmCancelLabel || 'Keep it';
      dialog.showModal();
      dialog.querySelector('[autofocus]').focus();
    });
  });
  document.querySelectorAll('[data-dialog-close]').forEach((button) => button.addEventListener('click', () => dialog.close()));
  dialog?.addEventListener('close', () => { pendingForm = null; trigger?.focus(); });
  dialog?.addEventListener('click', (event) => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  confirmButton?.addEventListener('click', () => {
    if (!pendingForm) return;
    const form = pendingForm;
    dialog.close();
    form.requestSubmit();
  });

  // Bring server-returned validation messages into keyboard and screen-reader focus.
  const errors = document.querySelector('.alert-error[role="alert"]');
  if (errors) {
    errors.tabIndex = -1;
    errors.focus();
  }
})();
