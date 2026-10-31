// Enhance the existing tab containers, without rerunning or discarding forms.
(() => {
  window.smSidebarCleanup?.();
  const root = document.documentElement;
  const storageKey = "sensemaker.sidebar.collapsed";
  let collapsed = matchMedia("(max-width: 760px)").matches;
  try {
    const saved = sessionStorage.getItem(storageKey);
    if (saved !== null) collapsed = saved === "true";
  } catch { /* Storage can be unavailable in private/embedded browsers. */ }

  const tabs = () => {
    const list = document.querySelector('.st-key-sm_views [role="tablist"]');
    list?.classList.add("sm-primary-tab-list");
    return list ? [...list.querySelectorAll('[role="tab"]')] : [];
  };
  const buttons = () => [...document.querySelectorAll("[data-sm-view]")];
  const sync = () => {
    const nav = document.querySelector(".sm-sidebar");
    const viewTabs = tabs();
    const ready = !!nav && viewTabs.length > 0 && viewTabs.length === buttons().length;
    root.classList.toggle("sm-navigation-ready", ready);
    root.classList.toggle("sm-sidebar-collapsed", ready && collapsed);
    if (!ready) return;

    const toggle = nav.querySelector(".sm-sidebar-toggle");
    const action = collapsed ? "Expand sidebar" : "Collapse sidebar";
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-label", action);
    toggle.title = action;
    for (const button of buttons()) {
      const tab = viewTabs.find((item) => item.textContent.trim() === button.dataset.smView);
      if (!tab) continue;
      button.setAttribute("aria-current", tab.getAttribute("aria-selected") === "true" ? "page" : "false");
      button.setAttribute("aria-controls", tab.getAttribute("aria-controls"));
    }
  };

  const click = (event) => {
    const button = event.target.closest(".sm-rail-button");
    if (!button) return;
    if (button.matches(".sm-sidebar-toggle")) {
      collapsed = !collapsed;
      try { sessionStorage.setItem(storageKey, String(collapsed)); } catch { /* Optional preference. */ }
      sync();
    } else if (button.dataset.smView) {
      const tab = tabs().find((item) => item.textContent.trim() === button.dataset.smView);
      tab?.click();
      // The hidden native tab must not retain focus after keyboard navigation.
      button.focus({ preventScroll: true });
      document.querySelector('[data-testid="stMain"]')?.scrollTo({ top: 0 });
    } else if (button.matches(".sm-rail-sign-out")) {
      document.querySelector('.st-key-sensemaker_sign_out button')?.click();
    }
  };

  const keydown = (event) => {
    if (event.key === "Escape" && !collapsed) {
      collapsed = true;
      try { sessionStorage.setItem(storageKey, "true"); } catch { /* Optional preference. */ }
      sync();
      document.querySelector(".sm-sidebar-toggle")?.focus({ preventScroll: true });
    }
    if (!event.target.matches("[data-sm-view]")) return;
    const items = buttons();
    let index = items.indexOf(event.target);
    if (event.key === "ArrowDown") index = (index + 1) % items.length;
    else if (event.key === "ArrowUp") index = (index + items.length - 1) % items.length;
    else if (event.key === "Home") index = 0;
    else if (event.key === "End") index = items.length - 1;
    else return;
    event.preventDefault();
    items[index].focus();
  };

  let frame = 0;
  const observer = new MutationObserver(() => {
    if (!frame) frame = requestAnimationFrame(() => { frame = 0; sync(); });
  });
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-selected"] });
  document.addEventListener("click", click);
  document.addEventListener("keydown", keydown);
  sync();
  window.smSidebarCleanup = () => {
    observer.disconnect();
    cancelAnimationFrame(frame);
    document.removeEventListener("click", click);
    document.removeEventListener("keydown", keydown);
  };
})();
