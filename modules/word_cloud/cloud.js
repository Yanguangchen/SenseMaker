import { packWords } from "./layout.mjs";

const cloud = document.getElementById("cloud");
const card = document.querySelector(".cloud-card");
const detail = document.getElementById("detail");
const motion = document.getElementById("motion");
const reset = document.getElementById("reset");
const wordLimit = document.getElementById("word-limit");
const hint = document.getElementById("hint");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const parentOrigin = new URL(location.href).origin;
let allTerms = [], terms = [], buttons = [], revision = null, selection = null, drag = null;
let showAll = false;
let paused = reducedMotion.matches, visible = true, hovered = false;
let layoutFrame = 0, lastWidth = 0, lastHeight = 0, seed = 0;

function send(type, extra = {}) {
  parent.postMessage({ isStreamlitMessage: true, type: `streamlit:${type}`, ...extra }, parentOrigin);
}
function frameHeight() {
  const height = Math.ceil(card.getBoundingClientRect().height) + 2;
  if (height !== lastHeight) {
    lastHeight = height;
    send("setFrameHeight", { height });
  }
}
function updateMotion() {
  const stopped = paused || reducedMotion.matches;
  card.classList.toggle("paused", stopped);
  card.classList.toggle("offscreen", !visible || document.hidden);
  card.classList.toggle("inspecting", hovered || cloud.contains(document.activeElement) || Boolean(drag));
  motion.textContent = reducedMotion.matches ? "Reduced motion on" : stopped ? "Resume motion" : "Pause motion";
  motion.setAttribute("aria-pressed", String(stopped));
  motion.disabled = reducedMotion.matches;
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function showSelection(word) {
  selection = word;
  for (const button of buttons) button.setAttribute("aria-pressed", String(button.textContent === word));
  const term = terms.find(item => item.word === word);
  detail.replaceChildren();
  if (!term) {
    detail.append(element("p", "Select any word for its frequency and source examples. You can also use Tab and Enter.", "empty"));
  } else {
    detail.append(element("h3", term.word), element("p",
      `${term.count.toLocaleString()} occurrence${term.count === 1 ? "" : "s"} across ${term.records.toLocaleString()} record${term.records === 1 ? "" : "s"}.`, "counts"));
    const examples = element("ul", undefined, "examples");
    for (const source of term.examples || []) {
      const item = element("li");
      item.append(element("div", `${source.sentiment} · ${source.kind}`, "source-meta"), element("p", source.text));
      try {
        const url = new URL(source.url);
        if (["http:", "https:"].includes(url.protocol) && !url.username) {
          const link = element("a", "Open source ↗");
          link.href = url.href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          item.append(link);
        }
      } catch { /* Records without a source URL still have useful text. */ }
      examples.append(item);
    }
    detail.append(examples);
    if (term.records > examples.children.length) {
      detail.append(element("p", `Showing ${examples.children.length} of ${term.records} matching records.`, "source-meta"));
    }
  }
  frameHeight();
}
function position(button, x, y) {
  button.dataset.x = x;
  button.dataset.y = y;
  button.style.transform = `translate(${x}px, ${y}px)`;
}
function layout() {
  layoutFrame = 0;
  const width = cloud.clientWidth;
  // Streamlit mounts inactive tabs at zero width. ResizeObserver handles activation.
  if (width < 80 || !allTerms.length) return;
  lastWidth = width;
  const compact = width < 560;
  const count = compact && !showAll ? Math.min(20, allTerms.length) : allTerms.length;
  wordLimit.hidden = !compact || allTerms.length <= 20;
  wordLimit.textContent = showAll ? "Show top 20" : `Show all ${allTerms.length}`;
  wordLimit.setAttribute("aria-expanded", String(showAll));
  hint.textContent = count < allTerms.length
    ? `Top ${count} of ${allTerms.length} words · Select a word for source text`
    : "Select a word to explore its source text";
  if (terms.length !== count) {
    mount(allTerms.slice(0, count));
    return;
  }
  drag = null;
  const max = Math.max(...terms.map(term => term.count));
  const boxes = buttons.map((button, index) => {
    button.classList.remove("dragging");
    button.style.fontSize = `${Math.round(14 + (compact ? 16 : 46) * Math.sqrt(terms[index].count / max))}px`;
    button.style.maxWidth = `${width - 24}px`;
    return { index, width: button.offsetWidth, height: button.offsetHeight };
  });
  const packed = packWords(boxes, width, width < 560 ? 460 : 430, seed);
  cloud.style.height = `${packed.height}px`;
  for (const box of packed.boxes) position(buttons[box.index], box.x, box.y);
  updateMotion();
  frameHeight();
}
function queueLayout() {
  if (!layoutFrame) layoutFrame = requestAnimationFrame(layout);
}
function mount(nextTerms) {
  terms = nextTerms;
  cloud.replaceChildren();
  buttons = terms.map((term, index) => {
    const button = element("button", term.word, "word");
    button.type = "button";
    button.setAttribute("aria-label", `${term.word}: ${term.count} occurrences in ${term.records} records. Select for source examples.`);
    button.setAttribute("aria-pressed", "false");
    button.style.setProperty("--duration", `${5 + index % 7 * 0.7}s`);
    button.style.setProperty("--delay", `${-index * 0.43}s`);
    button.addEventListener("click", () => showSelection(term.word));
    button.addEventListener("pointerdown", event => {
      if (event.button !== 0 || (drag && drag.id !== event.pointerId)) return;
      drag = { button, id: event.pointerId, x: event.clientX, y: event.clientY,
        startX: Number(button.dataset.x), startY: Number(button.dataset.y) };
      button.setPointerCapture(event.pointerId);
      button.classList.add("dragging");
      updateMotion();
    });
    button.addEventListener("pointermove", event => {
      if (drag?.button !== button || drag.id !== event.pointerId) return;
      const x = Math.max(5, Math.min(cloud.clientWidth - button.offsetWidth - 5, drag.startX + event.clientX - drag.x));
      const y = Math.max(5, Math.min(cloud.clientHeight - button.offsetHeight - 5, drag.startY + event.clientY - drag.y));
      position(button, x, y);
    });
    const endDrag = event => {
      if (drag?.button !== button || drag.id !== event.pointerId) return;
      drag = null;
      button.classList.remove("dragging");
      if (button.hasPointerCapture(event.pointerId)) button.releasePointerCapture(event.pointerId);
      updateMotion();
    };
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) button.addEventListener(name, endDrag);
    cloud.append(button);
    return button;
  });
  showSelection(terms.some(term => term.word === selection) ? selection : null);
  queueLayout();
}
motion.addEventListener("click", () => { paused = !paused; updateMotion(); });
reset.addEventListener("click", () => { seed += 0.7; queueLayout(); });
wordLimit.addEventListener("click", () => { showAll = !showAll; queueLayout(); });
cloud.addEventListener("pointerover", event => { hovered = event.target.classList.contains("word"); updateMotion(); });
cloud.addEventListener("pointerleave", () => { hovered = false; updateMotion(); });
cloud.addEventListener("focusin", updateMotion);
cloud.addEventListener("focusout", () => requestAnimationFrame(updateMotion));
reducedMotion.addEventListener("change", () => { paused = reducedMotion.matches; updateMotion(); });
document.addEventListener("visibilitychange", updateMotion);
new IntersectionObserver(entries => { visible = entries[0].isIntersecting; updateMotion(); }).observe(cloud);
new ResizeObserver(() => {
  if (cloud.clientWidth !== lastWidth) queueLayout();
  frameHeight();
}).observe(card);
document.fonts.ready.then(queueLayout);

function applyTheme(theme) {
  const dark = theme?.base === "dark";
  const colors = dark
    ? { bg: "#131d27", text: "#e3edf5", muted: "#afc0d1", line: "#344656", soft: "#1c2b39", accent: "#80d5df", selected: "#ffc274" }
    : { bg: "#fff", text: "#203348", muted: "#5c6c7c", line: "#dce5eb", soft: "#f4f8fa", accent: "#146979", selected: "#a75d04" };
  for (const [name, value] of Object.entries(colors)) document.documentElement.style.setProperty(`--${name}`, value);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}
window.addEventListener("message", event => {
  if (event.source !== parent || event.origin !== parentOrigin || event.data?.type !== "streamlit:render") return;
  clearInterval(readyTimer);
  applyTheme(event.data.theme);
  const args = event.data.args || {};
  if (args.revision !== revision) {
    revision = args.revision;
    allTerms = args.cloud?.terms || [];
    showAll = false;
    mount(allTerms);
  }
});
const readyTimer = setInterval(() => send("componentReady", { apiVersion: 1 }), 500);
send("componentReady", { apiVersion: 1 });
updateMotion();
