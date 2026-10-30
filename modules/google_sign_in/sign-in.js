// Firebase Authentication only. All database access stays on the Python server.
const button = document.getElementById("sign-in");
const message = document.getElementById("message");
const parentOrigin = new URL(document.referrer || location.href).origin;
const send = (type, extra = {}) => parent.postMessage(
  { isStreamlitMessage: true, type, ...extra }, parentOrigin,
);
const resize = () => send("streamlit:setFrameHeight", { height: document.body.scrollHeight + 4 });
const publish = (value) => send("streamlit:setComponentValue", {
  value: { ...value, event_id: crypto.randomUUID() }, dataType: "json",
});
let initializing = false;
let firebaseAuth;
let lastToken;

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
      button.textContent = user ? "Switch Google account" : "Sign in with Google";
      if (!user) {
        lastToken = undefined;
        publish({ type: "sign_out" });
        button.disabled = false;
        resize();
        return;
      }
      try {
        await refreshToken(forceRefresh);
      } catch {
        message.textContent = "Session refresh is temporarily unavailable. Retrying automatically…";
      } finally {
        button.disabled = false;
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
      button.disabled = true;
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
        };
        message.textContent = hints[error.code] || "Google sign-in did not complete. Try again.";
      } finally {
        button.disabled = false;
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
    initialize(event.data.args.config, event.data.args.clear_session);
  }
});
send("streamlit:componentReady", { apiVersion: 1 });
resize();
