# SenseMaker (Project Sentinel MVP)

Social listening prototype for monitoring migrant worker well-being content from social platforms.

The dashboard requires Google sign-in. Access is limited to verified accounts in
`FAQ_EDITOR_EMAILS`, using the same Firebase project and approved-user list as OYOSHI.

**Core pipeline:** Scrape social media posts -> Store in Firestore -> Analyse with Gemini or ChatGPT -> Review sentiment, risk scores & translations in a visual dashboard.

## AI provider setup

Configure credentials once on the server in `.env.local` or `.env`; dashboard users choose **Gemini**
or **ChatGPT** in the **AI provider** dropdown without entering an API key.

```env
AI_PROVIDER=gemini
GEMINI_KEY=your_gemini_api_key_here
GEMINI_MODEL=auto
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-5.4-mini
```

Gemini is the default. Set `AI_PROVIDER=openai` to make ChatGPT the initial selection
and the provider used by `main.py`. `GEMINI_API_KEY` is also accepted when `GEMINI_KEY`
is unset. A provider without a credential cannot run analysis; the user can still
switch to the other provider. `.env.local` takes precedence over `.env`; non-empty
server environment variables take precedence over both files. Empty environment
values are filled from these files on rerun. Restart the app when changing an
already-loaded key or other setting.

For hosted deployments, set the same variables in Vercel environment settings or
top-level Streamlit Secrets. Keys stay on the server and are never populated into
page widgets. Both providers use the same batching, evidence validation and retry
logic; saved analyses include `provider` and `model`.

