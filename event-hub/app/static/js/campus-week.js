/* Native date links remain the fallback. No account or payment state lives here. */
(() => {
  const rail = document.querySelector('.campus-week-rail');
  const results = document.querySelector('#campus-results');
  const form = document.querySelector('#campus-filters');
  const status = document.querySelector('#campus-week-status');
  if (!rail || !results || !form || !window.fetch || !window.AbortController) return;

  let marker = rail.querySelector('.campus-week-marker');
  const calendar = document.querySelector('.campus-week-scroll');
  const navigation = document.querySelector('.campus-week-navigation');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const narrowScreen = window.matchMedia('(max-width: 640px)');
  const filterDisclosure = document.querySelector('.campus-filter-disclosure');
  let controller = null;
  let requestNumber = 0;
  let transition = null;
  let renderedLocation = window.location.pathname + window.location.search;

  function positionMarker() {
    const selected = rail.querySelector('[data-campus-date][aria-current]');
    if (!marker || !selected) {
      if (marker) marker.hidden = true;
      return;
    }
    marker.hidden = false;
    marker.style.width = `${selected.offsetWidth}px`;
    marker.style.height = `${selected.offsetHeight}px`;
    marker.style.transform = `translate(${selected.offsetLeft}px, ${selected.offsetTop}px)`;
    rail.classList.add('has-campus-marker');
  }

  function nameCards() {
    results.querySelectorAll('.event-card').forEach((card) => {
      const href = card.querySelector('h3 a')?.getAttribute('href') || '';
      const eventId = href.match(/^\/events\/(\d+)$/)?.[1];
      if (eventId) card.style.viewTransitionName = `campus-event-${eventId}`;
    });
  }

  function revealDate(link) {
    if (!calendar || !link) return;
    // Move only this horizontal scroller, never the page or keyboard focus.
    calendar.scrollLeft = Math.max(0, link.offsetLeft + link.offsetWidth / 2 - calendar.clientWidth / 2);
  }

  function syncControls(nextDocument) {
    const nextRail = nextDocument.querySelector('.campus-week-rail');
    const weekChanged = rail.dataset.calendar !== nextRail.dataset.calendar;
    const focusedDate = rail.contains(document.activeElement) ? document.activeElement.dataset.campusDate : undefined;
    const focusedDirection = navigation?.contains(document.activeElement) ? document.activeElement.dataset.campusWeek : undefined;
    if (weekChanged) {
      rail.replaceChildren(...[...nextRail.childNodes].map((node) => document.importNode(node, true)));
      rail.dataset.calendar = nextRail.dataset.calendar;
      rail.setAttribute('aria-label', nextRail.getAttribute('aria-label'));
      marker = rail.querySelector('.campus-week-marker');
      // Keep later-week dates visible even when the rail overflows on a phone.
      revealDate(rail.querySelector('[aria-current="date"]') || rail.querySelector('[data-campus-date]:not(.campus-day-all)'));
    }
    rail.querySelectorAll('[data-campus-date]').forEach((link) => {
      const incoming = [...nextRail.querySelectorAll('[data-campus-date]')]
        .find((candidate) => candidate.dataset.campusDate === link.dataset.campusDate);
      if (!incoming) return;
      link.href = incoming.getAttribute('href');
      for (const name of ['aria-current', 'aria-label']) {
        if (incoming.hasAttribute(name)) link.setAttribute(name, incoming.getAttribute(name));
        else link.removeAttribute(name);
      }
      link.querySelector('.campus-day-count').textContent = incoming.querySelector('.campus-day-count').textContent;
    });
    const nextForm = nextDocument.querySelector('#campus-filters');
    for (const name of ['q', 'category', 'location', 'calendar']) {
      const current = form.elements.namedItem(name);
      const incoming = nextForm.elements.namedItem(name);
      if (current && incoming) current.value = incoming.value;
    }
    const dateSelect = form.elements.namedItem('date');
    const nextDateSelect = nextForm.elements.namedItem('date');
    if (dateSelect && nextDateSelect) {
      dateSelect.replaceChildren(...[...nextDateSelect.children].map((node) => document.importNode(node, true)));
      dateSelect.value = nextDateSelect.value;
    }
    const nextNavigation = nextDocument.querySelector('.campus-week-navigation');
    if (navigation && nextNavigation) {
      navigation.replaceChildren(...[...nextNavigation.childNodes].map((node) => document.importNode(node, true)));
      if (focusedDirection) {
        (navigation.querySelector(`[data-campus-week="${focusedDirection}"]`) || navigation.querySelector('a'))?.focus({preventScroll: true});
      }
    }
    if (weekChanged && focusedDate !== undefined) {
      const matchingDate = [...rail.querySelectorAll('[data-campus-date]')].find((link) => link.dataset.campusDate === focusedDate);
      (matchingDate || rail.querySelector('[data-campus-date]'))?.focus({preventScroll: true});
    }
    positionMarker();
  }

  async function visit(url, pushHistory = true) {
    const target = new URL(url, window.location.href);
    if (target.origin !== window.location.origin || target.pathname !== '/events') {
      window.location.assign(target.href);
      return;
    }
    const thisRequest = ++requestNumber;
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    transition?.skipTransition();
    results.setAttribute('aria-busy', 'true');
    if (status) status.textContent = 'Finding campus plans…';
    try {
      const response = await fetch(target.href, {signal, credentials: 'same-origin', headers: {'Accept': 'text/html'}});
      if (!response.ok || response.redirected) throw new Error('Use normal page navigation.');
      const nextDocument = new DOMParser().parseFromString(await response.text(), 'text/html');
      const incomingResults = nextDocument.querySelector('#campus-results');
      if (!incomingResults || !nextDocument.querySelector('.campus-week-rail') || !nextDocument.querySelector('#campus-filters')) {
        throw new Error('The response is not an event discovery page.');
      }
      if (signal.aborted || thisRequest !== requestNumber) return;
      const nextCalendar = nextDocument.querySelector('.campus-week-rail').dataset.calendar;
      const weekChanged = nextCalendar !== rail.dataset.calendar;
      const update = () => {
        if (signal.aborted || thisRequest !== requestNumber) return;
        const focusWasInResults = results.contains(document.activeElement);
        const focusedHref = focusWasInResults ? document.activeElement.closest('a')?.getAttribute('href') : null;
        results.replaceChildren(...[...incomingResults.childNodes].map((node) => document.importNode(node, true)));
        results.dataset.announcement = incomingResults.dataset.announcement || '';
        syncControls(nextDocument);
        nameCards();
        document.title = nextDocument.title;
        renderedLocation = target.pathname + target.search;
        results.removeAttribute('aria-busy');
        if (pushHistory && target.href !== window.location.href) {
          history.pushState({campusWeek: true}, '', target.href);
        }
        if (status) status.textContent = weekChanged
          ? `Showing ${navigation.querySelector('.campus-week-range').textContent}. Pick a date to see its events.`
          : results.dataset.announcement;
        if (focusWasInResults) {
          const matchingLink = [...results.querySelectorAll('a')].find((link) => link.getAttribute('href') === focusedHref);
          (matchingLink || rail.querySelector('[aria-current]') || rail.querySelector('[data-campus-date]'))?.focus({preventScroll: true});
        }
      };
      if (document.startViewTransition && !reducedMotion.matches && !document.hidden) {
        document.documentElement.dataset.campusTransition = 'true';
        if (weekChanged) document.documentElement.dataset.campusDirection = nextCalendar > rail.dataset.calendar ? 'next' : 'previous';
        else delete document.documentElement.dataset.campusDirection;
        const currentTransition = document.startViewTransition(update);
        transition = currentTransition;
        try {
          // Animation can be skipped, but a failed DOM update must reach the
          // normal-navigation fallback rather than leave a partial page.
          await currentTransition.finished;
        } finally {
          if (transition === currentTransition) {
            transition = null;
            delete document.documentElement.dataset.campusTransition;
            delete document.documentElement.dataset.campusDirection;
          }
        }
      } else {
        update();
      }
    } catch (error) {
      if (signal.aborted || thisRequest !== requestNumber) return;
      window.location.assign(target.href);
    } finally {
      if (thisRequest === requestNumber) results.removeAttribute('aria-busy');
    }
  }

  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-campus-date], a[data-campus-clear], a[data-campus-week]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download')) return;
    const target = new URL(link.href);
    if (target.origin !== window.location.origin || target.pathname !== '/events') return;
    event.preventDefault();
    // A day choice applies the visible filters, including text just typed.
    // Native links still work with the last submitted filters without JS.
    const params = new URLSearchParams(new FormData(form));
    if (link.hasAttribute('data-campus-week')) params.set('calendar', target.searchParams.get('calendar') || '');
    else params.set('date', link.dataset.campusDate || '');
    for (const [name, value] of [...params]) {
      if (!value.trim()) params.delete(name);
      else params.set(name, value.trim());
    }
    target.search = params.toString();
    visit(target.href);
  });

  window.addEventListener('popstate', () => {
    // Hash-only navigation (the mobile results anchor) must keep its native focus.
    const requestedLocation = window.location.pathname + window.location.search;
    if (window.location.pathname === '/events' && requestedLocation !== renderedLocation) {
      visit(window.location.href, false);
    } else if (window.location.pathname === '/events') {
      // Back then Forward can return to the rendered page while an older fetch
      // is still pending. It must not overwrite the page behind the new URL.
      ++requestNumber;
      controller?.abort();
      transition?.skipTransition();
      results.removeAttribute('aria-busy');
      if (status) status.textContent = results.dataset.announcement;
    }
  });
  window.addEventListener('resize', positionMarker);
  reducedMotion.addEventListener('change', () => {
    if (reducedMotion.matches) transition?.skipTransition();
  });
  if (filterDisclosure) {
    const hasActiveFilters = ['q', 'category', 'location']
      .some((name) => form.elements.namedItem(name)?.value.trim());
    if (narrowScreen.matches && !hasActiveFilters) filterDisclosure.open = false;
    narrowScreen.addEventListener('change', () => {
      // Only the initial mobile view collapses automatically; keep a user's
      // choice on mobile and ensure all desktop controls remain available.
      if (!narrowScreen.matches) filterDisclosure.open = true;
    });
  }
  if ('ResizeObserver' in window) new ResizeObserver(positionMarker).observe(rail);
  positionMarker();
  revealDate(rail.querySelector('[aria-current="date"]'));
  nameCards();
})();
