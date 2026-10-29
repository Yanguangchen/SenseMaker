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

async function initialize(config) {
  if (initializing) return;
  initializing = true;
  try {
    const [{ initializeApp }, sdk] = await Promise.all([
      import("https://www.gstatic.com/firebasejs/13.1.0/firebase-app.js"),
      import("https://www.gstatic.com/firebasejs/13.1.0/firebase-auth.js"),
    ]);
    firebaseAuth = sdk.getAuth(initializeApp(config));
    // No Google session or refresh token is persisted in browser storage.
    await sdk.setPersistence(firebaseAuth, sdk.inMemoryPersistence);
    sdk.onIdTokenChanged(firebaseAuth, async (user) => {
      if (!user) return;
      try {
        const token = await user.getIdToken();
        if (token !== lastToken) {
          lastToken = token;
          publish({ id_token: token });
        }
        button.textContent = "Switch Google account";
      } catch {
        message.textContent = "Your session expired. Sign in again.";
        resize();
      }
    });
    button.textContent = "Sign in with Google";
    button.disabled = false;
    button.onclick = async () => {
      button.disabled = true;
      message.textContent = "";
      try {
        if (firebaseAuth.currentUser) {
          await sdk.signOut(firebaseAuth);
          publish({ type: "sign_out" });
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
  if (event.data?.type === "streamlit:render") initialize(event.data.args.config);
});
send("streamlit:componentReady", { apiVersion: 1 });
resize();