ChatGPT uses the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/text)
with [JSON output](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
and response storage disabled. `OPENAI_MODEL` defaults to
[`gpt-5.4-mini`](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
and can be changed to a Responses-compatible text model with JSON mode support.

## Current Project Structure

```text
SenseMaker/
├── .env.example
├── .gitignore
├── requirements.txt
├── main.py
├── dashboard.py              # Streamlit UI (collection + analysis tabs)
├── .streamlit/
│   ├── config.toml           # Streamlit theme & server config
│   └── secrets.toml.example  # Template for Streamlit Cloud secrets
├── scripts/
│   └── save_storage_state.py
└── modules/
    ├── __init__.py
    ├── database.py            # Firestore helpers (upsert, query, mark processed)
    ├── scraper.py             # Playwright web scraper
    ├── google_news.py         # Google News searches and boycott/border-tension keyword scan
    ├── news_risk.py           # Saved news scans and border-tension risk charts
    ├── openai.py              # OpenAI Responses API transport
    └── processor.py           # Shared Gemini/ChatGPT analysis with retry & backoff
```

---

## Thailand tensions and boycott watch

The **Sentiment Analysis** tab now assesses Thailand-related discussion in English,
Khmer and Thai. Enter Thai-linked project, company or brand names and their aliases
(one per line), or leave the watchlist empty for Thailand generally. Each analysis
stores the watchlist used, model name and schema version. Existing records can be
selected and reprocessed to add these fields.

Findings distinguish negative sentiment, reported tensions, explicit boycott calls,
reporting of boycotts, opposition to boycotts, no detected signal and uncertainty.
Named projects/brands are distinguished from general discussion of Thailand.
Every signal needs a verbatim supporting quote; missing/invalid evidence or an
unestablished Thailand connection downgrades the result to **Needs review**.
The original language, quote source, source link and subjective confidence are shown.
Filters, sample counts and CSV export make findings reviewable. The existing risk
field is displayed as **review priority**, not a judgment about people.

These are model assessments, not confirmed events. Quote validation cannot prove
that an interpretation is correct. Records are processed in batches of eight;
unknown/duplicate response IDs are rejected, and successful batches survive a later
failure. Analysis has a 180-second request budget to leave time for saving results
within the hosted runtime; unprocessed records can be retried using the error filter.
Long records are excerpted and visibly marked. No individual identities,
nationalities or political affiliations are inferred. Counts refer to sampled records,
not unique people or population sentiment. Live classification quality still needs
evaluation with your configured provider and representative, human-labeled data.

## Delete processed records

In **Sentiment Analysis**, click **Show history**, open **Delete processed records**,
select records, and confirm their permanent deletion. This removes the selected records
and analyses from the shared library for all users. Records that are no longer processed
are kept. Source links stay available for future collection.

## Reddit collection: r/cambodia

1. Open **Scrape Data → Reddit** to collect directly from **r/cambodia**.
2. Check the connection and choose up to **500 posts per source** and **0–100 sampled comments per post**.
3. Click **Collect posts**, inspect the results and save them to Firestore.
4. Select saved records in **Sentiment Analysis**, set your watchlist and run analysis.

The connector accepts r/cambodia `new`, `hot`, `top`, `search/?q=...` and full
post links. It uses Reddit's authenticated read-only Data API rather than the browser
scraper. Default collection is newest posts, with returned comments sorted by newest.
Reddit pagination, API availability and the 180-second collection budget limit coverage;
large jobs can finish partially. Rate-limit headers are respected and partial failures
are displayed. Search results and bounded comment samples are not a complete archive;
`more` comment stubs are not expanded. Collection is on demand, not a background monitor.

Each Reddit post and comment has its own canonical permalink and record. Comments
carry the original post as context; context-only calls cannot count as a comment's
own boycott advocacy. Usernames are not stored. Removed content encountered during
collection is saved as a tombstone, clearing that record's old text and analysis.
Recollecting edited text resets its analysis to pending. Records not encountered again
are not automatically refreshed or checked for deletion; this is not a retention service.

Live access requires an approved Reddit app. Configure these server settings in
`.env`, Streamlit Secrets or Vercel production Secrets:

```env
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=web:SenseMaker:1.0 (by /u/your_reddit_username)
# Optional, for an approved user-authorized app with read scope:
REDDIT_REFRESH_TOKEN=...
```

Without a refresh token the connector requests an application-only OAuth token.
Missing credentials, denied access and throttling produce a connection/error notice;
there is no unauthenticated scraping fallback. No Reddit credentials are included in
this repository. See [Reddit's access policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy),
[Data API documentation](https://www.reddit.com/dev/api/) and
[OAuth application credentials](https://github.com/reddit-archive/reddit/wiki/OAuth2#application-only-oauth).

## Google News monitoring: OYOSHI, border tension and boycotts

Open **Scrape Data → Online news** and click **Search Google News**. SenseMaker runs the same
searches as Google Search's **News** tab for three categories, each with editable search terms:

| Category | Default search |
|---|---|
| OYOSHI mentions | `OYOSHI` |
| Thailand–Cambodia border tension | `Thailand Cambodia border tension` |
| Boycott of Thai products in Cambodia | `boycott Thailand products Cambodia` |

Choose the publication window (past day, week, month or year), the English and/or Thai Google
News editions, and up to 100 articles per search. Results come from Google News' public search
feed rather than a scripted browser, so collection works on hosted deployments without
CAPTCHAs or credentials. Articles found by several searches are listed once with all their
categories. A failed search is shown as a notice and does not discard the other results.

Every headline is scanned with transparent English, Thai and Khmer keyword lists:

- **Tone**: Positive, Neutral or Negative headline wording.
- **Boycott**: boycott wording in an article that mentions Thailand or Cambodia.
- **Border tension**: disputes, troop movements, border closures or protests.
- **Escalation**: clashes, shelling, casualties, evacuations or ceasefire violations.
- **De-escalation**: ceasefires, talks, reopenings or agreements, which lower an article's tension by one level.

Each search saves one point on **Sentiment Trends → Border tension risk over time**:
`risk = 100 × Σ tension level / (2 × max(Thailand/Cambodia articles, 10))`, where escalation is
level 2 and tension level 1. The chart shades **Low** (< 20), **Elevated** (20–45) and **High**
(≥ 45) bands, marks scans that rose 10 or more points with **▲ Rising**, and shows the boycott
index as a dashed line. High, elevated or rising risk and any boycott articles raise alerts on
both pages. Scans are stored in `<FIREBASE_COLLECTION>_news_scans`. Compare scans that use the
same searches and window, because the score depends on what each search returns.

Keyword flags are leads for review, not confirmed events, and only headlines are scanned. Use
**Save data to cloud** to store the articles, then run the AI provider on them in **Sentiment
Analysis** for evidence-grounded sentiment, boycott and tension assessments. On the
**Sentiment over time** chart, red rings mark analysis runs that found border-tension or
boycott signals. Short search terms such as `OYOSHI` can also match unrelated news, so refine
the terms when needed. Google's feed is intended for personal, non-commercial use; check its
terms for your use case.

## Deploy to Vercel

The production app is [sensemaker-psi.vercel.app](https://sensemaker-psi.vercel.app).
The Vercel project is `sensemaker` in `yanguangchens-projects`. It uses Vercel's
[Container Runtime](https://vercel.com/docs/functions/runtimes/container) (beta)
to run the existing Streamlit server on port 80. `Dockerfile.vercel` installs the
tested Python versions from `requirements-vercel.txt` and Chromium for headless
web collection. Local credentials, virtual environments and Facebook sessions
are excluded from the upload and container image.

Configure the top-level Firebase settings from `.streamlit/secrets.toml.example`
as production Vercel environment variables, with `FIREBASE_CLIENT_EMAIL` and
`FIREBASE_PRIVATE_KEY` instead of the `[firebase]` table. Store credentials as
Secret variables. Add the production hostname to Firebase Authentication's
authorized domains. Configure `GEMINI_KEY` and `OPENAI_API_KEY` on the server to
enable both providers; users do not enter keys in the UI.

```sh
vercel link --project sensemaker --scope yanguangchens-projects
vercel --prod
```

Hosted collection runs headlessly. Interactive Facebook login is available only
when running SenseMaker on your own computer. Vercel's function duration and
scale-to-zero limits also apply to WebSocket sessions: reconnects restore Google
login from the browser, and long collections must fit within the hosting limits.
Saved links and collected posts persist in Firestore when a container restarts.

## Deploy to Streamlit Community Cloud

This alternative deployment supports Firestore and Gemini/ChatGPT processing. Collection
and interactive Facebook login run locally.

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
AI_PROVIDER = "gemini"
GEMINI_KEY = "your_gemini_api_key_here"
GEMINI_MODEL = "auto"
OPENAI_API_KEY = "your_openai_api_key_here"
OPENAI_MODEL = "gpt-5.4-mini"
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
| Gemini/ChatGPT processing (sentiment, risk, translation) | Yes | Yes |
| Firestore read/write | Yes | Yes |
| Show history (processed records) | Yes | Yes |
| Web scraping (Playwright) | No | Yes |
| Facebook auth capture | No | Yes |

The scraper tab will show an info message on cloud. Use local scraping to populate Firestore, then use the cloud deployment for AI analysis and viewing results.

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
After login, your Facebook name and connection status appear in the sidebar toolbar
above your Google account; the main connection card is hidden.
Saved sessions are checked automatically; expired sessions show the login button again.

## Required Environment Variables

Set these values in `.env` (local) or Streamlit Secrets (cloud):

```env
FIREBASE_CREDENTIALS=path/to/firebase-service-account.json
FIREBASE_PROJECT_ID=project-sentinel-9ab8e
FIREBASE_COLLECTION=raw_posts
GEMINI_KEY=your_gemini_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
TARGET_GROUP_URL=https://www.facebook.com/groups/example
HEADLESS=false
```

Optional:

```env
PLAYWRIGHT_STORAGE_STATE=path/to/storage_state.json
AI_PROVIDER=gemini
GEMINI_MODEL=auto
OPENAI_MODEL=gpt-5.4-mini
```

Notes:
- Use `PLAYWRIGHT_STORAGE_STATE` for authenticated scraping context (required for Facebook).
- The Python backend uses Firebase Admin credentials (`FIREBASE_CREDENTIALS`), the
  `[firebase]` Streamlit table, OYOSHI's `FIREBASE_CLIENT_EMAIL` and
  `FIREBASE_PRIVATE_KEY` environment variables, or Application Default Credentials.
  The dashboard also needs `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`,
  `FIREBASE_APP_ID` and `FAQ_EDITOR_EMAILS` for Google sign-in. Copy these values
  from OYOSHI; keep the service-account private key on the server.
- Configure credentials for each provider you want to enable; they are read only on the server.
- `GEMINI_MODEL` defaults to `auto`: Google’s model catalogue is checked using the server API key before each run.

## Run the Dashboard (Local)

```powershell
.\.venv\Scripts\python -m streamlit run dashboard.py
```

The dashboard has **three pages**, accessible from the left toolbar:

### Tab 1 — Scrap data

- Source tabs: **Facebook**, **Reddit**, and **Online news** (Google News monitoring).
- Facebook and Reddit each keep their own source selections, collection settings and results.
- Facebook auth session capture (opens Chrome for manual login)
- Save a personal library of links, available on future visits and other devices
- Select saved links, select all, remove links, or paste any number of new links
- Collect large batches with progress tracking; three sources run at a time
- Headless browser toggle + scroll iterations slider (1-50)
- Results table + raw JSON output
- "Save data to cloud" button with custom title — writes scraped data to Firestore with timestamps

### Tab 2 — Sentiment Analysis

- **Load records** by status (`all`, `pending`, `processed`, `error`)
- Choose Gemini or ChatGPT, select records and run analysis (translation, sentiment, risk score, topics)
- **Live progress panel** shows retry status when rate-limited (429 errors)
- **Visual analysis cards** after processing:
  - Color-coded sentiment badges (Anxiety=amber, Anger=red, Joy=green, Neutral=blue)
  - Color-coded risk scores (8-10=red, 5-7=amber, 1-4=green)
  - English translation, topics, original text preview, source link
- **Processed/Error metric counters**
- **Show history** to review previously analysed records

### Tab 3 — Sentiment Trends

Open **Sentiment Trends** from the left toolbar (the rising-line icon when collapsed).
**Sentiment over time** saves one dated point for each analysis run, including
reanalysis of the same records. The index is `100 × (Joy − Anger − Anxiety) / N`,
where `N` includes all successfully analyzed records, including Neutral. The
dashed line is the equally weighted mean of the saved run scores. Failed
records do not affect a score; entirely failed runs do not create a point.
Post updates and the run summary commit together in Firestore. History lives in
`<FIREBASE_COLLECTION>_analysis_runs`, survives refreshes and restarts, and keeps
aggregate summaries when source records are deleted. Tracking starts with the
next analysis; earlier run boundaries cannot be recovered from overwritten post
results. The timeline uses completion time in UTC, with details in Analysis history.

## Validated Facebook Workflow

Use this sequence for the most reliable results:

1) Start dashboard:

```powershell
.\.venv\Scripts\python -m streamlit run dashboard.py
```

2) Click **Log in to Facebook** in the connection card, if your account is not already connected.
3) Log into Facebook in the opened Chrome window and wait for capture completion.
   SenseMaker restores the saved login on future visits and after app restarts on
   this computer. It saves renewed cookies, local storage and IndexedDB after
   successful checks and collection, and keeps the same browser identity.
   A temporary connection-check failure shows **Facebook · Saved** and keeps the
   session; use **Check Facebook connection** to retry without logging in again.
   Facebook can still expire or revoke a login or require its own verification.
   Session files stay private on this computer and are excluded from Git and deployments.
4) In scraper panel, set:
   - select saved Facebook links or add new URLs, one per line
   - `Headless Browser` as needed
   - `Scroll Iterations` between `10` and `30` for feed-heavy pages
5) New valid links are saved and selected automatically when you finish editing. Click **Collect posts** and inspect results. Use **Clear** to deselect all sources and clear pasted links.
6) Enter a **Custom title** and click **Save data to cloud** to persist to Firestore.
7) Switch to the **Sentiment Analysis** tab:
   - Click **Load records** (status: `pending`)
   - Choose an AI provider, select records and click **Run Gemini on selected records** or **Run ChatGPT on selected records**
   - View sentiment cards, risk scores, and translations inline

