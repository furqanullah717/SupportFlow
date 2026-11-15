(() => {
  'use strict';

  const toggle = document.querySelector('[data-nav-toggle]');
  const sidebar = document.querySelector('#sidebar');
  const mobile = window.matchMedia('(max-width: 800px)');
  const syncNavigation = () => {
    if (sidebar) sidebar.inert = mobile.matches && !document.body.classList.contains('nav-open');
  };
  const closeNavigation = () => {
    const restoreFocus = sidebar?.contains(document.activeElement);
    document.body.classList.remove('nav-open');
    toggle?.setAttribute('aria-expanded', 'false');
    syncNavigation();
    if (restoreFocus) toggle?.focus();
  };
  syncNavigation();
  mobile.addEventListener('change', syncNavigation);
  toggle?.addEventListener('click', () => {
    const open = document.body.classList.toggle('nav-open');
    toggle.setAttribute('aria-expanded', String(open));
    syncNavigation();
    if (open) sidebar?.querySelector('nav a')?.focus();
  });
  document.querySelector('[data-nav-close]')?.addEventListener('click', closeNavigation);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeNavigation();
    if (event.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      event.preventDefault();
      document.querySelector('#global-search')?.focus();
    }
  });

  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const original = button.textContent;
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        button.textContent = 'Copied!';
        setTimeout(() => { button.textContent = original; }, 2000);
      } catch {
        const input = button.parentElement.querySelector('input');
        input?.focus();
        input?.select();
        button.textContent = 'Select and copy link';
      }
    });
  });

  const notifications = document.querySelector('[data-notifications-url]');
  const refreshNotifications = async () => {
    if (!notifications || document.hidden) return;
    try {
      const response = await fetch(notifications.dataset.notificationsUrl, {
        cache: 'no-store', signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) return;
      const data = await response.json();
      notifications.setAttribute('aria-label', data.unread_count ? `Notifications, ${data.unread_count} unread` : 'Notifications');
      let dot = notifications.querySelector('.notification-dot');
      if (data.unread_count && !dot) {
        dot = document.createElement('span');
        dot.className = 'notification-dot';
        notifications.append(dot);
      }
      if (dot) dot.hidden = !data.unread_count;
    } catch { /* A background refresh must not interrupt active support work. */ }
  };
  if (notifications) {
    setInterval(refreshNotifications, 30000);
    document.addEventListener('visibilitychange', refreshNotifications);
  }

  const panel = document.querySelector('[data-ai-panel]');
  if (!panel) return;
  const output = panel.querySelector('.ai-output');
  const text = panel.querySelector('.ai-output-text');
  const meta = panel.querySelector('.ai-output-meta');
  const apply = panel.querySelector('.ai-apply');
  let last = null;

  panel.querySelectorAll('[data-ai-operation]').forEach((button) => {
    button.addEventListener('click', async () => {
      const buttons = panel.querySelectorAll('[data-ai-operation]');
      buttons.forEach((item) => { item.disabled = true; });
      output.hidden = false;
      apply.hidden = true;
      meta.textContent = 'Working on your suggestion…';
      text.textContent = '';
      last = null;
      try {
        const token = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
        const response = await fetch(panel.dataset.aiUrl, {
          method: 'POST',
          headers: {
            'X-CSRFToken': token, Accept: 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
          },
          body: new URLSearchParams({ operation: button.dataset.aiOperation, revision: panel.dataset.revision }),
          signal: AbortSignal.timeout(30000),
        });
        if (!response.headers.get('content-type')?.includes('application/json')) {
          throw new Error('Your session may have expired. Sign in again and retry.');
        }
        const data = await response.json();
        if (!response.ok) throw new Error(data.error?.message || 'We could not generate a suggestion. Please try again.');
        last = data;
        meta.textContent = `${data.demo ? 'Demo AI · ' : ''}${data.provider}${data.model ? ' / ' + data.model : ''} · Review before use`;
        if (data.operation === 'classify') {
          text.textContent = `Category: ${data.result.category || 'Uncategorized'}\nPriority: ${data.result.priority}\n\n${data.result.rationale}`;
          apply.textContent = 'Use suggestions';
          apply.hidden = false;
        } else {
          text.textContent = data.result.text;
          apply.textContent = 'Use as draft';
          apply.hidden = data.operation !== 'reply';
        }
      } catch (error) {
        meta.textContent = 'Suggestion unavailable';
        text.textContent = error.name === 'TimeoutError' ? 'This request took too long. Please try again.' : error.message;
      } finally {
        buttons.forEach((item) => { item.disabled = false; });
      }
    });
  });

  apply.addEventListener('click', () => {
    if (!last) return;
    if (String(last.source_revision) !== String(panel.dataset.revision)) {
      meta.textContent = 'This ticket changed. Refresh the page before applying a suggestion.';
      return;
    }
    if (last.operation === 'reply') {
      const textarea = document.querySelector('.reply-composer textarea');
      if (textarea) {
        textarea.value = last.result.text;
        textarea.focus();
        textarea.scrollIntoView({ behavior: 'smooth', block: 'center' });
        meta.textContent = 'Added to your draft. Review it before adding the reply.';
        apply.hidden = true;
      }
    }
    if (last.operation === 'classify') {
      const priority = document.querySelector('.metadata-form [name=priority]');
      const category = document.querySelector('.metadata-form [name=category]');
      if (priority) priority.value = last.result.priority;
      const option = category && [...category.options].find((item) => item.textContent.toLowerCase() === String(last.result.category).toLowerCase());
      if (option) category.value = option.value;
      meta.textContent = option ? 'Suggestions filled in. Review the ticket details and save changes.' : 'Priority filled in. Choose a category and save changes.';
      document.querySelector('.metadata-form')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      apply.hidden = true;
    }
  });
})();
