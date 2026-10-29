// Runs once in the main Streamlit page (injected by modules/ui.py).
// Glass cards and .sm-reveal blocks ease into place as they scroll into view.
(() => {
  const root = document.documentElement;
  const CARD = '[data-testid="stMain"] [data-testid="stVerticalBlock"][class*="st-key-"]';
  const TARGETS = `.sm-reveal, ${CARD}`;

  // Frost the header once the main column scrolls away from the top.
  document.addEventListener("scroll", (event) => {
    const el = event.target;
    if (el instanceof Element && el.matches('[data-testid="stMain"]')) {
      root.classList.toggle("sm-scrolled", el.scrollTop > 8);
    }
  }, { capture: true, passive: true });

  if (matchMedia("(prefers-reduced-motion: reduce)").matches || !("IntersectionObserver" in window)) return;

  const observer = new IntersectionObserver((entries) => {
    // Items that arrive together cascade instead of appearing as one block.
    let order = 0;
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.style.setProperty("--sm-delay", `${Math.min(order++, 6) * 70}ms`);
      entry.target.classList.add("sm-in");
      observer.unobserve(entry.target);
    }
  }, { rootMargin: "0px 0px -8% 0px" });

  const watched = new WeakSet();
  const scan = () => {
    for (const el of document.querySelectorAll(TARGETS)) {
      if (watched.has(el)) continue;
      watched.add(el);
      observer.observe(el);
    }
  };

  // Streamlit adds and replaces elements on every rerun.
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      scan();
    });
  }).observe(document.body, { childList: true, subtree: true });

  root.classList.add("sm-motion");
  scan();
})();