If extraction is still sparse, raise scroll iterations and verify the target page is visible to the authenticated account used in capture.

## Run Pipeline Entry Point

```powershell
.\.venv\Scripts\python main.py
```

This performs:
1) Scrape from `TARGET_GROUP_URL`
2) Upsert into Firestore
3) Process pending items with the server's `AI_PROVIDER` (Gemini by default)

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
- fallback and error records are shown as notices, excluded from saved posts and analysis
- multi-URL scraping without a total-link cap, with at most 3 concurrent browsers

Known limitations:
- Facebook content visibility depends on account privacy/login status
- Some profiles/pages may expose limited DOM nodes for anonymous sessions
- Comments are best-effort and vary by page markup and expansion state

## Gemini Processor Notes

`modules/processor.py` features:
- **Automatic retry with exponential backoff** for 429 rate-limit errors (up to 5 retries: 2s, 4s, 8s, 16s waits)
- Simple index keys (`post_1`, `post_2`, ...) sent to Gemini instead of raw MD5 hashes — ensures reliable ID matching in responses
- Exact ID matching; unknown and duplicate IDs are rejected rather than assigned to another record
- Robust JSON extraction from Gemini responses (handles markdown fences, surrounding text)
- Live status callback for UI progress updates during retries
- Default model: `auto` (an available stable Flash model is preferred); use **Refresh available models** to choose an explicit text model returned by Google. Bare names and `models/` names are both accepted in server configuration.

