// Installed once in the parent page. Automatic velocity checks are shown with the
// browser's own Notification API, so nothing is pushed from a server and no
// third party is involved: a queued check appears when this app is open in a tab
// or in its installed window. Permission is requested from a real click, which is
// what Chrome requires, and a check stays queued until it has actually been shown.
(() => {
  if (!('Notification' in window)) return;
  const STORE = 'crisisradar.velocity.notified';
  const shown = new Set();
  try { JSON.parse(localStorage.getItem(STORE) || '[]').forEach((id) => shown.add(id)); } catch (_) {}
  const remember = () => {
    try { localStorage.setItem(STORE, JSON.stringify([...shown].slice(-200))); } catch (_) {}
  };
  const show = (marker) => {
    const id = marker.dataset.smNotifyId;
    if (!id || shown.has(id)) return;
    // Keep it queued while permission is missing: granting it later still shows
    // the check, and the app's own toast has already reported it in the page.
    if (Notification.permission !== 'granted') return;
    let notification;
    try {
      notification = new Notification(marker.dataset.smNotifyTitle || 'CrisisRadar', {
        body: marker.dataset.smNotifyBody || '',
        tag: id,
        icon: '/app/static/icon-192.png',
        badge: '/app/static/favicon-32.png',
      });
    } catch (_) {
      return;  // Android and iOS only allow notifications from a service worker.
    }
    shown.add(id);
    remember();
    notification.onclick = () => { try { window.focus(); notification.close(); } catch (_) {} };
  };
  const ask = () => {
    if (Notification.permission !== 'default') return;
    try { Promise.resolve(Notification.requestPermission()).then(scan, () => {}); } catch (_) {}
  };
  const scan = () => {
    for (const button of document.querySelectorAll('[class*="st-key-velocity_notify_enable"] button')) {
      if (button.dataset.smNotifyWired) continue;
      button.dataset.smNotifyWired = '1';
      button.addEventListener('click', ask);
    }
    document.querySelectorAll('[data-sm-notify-id]').forEach(show);
  };
  let timer;
  new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(scan, 100);
  }).observe(document.body, { childList: true, subtree: true });
  scan();
})();
