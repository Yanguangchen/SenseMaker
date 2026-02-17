# SenseMaker (Project Sentinel MVP)

Social listening prototype for monitoring migrant worker well-being content from social platforms.

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
You can also do this directly from the dashboard via the **Capture Facebook Login Session** button.

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
- For this Python backend, use Firebase Admin credentials (`FIREBASE_CREDENTIALS`). The Firebase Web SDK config is not required.
- `GEMINI_MODEL` defaults to `models/gemini-2.0-flash` if not set.

## Run the Dashboard (Local)

```powershell
.\.venv\Scripts\python -m streamlit run dashboard.py
```

The dashboard has **two tabs**:

### Tab 1 — Scrap data

- Facebook auth session capture (opens Chrome for manual login)
- Paste up to 5 URLs (one per line) to scrape in parallel
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

2) In dashboard, open **log into facebook** and click **Capture Facebook Login Session**.
3) Log into Facebook in the opened Chrome window and wait for capture completion.
4) In scraper panel, set:
   - target Facebook URL(s) — up to 5
   - `Headless Browser` as needed
   - `Scroll Iterations` between `10` and `30` for feed-heavy pages
5) Click **Run Scrape Test** and inspect results.
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
- parallel multi-URL scraping (up to 5 concurrent)

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