Analysis output per post:
- `translation` — English translation (unchanged if already English)
- `sentiment` — one of: Anxiety, Anger, Joy, Neutral
- `risk_score` — integer 1-10 (10 = most urgent)
- `topics` — array of topic strings
- `analysis_version`, `model` — analysis provenance
- `monitor` — Thailand relevance, signal type, target kind/names, evidence, confidence, review status and watch terms

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

Signed-out users see a dedicated sign-in screen. **Continue with Google** starts
the account chooser only when clicked; opening the page never opens a popup.
The browser component remounts when Firebase web settings change so a corrected
API key is used immediately instead of retaining the old Firebase instance.

Before rendering either dashboard tab, the server verifies the Firebase ID token
(including expiry, project, revocation and disabled-account checks), requires a
verified email, and checks `FAQ_EDITOR_EMAILS`. The check runs on every interaction.
Missing configuration or an empty allowlist blocks access. Firebase persists the
Google login in this browser and restores it after refreshes, revisits and server
reconnects. The sign-in component renews tokens periodically and when a background
tab returns. Expired tokens refresh without another Google login; a temporary
verification outage blocks access and allows retry without discarding the login.
Signing out, revoked/invalid credentials or denied access clears both the browser
login and Streamlit session, including loaded records. Browser storage must be
enabled; clearing site data or using a new private-browsing session requires login.

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
  "source_type": "container_post|reddit_post|reddit_comment",
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
- Template values such as `your_firebase_web_api_key` are ignored, including
  those left in the running process or copied into `.env.local`.
