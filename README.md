# SenseMaker (Project Sentinel MVP)

Social listening prototype for monitoring migrant worker well-being content from social platforms.

The dashboard requires Google sign-in. Access is limited to verified accounts in
`FAQ_EDITOR_EMAILS`, using the same Firebase project and approved-user list as OYOSHI.

**Core pipeline:** Scrape social media posts -> Store in Firestore -> Analyse with Gemini AI -> Review sentiment, risk scores & translations in a visual dashboard.

## Current Project Structure

```text
SenseMaker/
├── .env.example
├── .gitignore
├── requirements.txt
├── main.py
├── dashboard.py              # Streamlit UI (scraping + Gemini tabs)
├── .streamlit/
│   ├── config.toml           # Streamlit theme & server config
│   └── secrets.toml.example  # Template for Streamlit Cloud secrets
├── scripts/
│   └── save_storage_state.py
└── modules/
    ├── __init__.py
    ├── database.py            # Firestore helpers (upsert, query, mark processed)
    ├── scraper.py             # Playwright web scraper
    └── processor.py           # Gemini analysis with retry & backoff
```

---

## Deploy to Streamlit Community Cloud

The fastest way to get a live URL. Scraping is local-only; the cloud deployment supports Firestore + Gemini processing.

### 1) Push to GitHub

Make sure your repo is pushed to GitHub (it already is at `SenseMaker`).

### 2) Create a Streamlit Cloud app

