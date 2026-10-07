/* Presentation only: an event draft preview. Saving and validation stay on the server. */
(() => {
  const editor = document.querySelector('[data-event-editor]');
  const form = editor?.querySelector('[data-event-form]');
  if (!editor || !form) return;

  const card = editor.querySelector('[data-draft-card]');
  const art = editor.querySelector('[data-preview-art]');
  const state = editor.querySelector('[data-form-state]');
  const cover = editor.querySelector('[data-cover-upload]');
  const coverInput = form.elements.namedItem('cover_image');
  const removeValue = form.querySelector('[data-remove-cover-value]');
  const removeButton = editor.querySelector('[data-cover-remove]');
  const coverError = editor.querySelector('#cover_image-error');
  const coverName = editor.querySelector('[data-cover-name]');
  const savedCoverName = coverName?.textContent || 'Current event cover';
  let storedCover = cover?.dataset.currentCover || '';
  let objectUrl = '';
  let coverRequest = 0;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const artwork = {
    'Arts & Culture': 'makers.svg',
    Social: 'club-fair.svg',
    Sports: 'basketball.svg',
    Tech: 'tech.svg',
    Service: 'volunteer.svg',
    Wellness: 'coffee.svg',
  };
  const fieldValue = (name) => form.elements.namedItem(name)?.value.trim() || '';
  const originalValues = Array.from(form.elements)
    .filter((field) => field.name && field.type !== 'hidden')
    .map((field) => [field.name, field.value]);

  function setPreview(name, text) {
    const output = editor.querySelector(`[data-preview-field="${name}"]`);
    if (output) output.textContent = text;
  }

  function scheduleLabel(value) {
    // datetime-local carries the event's Pacific wall-clock time. Keep those
    // entered fields intact even when the browser is in another time zone.
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
    if (!match) return 'Date & time to come';
    const [, year, month, day, hour, minute] = match.map(Number);
    const date = new Date(Date.UTC(year, month - 1, day, hour, minute));
    if (Number.isNaN(date.getTime())) return 'Date & time to come';
    return new Intl.DateTimeFormat('en-US', {
      weekday: 'short', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit', timeZone: 'UTC',
    }).format(date) + ' PT';
  }

  function refreshPreview() {
    setPreview('title', fieldValue('title') || 'Something good is coming.');
    setPreview('category', fieldValue('category') || 'Your category');
    setPreview('location', fieldValue('location') || 'Your spot on campus');
    setPreview('date', scheduleLabel(fieldValue('start_time')));
    const enteredPrice = fieldValue('price');
    const price = Number(enteredPrice);
    setPreview('price', enteredPrice === '' || !Number.isFinite(price) || price < 0
      ? 'Price to come'
      : price === 0 ? 'Free'
      : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(price));

    const dirty = form.dataset.dirtyOnLoad === 'true' || removeValue?.value === '1' || originalValues.some(([name, value]) => form.elements.namedItem(name)?.value !== value);
    const stateText = dirty ? 'Unsaved changes' : state?.dataset.cleanLabel || 'Ready when you are';
    if (state && state.textContent !== stateText) state.textContent = stateText;
    if (state) state.dataset.dirty = String(dirty);
  }

  function refreshArtwork(animate = false) {
    const imageUrl = objectUrl || storedCover;
    const category = fieldValue('category');
    const file = category === art?.dataset.originalCategory
      ? art.dataset.originalArt : artwork[category] || 'campus.svg';
    if (art) art.src = imageUrl || `/static/images/${file}`;
    if (cover) {
      cover.querySelector('[data-cover-preview]').hidden = !imageUrl;
      cover.querySelector('[data-cover-empty]').hidden = Boolean(imageUrl);
      const image = cover.querySelector('[data-cover-image]');
      if (imageUrl) image.src = imageUrl;
      else image.removeAttribute('src');
      removeButton.hidden = !imageUrl && coverError.hidden;
      removeButton.querySelector('[data-cover-remove-label]').textContent = !coverError.hidden && !objectUrl
        ? storedCover ? 'Keep current cover' : 'Use default cover' : 'Remove image';
      coverName.textContent = objectUrl ? coverInput.files[0]?.name || 'Selected image'
        : storedCover ? savedCoverName : 'No image selected. We’ll use a campus cover.';
    }
    if (animate && !reducedMotion.matches && card) {
      card.classList.remove('is-category-changing');
      requestAnimationFrame(() => card.classList.add('is-category-changing'));
    }
  }

  function releaseObjectUrl() {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = '';
  }

  function showCoverError(message) {
    coverInput.setCustomValidity(message);
    coverInput.toggleAttribute('aria-invalid', Boolean(message));
    if (message) coverInput.setAttribute('aria-invalid', 'true');
    coverError.textContent = message;
    coverError.hidden = !message;
  }

  async function selectCover() {
    const request = ++coverRequest;
    const file = coverInput.files[0];
    if (!file) {
      releaseObjectUrl();
      showCoverError('');
      refreshArtwork();
      refreshPreview();
      return;
    }
    releaseObjectUrl();
    const allowed = ['image/jpeg', 'image/png', 'image/webp'];
    let error = file.size > 5 * 1024 * 1024 ? 'Choose an image smaller than 5 MB.'
      : file.size === 0 ? 'This file is empty. Choose another image.'
      : !allowed.includes(file.type) && !(file.type === '' && /\.(jpe?g|png|webp)$/i.test(file.name))
      ? 'Choose a JPG, PNG or WebP image.' : '';
    let candidate = '';
    if (!error) {
      coverInput.setCustomValidity('Please wait for your image to load.');
      coverName.textContent = 'Loading your cover…';
      candidate = URL.createObjectURL(file);
      const image = new Image();
      image.src = candidate;
      try {
        await image.decode();
        if (image.naturalWidth * image.naturalHeight > 20000000 || Math.max(image.naturalWidth, image.naturalHeight) > 10000) {
          error = 'Choose an image under 20 megapixels and 10,000 pixels per side.';
        }
      } catch {
        error = 'This image could not be opened. Try a different JPG, PNG or WebP.';
      }
    }
    if (request !== coverRequest) {
      if (candidate) URL.revokeObjectURL(candidate);
      return;
    }
    if (error) {
      if (candidate) URL.revokeObjectURL(candidate);
      coverInput.value = '';
      showCoverError(error);
    } else {
      objectUrl = candidate;
      removeValue.value = '0';
      showCoverError('');
    }
    refreshArtwork(!error);
    refreshPreview();
  }

  if (cover && coverInput && removeValue) {
    removeValue.disabled = false;
    coverInput.addEventListener('change', selectCover);
    removeButton.addEventListener('click', () => {
      ++coverRequest;
      // Rejecting a replacement should never force removal of the saved cover.
      if (!coverError.hidden && !objectUrl) {
        showCoverError('');
        refreshArtwork();
        coverInput.focus({preventScroll: true});
        return;
      }
      releaseObjectUrl();
      storedCover = '';
      coverInput.value = '';
      form.elements.namedItem('cover_token').value = '';
      removeValue.value = '1';
      showCoverError('');
      refreshArtwork();
      refreshPreview();
      coverInput.focus({preventScroll: true});
    });
    let dragDepth = 0;
    cover.addEventListener('dragenter', (event) => {
      if (!event.dataTransfer.types.includes('Files')) return;
      event.preventDefault();
      dragDepth += 1;
      cover.classList.add('is-dragging');
    });
    cover.addEventListener('dragover', (event) => {
      if (event.dataTransfer.types.includes('Files')) {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
      }
    });
    cover.addEventListener('dragleave', () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) cover.classList.remove('is-dragging');
    });
    cover.addEventListener('drop', (event) => {
      event.preventDefault();
      dragDepth = 0;
      cover.classList.remove('is-dragging');
      if (event.dataTransfer.files.length !== 1) {
        ++coverRequest;
        releaseObjectUrl();
        coverInput.value = '';
        showCoverError('Choose one cover image at a time.');
        refreshArtwork();
        refreshPreview();
        return;
      }
      coverInput.files = event.dataTransfer.files;
      selectCover();
    });
    // Browser history can restore a selected file; keep its preview in sync.
    window.addEventListener('pageshow', () => {
      if (coverInput.files.length) selectCover();
    });
  }

  form.addEventListener('input', refreshPreview);
  form.addEventListener('change', (event) => {
    refreshPreview();
    if (event.target.name === 'category') refreshArtwork(true);
  });
  card?.addEventListener('animationend', () => card.classList.remove('is-category-changing'));
  reducedMotion.addEventListener('change', () => card?.classList.remove('is-category-changing'));
  refreshPreview();
  refreshArtwork();
})();