- An invalid-key message refers to the **Firebase web API key**, not an AI API key.
  Correct the Firebase configuration and rerun the app; its sign-in component
  automatically reloads the new settings.
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

### Gemini analysis fails or a model is unavailable

The dashboard now displays the actual Google HTTP error and redacted diagnostic,
or a specific content-blocking, output-length, JSON/ID validation or time-budget error.
It no longer assumes that every failure is caused by unmatched record IDs.

1. Have the app administrator configure `GEMINI_KEY` on the server, then click **Refresh available models**.
2. Choose **Automatic** or an available text model and retry your selection.
3. For HTTP 400/401/403, check the key, API restrictions and project permissions.
4. For HTTP 404, refresh models; the chosen model may not support `generateContent`
   for your key/API version. For HTTP 429, check quota and billing before retrying.

Automatic selection prefers stable Flash-Lite text models for lower-latency
classification, then general Flash models, using the models returned by Google's
[`models.list` API](https://ai.google.dev/api/models). It never silently selects a
Pro model. The catalogue confirms API-method availability, not remaining quota or
support for every generation option. An explicit model must appear in that catalogue.
Model/key validation happens before saved records are changed. Successful batches
survive later failures, with diagnostics shown alongside partial results. API keys
are sent in headers and redacted from Google error messages.

Google lists Gemini 2.0 Flash as retired; consult its
[model lifecycle](https://ai.google.dev/gemini-api/docs/deprecations) for current dates.
