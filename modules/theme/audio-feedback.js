// Installed once in the parent page; no media downloads or external services.
(() => {
  const Audio = window.AudioContext || window.webkitAudioContext;
  if (!Audio) return;
  let enabled = true;
  try { enabled = localStorage.getItem('crisisradar.sounds') !== 'off'; } catch (_) {}
  let context;
  let lastPlayed = -Infinity;
  let pending;
  const voices = new Set();
  const unlock = () => {
    if (!enabled) return;
    try {
      context ||= new Audio();
      if (context.state === 'suspended') context.resume().catch(() => {});
    } catch (_) { /* Audio is optional; app actions must still work. */ }
  };
  document.addEventListener('pointerdown', unlock, { capture: true, passive: true });
  document.addEventListener('keydown', unlock, { capture: true });
  const play = (kind) => {
    if (!enabled || !context || context.state !== 'running') return;
    if (performance.now() - lastPlayed < 1200) return;
    lastPlayed = performance.now();
    try {
      // Bright ascending major triad; gentle descending pair for errors.
      const notes = kind === 'success' ? [523.25, 659.25, 783.99] : [349.23, 261.63];
      notes.forEach((frequency, index) => {
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        const start = context.currentTime + index * 0.13;
        oscillator.type = 'sine';
        oscillator.frequency.value = frequency;
        gain.gain.setValueAtTime(0, start);
        gain.gain.linearRampToValueAtTime(0.075, start + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.001, start + 0.33);
        oscillator.connect(gain);
        gain.connect(context.destination);
        voices.add(oscillator);
        oscillator.onended = () => { voices.delete(oscillator); oscillator.disconnect(); gain.disconnect(); };
        oscillator.start(start);
        oscillator.stop(start + 0.36);
      });
    } catch (_) {}
  };
  const request = (kind) => {
    // Coalesce alerts from one render; errors take priority over completion.
    if (pending === 'error') return;
    pending = kind;
    clearTimeout(request.timer);
    request.timer = setTimeout(() => { const next = pending; pending = null; play(next); }, 180);
  };
  const button = document.createElement('button');
  button.id = 'sm-sound-toggle';
  button.type = 'button';
  button.style.cssText = 'position:fixed;bottom:16px;right:18px;z-index:999990;border:1px solid #a8b2c5;border-radius:20px;padding:8px 12px;background:#fff;color:#243247;font:500 12px system-ui;cursor:pointer;box-shadow:0 2px 8px #0001';
  const label = () => {
    button.textContent = enabled ? '♫ Sound on' : '♫ Sound off';
    button.setAttribute('aria-label', enabled ? 'Mute notification sounds' : 'Enable notification sounds');
    button.setAttribute('aria-pressed', String(enabled));
    button.title = 'Collection completion and error sounds';
  };
  button.addEventListener('click', () => {
    enabled = !enabled;
    try { localStorage.setItem('crisisradar.sounds', enabled ? 'on' : 'off'); } catch (_) {}
    label();
    if (enabled) {
      unlock();
      if (context) context.resume().then(() => play('success')).catch(() => {});
    } else {
      clearTimeout(request.timer);
      pending = null;
      for (const voice of voices) { try { voice.stop(); } catch (_) {} }
    }
  });
  label();
  document.body.appendChild(button);
  const seen = new Set();
  let previousErrors = new Set();
  const scan = () => {
    for (const marker of document.querySelectorAll('[data-sm-sound-id]')) {
      const id = marker.dataset.smSoundId;
      if (seen.has(id)) continue;
      seen.add(id);
      request(marker.dataset.smSound);
    }
    const errors = new Set([...document.querySelectorAll('[data-testid="stAlertContentError"], [data-testid="stException"]')]
      .map(el => el.textContent.trim()).filter(Boolean));
    for (const error of errors) if (!previousErrors.has(error)) request('error');
    previousErrors = errors;
  };
  let timer;
  new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(scan, 100);
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
  scan();
})();