1. Go to [share.streamlit.io](https://share.streamlit.io/)
2. Click **New app**
3. Select your GitHub repo, branch `main`, and set **Main file path** to `dashboard.py`
4. Click **Deploy**

### 3) Configure secrets

In the Streamlit Cloud dashboard for your app:

1. Go to **Settings > Secrets**
2. Paste the following (replace placeholder values with your real credentials):

```toml
GEMINI_KEY = "your_gemini_api_key_here"
FIREBASE_PROJECT_ID = "project-sentinel-9ab8e"
FIREBASE_COLLECTION = "raw_posts"
FIREBASE_API_KEY = "your_firebase_web_api_key"
FIREBASE_AUTH_DOMAIN = "project-sentinel-9ab8e.firebaseapp.com"
FIREBASE_APP_ID = "your_firebase_web_app_id"
FAQ_EDITOR_EMAILS = "editor1@example.com,editor2@example.com"

[firebase]
type = "service_account"
project_id = "project-sentinel-9ab8e"
private_key_id = "your_private_key_id"
private_key = "-----BEGIN RSA PRIVATE KEY-----\nYOUR_PRIVATE_KEY\n-----END RSA PRIVATE KEY-----\n"
client_email = "firebase-adminsdk-xxxxx@project-sentinel-9ab8e.iam.gserviceaccount.com"
client_id = "123456789"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/..."
```

To get these values, open your Firebase service account JSON file and copy each field into the `[firebase]` section.

See `.streamlit/secrets.toml.example` for a full template.

Copy the **complete** `FAQ_EDITOR_EMAILS` list and public Firebase web configuration
from OYOSHI into these top-level settings. Keep this list synchronized in both apps
when adding or removing access; the apps do not read each other's deployment secrets.
Use the same Firebase project for Authentication and Firestore. Enable the Google
sign-in provider in Firebase Authentication and add SenseMaker's deployed hostname
and `localhost` to **Authentication > Settings > Authorized domains**, without a
scheme or port. Existing OYOSHI Google accounts can then sign in to SenseMaker.

### 4) What works on Streamlit Cloud

| Feature | Cloud | Local |
|---|---|---|
| Gemini processing (sentiment, risk, translation) | Yes | Yes |
| Firestore read/write | Yes | Yes |
| View processed records | Yes | Yes |
| Web scraping (Playwright) | No | Yes |
| Facebook auth capture | No | Yes |

The scraper tab will show an info message on cloud. Use local scraping to populate Firestore, then use the cloud deployment for Gemini analysis and viewing results.

---

## Local Development Setup (Windows PowerShell)

### 1) Create and activate virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2) Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### 3) Install Playwright browser binaries

```powershell
python -m playwright install chromium
python -m playwright install chromium-headless-shell
```

### 4) Create `.env` from template

```powershell
copy .env.example .env
```

### 5) (Optional) Capture authenticated storage state

```powershell
.\.venv\Scripts\python scripts\save_storage_state.py
```

Then set `PLAYWRIGHT_STORAGE_STATE` in `.env` to the saved file path.
You can also do this directly from the dashboard via **Log in to Facebook**.
The connection card shows your Facebook name after login and hides the login button.
Saved sessions are checked automatically; expired sessions show the login button again.

## Required Environment Variables

Set these values in `.env` (local) or Streamlit Secrets (cloud):

```env
FIREBASE_CREDENTIALS=path/to/firebase-service-account.json
FIREBASE_PROJECT_ID=project-sentinel-9ab8e
FIREBASE_COLLECTION=raw_posts
GEMINI_KEY=your_gemini_api_key_here
TARGET_GROUP_URL=https://www.facebook.com/groups/example
HEADLESS=false
```

Optional:

```env
PLAYWRIGHT_STORAGE_STATE=path/to/storage_state.json
GEMINI_MODEL=models/gemini-2.0-flash
```

Notes:
- Use `PLAYWRIGHT_STORAGE_STATE` for authenticated scraping context (required for Facebook).
- The Python backend uses Firebase Admin credentials (`FIREBASE_CREDENTIALS`), the
  `[firebase]` Streamlit table, OYOSHI's `FIREBASE_CLIENT_EMAIL` and
  `FIREBASE_PRIVATE_KEY` environment variables, or Application Default Credentials.
  The dashboard also needs `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`,
  `FIREBASE_APP_ID` and `FAQ_EDITOR_EMAILS` for Google sign-in. Copy these values
  from OYOSHI; keep the service-account private key on the server.
- `GEMINI_MODEL` defaults to `models/gemini-2.0-flash` if not set.

## Run the Dashboard (Local)

```powershell
.\.venv\Scripts\python -m streamlit run dashboard.py
```

The dashboard has **two tabs**:

### Tab 1 — Scrap data

- Facebook auth session capture (opens Chrome for manual login)
- Save a personal library of links, available on future visits and other devices
- Select saved links, select all, remove links, or paste any number of new links
- Collect large batches with progress tracking; three sources run at a time
- Headless browser toggle + scroll iterations slider (1-50)
- Results table + raw JSON output
- "Save data to cloud" button with custom title — writes scraped data to Firestore with timestamps

### Tab 2 — Gemini processing

- Load records from Firestore by status (`all`, `pending`, `processed`, `error`)
- Select records and run Gemini analysis (translation, sentiment, risk score, topics)
- **Live progress panel** shows retry status when rate-limited (429 errors)
- **Visual analysis cards** after processing:
  - Color-coded sentiment badges (Anxiety=amber, Anger=red, Joy=green, Neutral=blue)
  - Color-coded risk scores (8-10=red, 5-7=amber, 1-4=green)
  - English translation, topics, original text preview, source link
- **Processed/Error metric counters**
- **View processed records** section to review previously analysed data from Firestore

## Validated Facebook Workflow

Use this sequence for the most reliable results:

1) Start dashboard:

```powershell
.\.venv\Scripts\python -m streamlit run dashboard.py
```

2) Click **Log in to Facebook** in the connection card, if your account is not already connected.
3) Log into Facebook in the opened Chrome window and wait for capture completion.
4) In scraper panel, set:
   - select saved Facebook links or add new URLs, one per line
   - `Headless Browser` as needed
   - `Scroll Iterations` between `10` and `30` for feed-heavy pages
