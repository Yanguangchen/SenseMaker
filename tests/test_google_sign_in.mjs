// Run with: node --experimental-vm-modules --test tests/test_google_sign_in.mjs
// Exercise the shipped component with Firebase/DOM doubles, without Google credentials.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../modules/google_sign_in/sign-in.js", import.meta.url), "utf8");
const settle = async () => {
  for (let i = 0; i < 4; i++) await new Promise(setImmediate);
};

async function mount(storage = {}, clearSession = false) {
  const button = { disabled: true, textContent: "" };
  const message = { textContent: "" };
  const events = [];
  const listeners = {};
  const timers = [];
  const auth = { currentUser: null };
  let observer;
  let persistence;
  let popupCount = 0;
  const sdk = {
    browserLocalPersistence: "local",
    inMemoryPersistence: "memory",
    browserPopupRedirectResolver: "popup-resolver",
    initializeAuth: (_, options) => {
      persistence = options.persistence;
      auth.currentUser = persistence === "local" ? storage.user || null : null;
      return auth;
    },
    onIdTokenChanged: (_, callback) => {
      observer = callback;
      void callback(auth.currentUser);
    },
    signOut: async () => {
      auth.currentUser = null;
      delete storage.user;
      await observer?.(null);
    },
    GoogleAuthProvider: class { setCustomParameters() {} },
    signInWithPopup: async () => {
      popupCount++;
      auth.currentUser = storage.nextUser;
      if (persistence === "local") storage.user = auth.currentUser;
      await observer(auth.currentUser);
    },
  };
  const parent = { postMessage: (data) => {
    if (data.type === "streamlit:setComponentValue") events.push(data.value);
  } };
  let id = 0;
  const context = vm.createContext({
    URL, parent, location: { href: "https://app.example/component/sign-in" },
    crypto: { randomUUID: () => `event-${++id}` },
    document: {
      referrer: "https://app.example/", body: { scrollHeight: 80 }, visibilityState: "visible",
      getElementById: (name) => name === "sign-in" ? button : message,
      addEventListener: (name, callback) => { listeners[name] = callback; },
    },
    window: { addEventListener: (name, callback) => { listeners[name] = callback; } },
    setInterval: (callback, delay) => { timers.push({ callback, delay }); },
  });
  const modules = {};
  const script = new vm.Script(source, {
    importModuleDynamically: async (url) => {
      if (!modules[url]) {
        const exports = url.endsWith("firebase-app.js") ? { initializeApp: () => ({}) } : sdk;
        const module = new vm.SyntheticModule(Object.keys(exports), function () {
          for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
        }, { context });
        await module.link(() => {});
        await module.evaluate();
        modules[url] = module;
      }
      return modules[url];
    },
  });
  script.runInContext(context);
  const render = (origin = "https://app.example") => listeners.message({
    source: parent, origin, data: { type: "streamlit:render", args: { config: {}, clear_session: clearSession } },
  });
  render();
  await settle();
  return { auth, sdk, button, message, events, listeners, timers, render,
    popupCount: () => popupCount, persistence: () => persistence,
    notify: () => observer(auth.currentUser) };
}

function user(token = "fresh-token") {
  return { calls: [], token, async getIdToken(force) { this.calls.push(force); return this.token; } };
}

test("one Google login survives multiple component/page reloads without a popup", async () => {
  const account = user();
  const storage = { nextUser: account };
  const first = await mount(storage);
  assert.equal(first.button.disabled, false);
  await first.button.onclick();
  assert.equal(first.popupCount(), 1);
  assert.equal(first.persistence(), "local");
  for (let i = 0; i < 3; i++) {
    const refreshed = await mount(storage);
    assert.equal(refreshed.events.at(-1).id_token, "fresh-token");
    assert.equal(refreshed.popupCount(), 0);
    assert.equal(account.calls.at(-1), true); // Force freshness when restoring.
    assert.equal(refreshed.button.textContent, "Switch Google account");
  }
});

test("server sign-out/denial erases persistent login before publishing a token", async () => {
  const storage = { user: user() };
  const reset = await mount(storage, true);
  assert.equal(storage.user, undefined);
  assert.equal(reset.events.length, 1);
  assert.equal(reset.events[0].type, "sign_out");
  const refreshed = await mount(storage);
  assert.equal(refreshed.events.at(-1).type, "sign_out");
  assert.equal(refreshed.button.textContent, "Sign in with Google");
});

test("account switch and cross-tab sign-out tell the server to drop its session", async () => {
  const storage = { user: user() };
  const component = await mount(storage);
  await component.button.onclick();
  assert.equal(storage.user, undefined);
  assert.equal(component.events.at(-1).type, "sign_out");
  assert.equal(component.button.textContent, "Sign in with Google");
  const other = await mount({ user: user() });
  other.auth.currentUser = null;
  await other.notify();
  assert.equal(other.events.at(-1).type, "sign_out");
});

test("token rotation, timer and foreground refresh recover without Google login", async () => {
  const account = user("old-token");
  const component = await mount({ user: account });
  account.token = "rotated-token";
  await component.notify();
  assert.equal(component.events.at(-1).id_token, "rotated-token");
  const count = component.events.length;
  await component.notify();
  assert.equal(component.events.length, count); // No rerun loop for unchanged tokens.
  account.getIdToken = async () => { throw new Error("offline"); };
  assert.equal(component.timers[0].delay, 300000);
  await component.timers[0].callback();
  assert.match(component.message.textContent, /Retrying automatically/);
  assert.equal(component.auth.currentUser, account);
  account.getIdToken = async () => "recovered-token";
  await component.listeners.focus();
  assert.equal(component.events.at(-1).id_token, "recovered-token");
  assert.equal(component.message.textContent, "");
  await component.listeners.visibilitychange();
  await settle();
  assert.equal(component.popupCount(), 0);
});

test("a pending refresh cannot publish a token after sign-out", async () => {
  const account = user();
  const component = await mount({ user: account });
  let finish;
  account.getIdToken = () => new Promise((resolve) => { finish = resolve; });
  const pending = component.listeners.focus();
  await component.sdk.signOut();
  finish("stale-token");
  await pending;
  assert.equal(component.events.at(-1).type, "sign_out");
  assert.equal(component.events.some((event) => event.id_token === "stale-token"), false);
});

test("Streamlit rerenders do not initialize Firebase or add refresh timers twice", async () => {
  const component = await mount({ user: user() });
  component.render("https://untrusted.example");
  component.render();
  await settle();
  assert.equal(component.timers.length, 1);
  assert.equal(component.events.length, 1);
});
