# SenseMaker (Project Sentinel MVP)

Social listening prototype for monitoring migrant worker well-being content from social platforms.

Current implementation focus:
- scraper-first Streamlit testing UI
- Playwright-based scraping experiments
- Firestore ingestion scaffolding
- Gemini processing scaffolding

## Current Project Structure

```text
SenseMaker/
├── .env.example
├── .gitignore
├── requirements.txt
├── main.py
├── dashboard.py
├── scripts/
│   └── save_storage_state.py
└── modules/
    ├── __init__.py
    ├── database.py
    ├── scraper.py
    └── processor.py
```

## Environment Setup (Windows PowerShell)

1) Create and activate virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2) Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

3) Install Playwright browser binaries:

```powershell
python -m playwright install chromium
python -m playwright install chromium-headless-shell
```

4) (Optional, CLI path) capture authenticated storage state:

```powershell
.\.venv\Scripts\python scripts\save_storage_state.py
```

Then set `PLAYWRIGHT_STORAGE_STATE` in `.env` to the saved file path.
You can also do this directly from the dashboard via the **Capture Facebook Login Session** button.

5) Create `.env` from template:

```powershell
copy .env.example .env
```

## Required Environment Variables

Set these values in `.env`:

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
```

Use `PLAYWRIGHT_STORAGE_STATE` if you need authenticated scraping context.

Notes:
- For this Python backend, use Firebase Admin credentials (`FIREBASE_CREDENTIALS`) for Firestore writes.
- The Firebase Web SDK config (`apiKey`, `authDomain`, etc.) is for browser apps and is not required by this backend.

## Run the Dashboard

```powershell
.\.venv\Scripts\python -m streamlit run dashboard.py
```

Dashboard currently focuses on scraper testing:
- single-page scraper test UI
- Facebook auth session capture button
- URL input + headless toggle + scroll iterations (1-50)
- result table + raw JSON output
- error details expander for debugging failures

## Validated Facebook Workflow

Use this sequence for the most reliable results:

1) Start dashboard:

```powershell
.\.venv\Scripts\python -m streamlit run dashboard.py
```

2) In dashboard, open **Facebook Auth Session** and click **Capture Facebook Login Session**.
3) Log into Facebook in the opened Chrome window and wait for capture completion.
4) In scraper panel, set:
   - target Facebook URL
   - `Headless Browser` as needed
   - `Scroll Iterations` between `10` and `30` for feed-heavy pages
5) Run scrape and inspect:
   - table output
   - `source_type` field in raw JSON (`container_post` is preferred)

If extraction is still sparse, raise scroll iterations and verify the target page is visible to the authenticated account used in capture.

## Run Pipeline Entry Point

```powershell
.\.venv\Scripts\python main.py
```

This currently performs:
1) scrape from `TARGET_GROUP_URL`
2) upsert into Firestore
3) process pending items with Gemini

## Scraper Notes

`modules/scraper.py` supports:
- multiple Facebook container selectors
- incremental harvest while scrolling
- adaptive stop on no-growth cycles
- resilient navigation strategy (no strict networkidle dependency)
- deduplication by normalized URL
- fallback URL generation when permalink not available
- best-effort comment extraction (`comments`, `comment_count`)
- `source_type` tagging for traceability (`container_post`, `permalink_fallback`, `page_fallback`, `emergency_fallback`)
- browser launch fallback to local Chrome when managed Chromium is missing
- guaranteed non-empty output via emergency fallback records

Known limitations:
- Facebook content visibility depends on account privacy/login status
- some profiles/pages may expose limited DOM nodes for anonymous sessions
- comments are best-effort and vary by page markup and expansion state

## Firestore Schema (Target)

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
  "analysis": {
    "language": "Bengali",
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

- Set `FIREBASE_CREDENTIALS` to your service-account JSON path.
- Set `FIREBASE_PROJECT_ID=project-sentinel-9ab8e`.
- Optional: if Google ADC is configured, `FIREBASE_CREDENTIALS` can be omitted.

### Scraper returns only 0-1 posts

- increase scroll iterations in `Scraper Test`
- test with an authenticated `PLAYWRIGHT_STORAGE_STATE`
- verify target profile/page privacy settings

### `Auth capture failed: NotImplementedError()`

This was addressed by using async Playwright with a Windows-compatible event loop policy in the dashboard capture flow.  
If it still appears, restart Streamlit and retry.

### `Page.goto timeout ... wait_until "networkidle"`

Facebook often keeps background requests open indefinitely.  
The scraper now uses resilient navigation (`domcontentloaded`/`load` fallbacks), so restart Streamlit and rerun the scrape if you still see old timeout behavior.