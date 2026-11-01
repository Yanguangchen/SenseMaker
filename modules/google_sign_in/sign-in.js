// Firebase Authentication only. All database access stays on the Python server.
const button = document.getElementById("sign-in");
const message = document.getElementById("message");
button.textContent = "Connecting to SenseMaker…";
// This component is served by the app itself. Never send Firebase tokens to a
// different origin that happens to embed its URL.
const parentOrigin = new URL(location.href).origin;
const send = (type, extra = {}) => parent.postMessage(
  { isStreamlitMessage: true, type, ...extra },
  type === "streamlit:setComponentValue" ? parentOrigin : "*",
);
const resize = () => send("streamlit:setFrameHeight", { height: document.body.scrollHeight + 4 });
const publish = (value) => send("streamlit:setComponentValue", {
  value: { ...value, event_id: crypto.randomUUID() }, dataType: "json",
});
let initializing = false;
let firebaseAuth;
let lastToken;
let popupPending = false;
let readyTimer;

async function initialize(config, clearSession) {
  if (initializing) return;
  initializing = true;
  try {
    const [{ initializeApp }, sdk] = await Promise.all([
      import("https://www.gstatic.com/firebasejs/13.1.0/firebase-app.js"),
      import("https://www.gstatic.com/firebasejs/13.1.0/firebase-auth.js"),
    ]);
    // Let Firebase restore and refresh the session across page/container reloads.
    // Choose storage at initialization so other tabs never see a migration
    // between Firebase's default IndexedDB storage and localStorage.
    firebaseAuth = sdk.initializeAuth(initializeApp(config), {
      persistence: sdk.browserLocalPersistence,
      popupRedirectResolver: sdk.browserPopupRedirectResolver,
    });
    if (clearSession) await sdk.signOut(firebaseAuth);
    let restoring = true;
    const refreshToken = async (forceRefresh = false) => {
      const user = firebaseAuth.currentUser;
      if (!user) return;
      const token = await user.getIdToken(forceRefresh);
      // Ignore a refresh that completed after this account signed out.
      if (firebaseAuth.currentUser !== user) return;
      if (token !== lastToken) {
        lastToken = token;
        publish({ id_token: token });
      }
      message.textContent = "";
    };
    sdk.onIdTokenChanged(firebaseAuth, async (user) => {
      const forceRefresh = restoring;
      restoring = false;
      button.textContent = user ? "Switch Google account" : "Continue with Google";
      if (!user) {
        lastToken = undefined;
        publish({ type: "sign_out" });
        button.disabled = popupPending;
        resize();
        return;
      }
      try {
        await refreshToken(forceRefresh);
      } catch {
        message.textContent = "Session refresh is temporarily unavailable. Retrying automatically…";
      } finally {
        button.disabled = popupPending;
        resize();
      }
    });
    const refresh = () => refreshToken().catch(() => {
      message.textContent = "Session refresh is temporarily unavailable. Retrying automatically…";
      resize();
    });
    // Refresh before hourly expiry and when a sleeping/background tab returns.
    setInterval(refresh, 5 * 60 * 1000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") refresh();
    });
    button.onclick = async () => {
      if (popupPending) return;
      popupPending = true;
      button.disabled = true;
      button.textContent = "Opening Google…";
      message.textContent = "";
      try {
        if (firebaseAuth.currentUser) {
          await sdk.signOut(firebaseAuth);
          return;
        }
        const provider = new sdk.GoogleAuthProvider();
        provider.setCustomParameters({ prompt: "select_account" });
        await sdk.signInWithPopup(firebaseAuth, provider);
      } catch (error) {
        const hints = {
          "auth/unauthorized-domain": "This app's domain must be authorized in Firebase Authentication.",
          "auth/popup-blocked": "Allow pop-ups for this app and try again.",
          "auth/operation-not-allowed": "Google sign-in must be enabled in Firebase Authentication.",
          "auth/invalid-api-key": "Google sign-in is not configured correctly. Ask the app administrator to check the Firebase web API key.",
          "auth/api-key-not-valid.-please-pass-a-valid-api-key.": "Google sign-in is not configured correctly. Ask the app administrator to check the Firebase web API key.",
          "auth/popup-closed-by-user": "The Google sign-in window was closed. Select Continue with Google to try again.",
          "auth/cancelled-popup-request": "Another sign-in window is already open. Finish signing in there, or try again.",
          "auth/network-request-failed": "Google could not be reached. Check your connection and try again.",
          "auth/web-storage-unsupported": "Your browser could not save the sign-in session. Allow site storage for SenseMaker and try again.",
          "auth/operation-not-supported-in-this-environment": "Google sign-in is unavailable in this browser. Open SenseMaker in a regular browser window and try again.",
        };
        const code = /^auth\/[a-z.-]{1,100}$/.test(error?.code || "") ? error.code : "auth/unknown-error";
        message.textContent = hints[code] || `Google sign-in could not complete. Try again. Error: ${code}`;
      } finally {
        popupPending = false;
        button.disabled = false;
        button.textContent = firebaseAuth.currentUser ? "Switch Google account" : "Continue with Google";
        resize();
      }
    };
  } catch {
    message.textContent = "Google sign-in could not load. Reload the page to try again.";
  }
  resize();
}

window.addEventListener("message", (event) => {
  if (event.source !== parent || event.origin !== parentOrigin) return;
  if (event.data?.type === "streamlit:render") {
    clearInterval(readyTimer);
    initialize(event.data.args.config, event.data.args.clear_session);
  }
});
window.addEventListener("resize", resize);
// Cached assets can execute before Streamlit attaches the iframe's message
// listener. Retry the handshake until the first render acknowledges readiness.
const announceReady = () => send("streamlit:componentReady", { apiVersion: 1 });
announceReady();
readyTimer = setInterval(announceReady, 500);
resize();
