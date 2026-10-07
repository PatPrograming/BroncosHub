/* Saved identity stays unchanged until the server accepts an update. */
(() => {
  const form = document.querySelector('[data-profile-form]');
  if (!form) return;
  const name = form.elements.namedItem('name');
  const year = form.elements.namedItem('year_level');
  const save = form.querySelector('[data-profile-save]');
  const discard = form.querySelector('[data-profile-discard]');
  const status = form.querySelector('[data-profile-save-state]');
  const saved = {name: form.dataset.savedName, year: form.dataset.savedYear};
  let hasErrors = form.dataset.hasErrors === 'true';

  function refresh() {
    const dirty = name.value.trim() !== saved.name || year.value !== saved.year;
    save.disabled = !dirty && !hasErrors;
    discard.hidden = !dirty && !hasErrors;
    const message = dirty ? 'You have unsaved changes.' : hasErrors ? 'Please check your details.' : 'You’re up to date.';
    if (status.textContent !== message) status.textContent = message;
    status.dataset.dirty = String(dirty);
  }

  form.addEventListener('input', refresh);
  form.addEventListener('change', refresh);
  discard.addEventListener('click', () => {
    // After a validation response the input defaults are rejected values.
    // Restore the persisted profile explicitly instead of form.reset().
    name.value = saved.name;
    year.value = saved.year;
    hasErrors = false;
    document.querySelectorAll('[data-profile-errors], [data-profile-field-error]').forEach((element) => { element.hidden = true; });
    name.removeAttribute('aria-invalid');
    year.removeAttribute('aria-invalid');
    name.removeAttribute('aria-describedby');
    year.setAttribute('aria-describedby', 'year-help');
    refresh();
    name.focus({preventScroll: true});
  });
  window.addEventListener('pageshow', refresh);
  refresh();
})();