5) Optionally click **Save links** to keep new URLs, then click **Collect posts** and inspect results.
6) Enter a **Custom title** and click **Save data to cloud** to persist to Firestore.
7) Switch to the **Gemini processing** tab:
   - Click **Load records from Firestore** (status: `pending`)
   - Select records and click **Run Gemini on selected records**
   - View sentiment cards, risk scores, and translations inline

If extraction is still sparse, raise scroll iterations and verify the target page is visible to the authenticated account used in capture.

## Run Pipeline Entry Point

```powershell
.\.venv\Scripts\python main.py
```

This performs:
1) Scrape from `TARGET_GROUP_URL`
2) Upsert into Firestore
3) Process pending items with Gemini

## Scraper Notes

`modules/scraper.py` supports:
- multiple Facebook container selectors
- incremental harvest while scrolling
- adaptive stop on no-growth cycles
- resilient navigation strategy (no strict `networkidle` dependency)
- deduplication by normalized URL
- fallback URL generation when permalink not available
- best-effort comment extraction (`comments`, `comment_count`)
- `source_type` tagging for traceability (`container_post`, `permalink_fallback`, `page_fallback`, `emergency_fallback`)
- browser launch fallback to local Chrome when managed Chromium is missing
- guaranteed non-empty output via emergency fallback records
- multi-URL scraping without a total-link cap, with at most 3 concurrent browsers

Known limitations:
- Facebook content visibility depends on account privacy/login status
- Some profiles/pages may expose limited DOM nodes for anonymous sessions
- Comments are best-effort and vary by page markup and expansion state

## Gemini Processor Notes

`modules/processor.py` features:
- **Automatic retry with exponential backoff** for 429 rate-limit errors (up to 5 retries: 2s, 4s, 8s, 16s waits)
- Simple index keys (`post_1`, `post_2`, ...) sent to Gemini instead of raw MD5 hashes — ensures reliable ID matching in responses
- Positional fallback matching when Gemini returns the correct count but mangles keys
- Robust JSON extraction from Gemini responses (handles markdown fences, surrounding text)
- Live status callback for UI progress updates during retries
- Default model: `models/gemini-2.0-flash` (configurable via UI or `GEMINI_MODEL` env var)

Analysis output per post:
- `translation` — English translation (unchanged if already English)
- `sentiment` — one of: Anxiety, Anger, Joy, Neutral
- `risk_score` — integer 1-10 (10 = most urgent)
- `topics` — array of topic strings

## Firestore Schema

### Shared Firebase project and access

Keep the shared database's existing deny-all Firestore rules exactly as deployed.
SenseMaker reads and writes through the Python Firebase Admin SDK on the server,
which authenticates with IAM and bypasses client security rules. The included
`firestore.rules` mirrors that shared deny-all policy. The browser uses Firebase only for
Google Authentication, never for database queries.

To publish this policy to the shared production database, deploy only the rules:

```sh
firebase deploy --only firestore:rules --project project-sentinel-9ab8e
```

`firebase.json` contains only this rules target; this command does not deploy
Firestore indexes, Hosting, Functions, or any other app.

Before rendering either dashboard tab, the server verifies the Firebase ID token
(including expiry, project, revocation and disabled-account checks), requires a
verified email, and checks `FAQ_EDITOR_EMAILS`. The check runs on every interaction.
Missing configuration or an empty allowlist blocks access. The browser keeps the
Firebase session in memory and refreshes its token while the sign-in component is
mounted. Reloading may require another sign-in. Signing out or failing verification
clears the Streamlit session, including loaded records.

The supplied Firestore rules contain **no user list**. `FAQ_EDITOR_EMAILS` is a
separate server setting, not a Firestore permission. All listed users receive the
same SenseMaker dashboard access. The command-line ingestion pipeline remains a
trusted server job using its service account rather than an interactive user login.

SenseMaker's posts use `FIREBASE_COLLECTION` (default `raw_posts`), and personal
link libraries use `sensemaker_saved_links/{owner_hash}/links/{url_hash}`.
The owner is derived from the email returned by the server's authentication check;
users see and manage only their own library. Each URL is a separate document, so
large libraries do not share one document's size limit. Writes use batches of up
to 400 documents; retrying a partially completed save does not create duplicates.
Removing saved links leaves collected posts intact. OYOSHI's `faq/oyoshi` document
is not used. This separation is enforced by the app's queries, not by the shared
service account's IAM permissions. Firestore rules remain unchanged.

Collection: `raw_posts`

```json
{
  "_id": "md5_hash_of_url",
  "url": "https://facebook.com/...",
  "scraped_at": "ISO_TIMESTAMP",
  "raw_text": "Original post text",
  "status": "pending|processed|error",
  "comments": ["comment one", "comment two"],
  "comment_count": 2,
  "source_type": "container_post|permalink_fallback|page_fallback|emergency_fallback",
  "custom_title": "User-provided title",
  "saved_at": "ISO_TIMESTAMP",
  "target_url": "https://original-input-url.com/...",
  "processed_at": "ISO_TIMESTAMP",
  "analysis": {
    "translation": "English translation",
    "sentiment": "Anxiety|Anger|Joy|Neutral",
    "risk_score": 8,
    "topics": ["Salary", "Housing"]
  }
}
```

## Troubleshooting

### `ModuleNotFoundError: No module named 'streamlit'`

Install deps in your project venv and run with venv python:

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m streamlit run dashboard.py
```

### `Executable doesn't exist ... chromium_headless_shell`

Install Playwright binaries in the same environment:

```powershell
.\.venv\Scripts\python -m playwright install chromium
.\.venv\Scripts\python -m playwright install chromium-headless-shell
```

### Firestore init errors (`FIREBASE_CREDENTIALS` missing / auth failure)

- **Local:** Set `FIREBASE_CREDENTIALS` in `.env` to your service-account JSON path.
- **Streamlit Cloud:** Paste the service account JSON fields into the `[firebase]` section of Secrets (see deployment section above).
- Set `FIREBASE_PROJECT_ID=project-sentinel-9ab8e`.
- A server `PermissionDenied` error requires checking the service account's existing
  IAM access to this project; changing client Firestore rules will not fix it.

### Google sign-in cannot complete or access is denied

- Set the four Firebase web settings and `FAQ_EDITOR_EMAILS` described above in
  `.env` locally or top-level Streamlit Secrets in the cloud.
- Enable Google sign-in and authorize the app hostname in Firebase Authentication.
- Use a verified Google account in the same complete allowlist as OYOSHI.
- Allow the Google sign-in popup and access to `www.gstatic.com`.

### Scraper returns only 0-1 posts

- Increase scroll iterations in the scraper panel
- Test with an authenticated `PLAYWRIGHT_STORAGE_STATE`
- Verify target profile/page privacy settings

### `Auth capture failed: NotImplementedError()`

Addressed by using async Playwright with a Windows-compatible event loop policy.
If it still appears, restart Streamlit and retry.

### `Page.goto timeout ... wait_until "networkidle"`

Facebook often keeps background requests open indefinitely.
The scraper uses resilient navigation (`domcontentloaded`/`load` fallbacks). Restart Streamlit and rerun.

### Gemini `429 Resource exhausted` / rate limit errors

The processor automatically retries with exponential backoff (up to 5 attempts over ~30s).
If it still fails:
- Wait a minute and retry — free-tier quotas reset quickly
- Reduce the number of records processed per batch
- Check your [Google AI Studio](https://aistudio.google.com/) quota dashboard
- Consider enabling billing for higher rate limits

### Gemini `processed=0, error=N`

This previously occurred because Gemini could not match MD5 hash IDs back to posts.
Fixed by sending simple index keys (`post_1`, `post_2`) with positional fallback matching.
If it recurs, try processing fewer records at a time.
