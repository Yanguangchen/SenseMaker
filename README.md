# CrisisRadar

Social listening workspace for monitoring Thailand-related discussion, border tensions and boycott signals.

The dashboard requires Google sign-in. Access is limited to verified accounts in
`FAQ_EDITOR_EMAILS`, using the same Firebase project and approved-user list as OYOSHI.

**Core pipeline:** Scrape social media posts -> Store in Firestore -> Analyse with Gemini or ChatGPT -> Review sentiment, risk scores & translations in a visual dashboard.

**GenCite:** Measure how often Gemini and ChatGPT mention a brand when answering customer questions,
then close the gaps with content and keyword recommendations. See [GenCite: AI share of voice](#gencite-ai-share-of-voice).

**User tutorial:** Open **Tutorial** in the dashboard sidebar for a first-analysis walkthrough,
searchable instructions for every workspace, troubleshooting and administrator setup.
Read it directly in the app: use **Contents** to jump to a chapter, **Previous chapter** /
**Next chapter** to follow the guide, or search and open a matching chapter.
The [CrisisRadar tutorial](modules/tutorial.md) is also available in this repository.

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

For hosted deployments, set the same variables in Railway Variables or
top-level Streamlit Secrets. Keys stay on the server and are never populated into
page widgets. Both providers use the same batching, evidence validation and retry
logic; saved analyses include `provider` and `model`.

### One set of keys for every AI feature

Sentiment Analysis and GenCite read the same two server credentials. GenCite needs no keys
of its own, so a deployment that already runs Sentiment Analysis can use GenCite without changes.

| Setting | Used by | Notes |
|---|---|---|
| `GEMINI_KEY` (or `GEMINI_API_KEY`) | Sentiment Analysis, GenCite | `GEMINI_API_KEY` is the GenCite web app's name; either works. |
| `OPENAI_API_KEY` | Sentiment Analysis, GenCite | Shown as ChatGPT. |
| `AI_PROVIDER` | Sentiment Analysis | Initial dropdown selection and `main.py` provider. GenCite runs every configured provider side by side. |
| `GEMINI_MODEL` | Sentiment Analysis | Default `auto`. |
| `OPENAI_MODEL` | Sentiment Analysis | Default `gpt-5.4-mini`. |
| `GENCITE_GEMINI_MODEL` | GenCite | Optional. Default `gemini-2.5-flash`; `auto` is also accepted. |
| `GENCITE_OPENAI_MODEL` | GenCite | Optional. Default `gpt-4.1-mini`. |

GenCite's model defaults match the GenCite web app, so share of voice measured in either app
uses the same models and stays comparable. Sentiment Analysis keeps its own model settings
because classification favors different models (for example Flash-Lite). If only one key is
configured, GenCite measures with that provider and generates ideas with it; with neither,
analysis is disabled and query variations fall back to built-in templates.

### Check that a key works with GenCite

A key that already runs Sentiment Analysis is the same kind of key GenCite uses, but GenCite also
needs its model (`gemini-2.5-flash` unless overridden) to be available to that key, structured JSON
answers, and enough quota for about five requests per 50-query analysis. To confirm, open
**GenCite → Check provider access → Run check** in the deployed app, or run locally:

```sh
python scripts/check_gencite_providers.py          # every configured provider
python scripts/check_gencite_providers.py gemini   # just Gemini
```

Each check sends one small, real GenCite request and reports whether the key was found, whether the
model is available to it, and whether it returned GenCite's five structured answers. Keys are never
shown. A pass cannot show remaining daily quota; quota or billing limits appear as HTTP 429 during runs.

ChatGPT uses the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/text)
with [JSON output](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
and response storage disabled. `OPENAI_MODEL` defaults to
[`gpt-5.4-mini`](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
and can be changed to a Responses-compatible text model with JSON mode support.

## GenCite: AI share of voice

Open **GenCite** from the left toolbar (the speech-bubble icon). It brings the
[GenCite](https://github.com/Yanguangchen/gencite) web app's functionality into CrisisRadar. It runs on
CrisisRadar's server with CrisisRadar's Google sign-in, approved-user list and provider keys. The
GenCite web app itself is unchanged and can keep running separately.

**Analyze**

Follow one step at a time. Later steps stay hidden until you complete the current one;
the Back buttons preserve your entries and query selections.

1. **Topic**: enter a query or topic and click **Generate query variations**. Gemini (or ChatGPT when
   only it is configured) writes 50 queries across ten search intents: direct, comparative,
   alternatives, evaluative, ranked list, discovery, pricing, use case, problem-solving and
   audience-specific. If generation fails, 50 template variations are used and the cause is shown.
2. **Queries to test**: opens after generating variations. Tick, edit or add queries (up to 50),
   then click **Continue to brand**.
3. **Brand**: enter the brand, optional aliases and competitors (up to five each), and optional
   market and region. Choose the providers and click **Run analysis**.

Each selected provider answers every query five times, in batches of ten queries with up to three
parallel requests per provider. An answer counts when it mentions the brand or an alias
(case-insensitive). Competitor mentions are counted the same way. Results show the overall share of
voice, each provider's rate, a per-query chart and table. **Run again** adds another round of answers
to the totals instead of replacing them, and shows the change. If one provider or batch fails, the
other answers still count and the failure is shown. Analysis has a 180-second budget.

**Recommendations** turns the measured per-query gaps (brand rate versus the strongest competitor)
into five content briefs and eight new keywords to test. They are ranked with GenCite's formulas:
briefs by 35% gap, 25% relevance, 20% cross-provider consistency and 10% freshness; keywords by
35% opportunity, 25% relevance, 25% novelty versus tested queries and 15% coverage type. Queue
keywords and click **Test queued keywords** to measure them. Mark briefs as published to remember
what you shipped, then run again later to see whether mentions change.

**Saved analyses** are private to each account and available on any device, unlike the web app's
browser storage. Open, rename or delete them; an opened analysis can be run again and saved as a new copy.

Counts are text mentions in sampled model answers, not source citations or measurements of live
consumer chat products, and they vary between runs. Recommendations can use every tested query
(the web app sends only the first ten).

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
    ├── client_report.py       # Client report templates (sentiment, velocity, negative post reports)
    ├── engagement.py          # Engagement counts and per-collection velocity checks
    ├── report_pdf.py          # PDF export of the client report through Chromium
    ├── scraper.py             # Playwright web scraper
    ├── reddit.py              # r/cambodia collection through Reddit's Data API
    ├── x.py                   # X recent-search collector
    ├── google_news.py         # Google News searches and boycott/border-tension keyword scan
    ├── news_risk.py           # Saved news scans and border-tension risk charts
    ├── openai.py              # OpenAI Responses API transport
    ├── processor.py           # Shared Gemini/ChatGPT analysis with retry & backoff
    ├── gencite.py             # GenCite share of voice, aggregation and recommendations
    ├── gencite_store.py       # Per-user saved GenCite analyses in Firestore
    └── gencite_ui.py          # GenCite workspace
```

---

## Thailand tensions and boycott watch

In **Sentiment Analysis → Manual selection**, check **Filter by record sources** first: Facebook,
Reddit, Online news, X, Custom website or Other saved sources. Nothing is selected automatically
in a new session. Select one or more source-colored dataset cards: all datasets saved today
appear first, using your local timezone; if none were saved today, the six latest appear.
Use **Choose older datasets or adjust selection** to search and select any saved dataset.
The cards and picker share the same selection. Only the selected sources are
included, even when a dataset contains more than one platform. Records load newest first,
with dataset/source filtering applied before the load limit.
Every dashboard save assigns a common UTC save timestamp to its records. Dataset pickers in
Sentiment Analysis and Clean & manage data show the latest saved date and time. Older records use
their collection date or Firestore creation time. Reusing a dataset title still groups records
together, and its displayed date advances when records are saved to it.
Changing sources clears loaded records, selections and previous results. The old
records remain in Firestore; they are not deleted or reclassified by language.
The sentiment timeline shows saved history from all record sources independently of
the selection for a new analysis. Earlier runs without source metadata, mixed-source
runs and saved Online News scans remain visible, including in a new browser session.

If the selected sources have no saved records yet, collect their posts before loading Sentiment Analysis.
Online news, X and Custom website save automatically; Facebook and Reddit require **Save data to cloud**. Saving source links
alone does not collect any posts. X posts collected from **Scrape Data → X** are
included with the current watches.

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

## Content recommendations: keywords and sentiment

Open **Content plan** in the left toolbar after running Sentiment Analysis
or loading processed records. Select **Latest analysis** or **Loaded processed records**,
then choose up to 30 relevant records, a brand/topic, a communication goal and an optional
focus concern. Choose English, Khmer or Thai and **Gemini** or **ChatGPT**, then click
**Generate content plan**.

The workspace reuses the server credentials and model settings from Sentiment Analysis:
`GEMINI_KEY` (or `GEMINI_API_KEY`) with `GEMINI_MODEL`, and `OPENAI_API_KEY` with
`OPENAI_MODEL`. No additional key or package is needed. Selecting a provider with no key
disables generation. Generation sends the selected text excerpts to that provider only,
with bounded retries for temporary failures; it never publishes content automatically.

Each plan contains up to five briefs with a specific primary keyword, supporting phrases,
search intent, the concern addressed, content format, title, outline, draft, facts to verify
and a measurement suggestion. The provider selects IDs from a catalog of source excerpts;
the server attaches the original text and source links, preserving Khmer, Thai and formatting.
Unknown references are rejected. Valid briefs remain available if another brief is incomplete
or duplicated, with a notice explaining what was omitted. A wholly invalid response gets
one automatic repair attempt within the request budget. Source excerpts establish source
presence, not factual truth or interpretation accuracy. Drafts
should retain verification placeholders until the relevant facts are checked.

Plans show the sentiment baseline of the selected records and rank briefs by the number
of cited Anger/Anxiety records, then total cited records. These are sample counts, not
predictions of sentiment improvement, search volume or keyword difficulty. Compare later
sentiment using the same sources and a comparable time window in **Sentiment Trends**;
other events and sampling changes can also move sentiment.

Long records are limited to 1,200 characters plus the first two comment excerpts (300
characters each). Removed content, collection errors and unanalyzed records are excluded.
Changing selected data, inputs, provider or model clears the displayed plan. Plans remain
in the current session; download the keyword CSV and full JSON plan to keep a copy.

## Manage and delete records

Open **Clean & manage data** in the sidebar to see individual dataset cards. The library loads
automatically and shows every dataset saved today in your browser's timezone (UTC when
unavailable), newest first. If none were saved today, it shows the six latest datasets.
Cards show the dataset name, record and pending counts, sources and latest save time.
Open **Find a dataset** to browse all datasets as cards, with 12 datasets per page.
Use the numbered page buttons below the cards, or **Previous** and **Next**, to browse more datasets.
Expand **Show / hide filters** to search by dataset name, page title, URL or record ID.
Collapse the panel to keep browsing filtered cards, or clear the search to show all datasets again.
Click **Open dataset** to review its records, or **Browse all records** for the shared
library. Filter by dataset (saved Custom title), source or status, and use **Search records**
to narrow the table. **Back to datasets** returns to the cards and keeps your dataset search.
**Refresh datasets** reloads changes from the cloud. Pending, processed, error, removed
and legacy records are supported.

Each record has a **Delete** button. Click it to review that record, then choose
**Permanently delete record** or **Cancel deletion** in the confirmation beside it.
The record disappears and dataset counts update after successful deletion.
Expand **View record table** for the full metadata table.

To delete several datasets together, turn on **Select datasets to delete** above the cards.
Select cards in either dataset tab; your selection carries across searches and pages.
Check the selected dataset names and full record counts, click **Review deletion of … dataset(s)**,
then **Permanently delete … dataset(s)** or **Cancel dataset deletion**. All reviewed records in
the selected datasets are included, even when a search shows only some of them. Changed, moved
and newly added records are kept. The library refreshes after deletion; incomplete operations
report confirmed progress and require a refresh before retrying.

For bulk deletion, select individual **Records to delete**, or **Select all records on this page**, then click
**Review deletion of … record(s)**. Check the preview before **Permanently delete … record(s)**,
or use **Cancel deletion**. Nothing starts selected. Each individual-record deletion covers at most 200 records.
Changing a filter,
page, search or selection invalidates the deletion preview.

To remove a whole dataset, choose its title under **Dataset**, expand **Delete an entire dataset**, click **Review dataset deletion**,
check the preview, then **Permanently delete dataset**. This includes all reviewed records under
that title across pages, sources and statuses, regardless of the table's other filters. Large
datasets are deleted in batches under one confirmation. Records changed or added since loading
are kept. The library refreshes afterward, and an empty dataset disappears from the list. If a
later batch fails, the result reports confirmed deletions and asks you to refresh before retrying.

Deletion removes the source record and its saved analysis for all approved users. The server
rechecks each record's Firestore update version in a transaction and keeps records changed
since loading; refresh to review those again. Timeline summaries, saved source links,
GenCite analyses and exported files remain. Deleted records are removed from the current
session's analysis and collection copies. Collecting and saving a URL again can create it anew;
an analysis already in flight cannot recreate a deleted source record.

The management catalog loads metadata only and does not require an AI API key.
It uses the same approved-user access as the other workspaces; the cloud library is shared.

In **Sentiment Analysis**, load processed records, open **Delete processed records**,
select records, and confirm their permanent deletion. This removes the selected records
and analyses from the shared library for all users. Records that are no longer processed
are kept. Source links stay available for future collection.

## Custom website collection

Open **Scrape Data → Custom website** and paste ordinary website links, one per
line. News articles, blogs and company pages are supported. This tab requires no
additional API key or Facebook connection. It reads only the URLs you supply;
it does not crawl the site's other pages. Social media URLs are rejected so they
remain in their existing platform collectors.

Click **Collect website pages**. Collected pages save automatically under a name generated from the source and collection time. Wait for the cloud-save confirmation.
To reuse a URL list, enter a **New preset name** under **URL presets** and click **Save preset**.
Presets are private to your account and available after signing in on another device.
Choose a **Saved URL preset** and click **Load preset** to replace the URL input with its links.
After editing the input, **Update preset** replaces the selected preset's URLs; **Save preset**
with a different name creates another list. **Delete preset** requires confirmation and removes
only the saved list. **Refresh presets** loads changes made on another device. Presets support
up to 100 unique links; saving or loading one does not collect pages or run analysis.
To rename a saved dataset, open it in **Clean & manage data**, expand **Rename dataset**, and use
**Dataset title → Save dataset title**. This is the only place to enter a custom dataset title.
Renaming changes title metadata across all records in that dataset, preserving record IDs, source text,
collection dates, analysis exclusions and saved analyses. Changed or moved records are skipped and reported.
Reusing an existing title groups the renamed records with that dataset. If collection saving fails, use
**Retry saving collected records** before collecting again.
Saved pages appear in **Sentiment Analysis** and **Clean & manage data** alongside the other sources. Analyze them with your
existing Gemini/OpenAI key, then use those results in **Content recommendations**.
Recollecting a changed page refreshes its text and resets its old analysis;
unchanged pages keep their analysis. Website pages have no engagement checks.

Collection supports public HTML and plain-text pages, preferring `<main>` or
`<article>` content and excluding common navigation, scripts and hidden elements.
It stores the title, final URL, submitted source URL, publisher hostname, collection
time and an article publication timestamp when supplied. Extraction is best effort;
review the text before analysis, particularly on pages with unusual layouts.

Limits: 100 unique links per run, with up to 5 pages collected concurrently, 2 MB per page,
60,000 extracted characters per page, up to 5 redirects, a 30-second page budget and
a 10-minute batch budget (individual network operations also have timeouts).
Long text is explicitly marked as truncated; AI analysis has its
own existing excerpt limits. Results from completed pages remain available when
another page fails. Different links redirecting to the same final URL are deduplicated.
Private/local network destinations are blocked and DNS addresses are validated and
pinned for every request, including redirects. Only standard HTTP/HTTPS ports are
allowed; existing browser sessions and credentials are not reused.

This collector does not execute JavaScript, log in, solve CAPTCHA challenges,
download PDFs/media or transcribe audio. Inaccessible pages produce a notice rather
than a fallback record. No new service needs to be configured in Railway, but the
server must have network access to the websites users choose.

## Reddit collection: r/cambodia

1. Open **Scrape Data → Other sources → Reddit** to collect directly from **r/cambodia**.
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
`.env`, Streamlit Secrets or Railway Variables:

```env
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=web:CrisisRadar:1.0 (by /u/your_reddit_username)
# Optional, for an approved user-authorized app with read scope:
REDDIT_REFRESH_TOKEN=...
```

Without a refresh token the connector requests an application-only OAuth token.
Missing credentials, denied access and throttling produce a connection/error notice;
there is no unauthenticated scraping fallback. No Reddit credentials are included in
this repository. See [Reddit's access policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy),
[Data API documentation](https://www.reddit.com/dev/api/) and
[OAuth application credentials](https://github.com/reddit-archive/reddit/wiki/OAuth2#application-only-oauth).

## X collection: recent public posts

1. Open **Scrape Data → X** to open the focused collection window; **Start X collection** also opens it. The surrounding page dims and blurs. The two-step flow starts with collection options; dataset naming is automatic.
2. Choose your searches and edit their terms. The same three watches as Online News are filled in. Choose up to **100 posts per search** and leave **Replies per post** at 0 unless you want a small reply sample. Review the estimated reads and cost, then click **Continue to collect**.
3. The final step shows your collection summary, with **Collect posts** in its own action area. Click it to start collection. Posts and collected replies save automatically under a source-and-time name, then the window closes to show the results and cloud-save confirmation. A failed save keeps the window open with a retry action.
4. Use **Back to options** to revise your choices. Close the window with Escape, the close button or a click outside; **Resume X collection** restores your options and current step within the session. Navigating between steps never starts collection. Failed saves can still be retried from any step.
5. Select saved records in **Sentiment Analysis**, set your watchlist and run analysis.

The connector uses X's official recent-search API (`GET /2/tweets/search/recent`), which covers about
the last 7 days. It does not drive a browser. Reposts are skipped, and the main search also skips
replies unless you ask for a reply sample. Each post and reply has its own canonical
`https://x.com/i/status/…` record, so the author name is not stored. Replies carry the post they
answer as context; context-only text cannot count as the reply's own boycott advocacy. A withheld
post with no text is saved as a tombstone. Recollecting edited text resets its analysis to pending.
X's reply count is stored for velocity tracking; view counts are not stored.

X bills about **$0.005 per post returned**. The tab shows the maximum reads and estimated cost
before collection. The default is 10 posts for each of the three searches and no replies, about
$0.15 if every search fills its cap. A failed or rate-limited search is shown as a notice and does
not discard posts already collected. Collection stops at a 180-second budget. It is on demand, not
a background monitor, and it runs on hosted deployments because it never launches Playwright.

To connect X:

1. Sign in to the [X Developer Console](https://console.x.com/) and open your app under **Apps**.
2. Copy the app-only **Bearer Token** from its credentials page. The collector uses this token
   for read-only public search; it does not need an API key/secret, user access token or OAuth callback.
3. Confirm that the account has API credits and access to recent search.
4. For local runs, set `X_BEARER_TOKEN` in `.env.local` (or `.env`). For production, set it in
   **Railway → SenseMaker → sensemaker → Variables**, then deploy/restart the service.
   Streamlit Secrets also works. Keep the value out of `.env.example`, which is a template and
   is not loaded as runtime configuration.
5. Open **Scrape Data → X**, name the dataset and continue to options. Select one watch,
   keep **Posts per search** at 10 and **Replies per post** at 0, then continue to the final
   **Collect posts** step to verify access with a small request. Successful results save automatically
   to Firestore and can be analyzed alongside the other sources.

The only X environment variable read by the app is:

```env
X_BEARER_TOKEN=...
```

Missing credentials, a denied token or exhausted post-read credit produce a connection notice.
There is no unauthenticated scraping fallback. No X token is included in this repository.
See [X API recent search](https://docs.x.com/x-api/posts/search/quickstart/recent-search)
and [X developer access and credentials](https://docs.x.com/x-api/getting-started/getting-access).

## Google News monitoring: OYOSHI, border tension and boycotts

Open **Scrape Data → Online news** and click **Search Google News**. CrisisRadar runs the same
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

Online news automatically saves the collected article records as well as the separate trend snapshot.
Dataset names always use the source and collection time during collection. Custom titles are available only in **Clean & manage data → Rename dataset**.
Keyword flags are leads for review, not confirmed events, and only headlines are scanned. After the
cloud-save confirmation, select the articles in **Sentiment Analysis** for evidence-grounded
sentiment, boycott and tension assessments. Recollected articles update their title and metadata;
unchanged headlines keep their analysis, while changed text resets analysis to pending. On the
**Sentiment over time** chart, each search's headline tone appears as an **Online News**
point, and red rings mark searches and analysis runs that found border-tension or boycott signals. Short search terms such as `OYOSHI` can also match unrelated news, so refine
the terms when needed. Google's feed is intended for personal, non-commercial use; check its
terms for your use case.

## Deploy to Railway

Production: **https://sensemaker-production.up.railway.app/**

Railway is the container host for CrisisRadar. The root `Dockerfile` installs the
tested Python versions from `requirements-hosted.txt` and Chromium for headless
collection. `hosted.py` runs Streamlit on `0.0.0.0:$PORT` (8080 locally), registers
Google sign-in assets before accepting requests, and starts the automatic daily velocity
worker so cards are still checked on a replica nobody has visited. `.railway/railway.ts` selects the Dockerfile
builder, checks `/_stcore/health`, restarts failed processes, and keeps one replica awake
for Streamlit's WebSocket sessions. Local credentials, virtual environments and Facebook
sessions are excluded by `.railwayignore`, `.dockerignore` and explicit Dockerfile copies.

1. Create or select a Railway project and a service named `sensemaker`.
2. In the service's **Variables**, configure the top-level values from `.env.example`.
   Keep the existing Firebase project, collection and `FAQ_EDITOR_EMAILS` allowlist.
   Use `FIREBASE_CLIENT_EMAIL` and `FIREBASE_PRIVATE_KEY` for server credentials;
   leave `FIREBASE_CREDENTIALS` unset because local credential files are not uploaded.
   The private key accepts real newlines or literal `\n` escapes.
3. Set `GEMINI_KEY`, `OPENAI_API_KEY`, any configured Reddit credentials, `X_BEARER_TOKEN`, and
   `BROWSERBASE_API_KEY`. These are runtime variables, not Docker build arguments.
4. Install the infrastructure SDK, link the CLI to the project, review and apply the
   infrastructure plan using the commands below. Railway builds `Yanguangchen/SenseMaker`
   from `main`, with the repository root as its source directory. Commit and push the
   root `Dockerfile`, `hosted.py`, hosted requirements and application changes before deploying.
5. Generate a Railway domain under **Settings → Networking**. Add its hostname
   (without `https://` or a path) to Firebase Authentication's **Authorized domains**.
   Keep `FIREBASE_AUTH_DOMAIN` pointing at the existing Firebase auth domain.
6. Verify the health endpoint, Google sign-in with an approved account, and collection.

```sh
npx @railway/cli login
npm ci --prefix .railway
npx @railway/cli link --project 3559f5bf-b605-4dc5-bc1f-e5cdbdc79b4e --environment production
npx @railway/cli config plan
npx @railway/cli config apply
npx @railway/cli domain --service sensemaker
```

For a local container smoke check with Docker installed:

```sh
docker build -t sensemaker .
docker run --rm -p 8080:8080 -e PORT=8080 sensemaker
curl --fail http://localhost:8080/_stcore/health
```

The local smoke check needs no credentials; configure runtime variables to use the app.
Production runs in the [SenseMaker Railway project](https://railway.com/project/3559f5bf-b605-4dc5-bc1f-e5cdbdc79b4e),
on the `sensemaker` service in `production`. Pushing to GitHub's `main` branch triggers
a Railway deployment. The Git archive contains application source only; local virtual
environments are ignored and are not tracked. A manual `railway up --service sensemaker
--environment production` upload does not commit files to GitHub, so it cannot fix a
missing-file error in a subsequent GitHub build.
Railway settings use [Infrastructure as Code](https://docs.railway.com/infrastructure-as-code),
applied explicitly through the CLI; Railway does not apply `.railway/` during an app upload.
New services cannot use the retired `railway.json`/`railway.toml` format.
Credentials remain managed in Railway Variables and are preserved by infrastructure changes.
Only run this project's infrastructure plan against the dedicated SenseMaker project.

Hosted Facebook login uses Browserbase. Set `BROWSERBASE_API_KEY` in Railway Variables;
Browserbase resolves the project automatically from the key. Marketplace-provided
settings from the previous host are not copied automatically. **Log in to Facebook** opens an interactive browser in
the app; finish Facebook's sign-in and any verification there, then click
**Save Facebook login**. The viewer stays open if verification is incomplete.
After saving, the viewer closes and the app confirms that login is saved.
Each approved Google account has its own persistent Browserbase
profile; Firestore stores only the profile ID and connection metadata in
`sensemaker_facebook_profiles/{owner_hash}`. Browser recordings and session logs
are disabled. Login windows close after about three minutes or on cancellation.
Facebook tasks using the same profile run one at a time. Provider plan
usage limits apply. Local Facebook login continues to open Chrome on your computer.

A Browserbase usage bar appears above the workspaces after sign-in. It reads project browser
minutes, proxy traffic and the concurrency limit through the read-only Browserbase API, with
a shared 60-second cache, automatic refresh and a manual refresh button. Failed refreshes
label the last successful reading as stale instead of showing zero usage.
Set `BROWSERBASE_PROJECT_ID` if your key can access more than one project.
The public API does not return the plan's browser-hour allowance: set `BROWSERBASE_PLAN`
to `developer` (the default, 100 hours), `free` (1 hour), or `startup` (500 hours).
For a custom allowance, set `BROWSERBASE_BROWSER_HOURS_LIMIT`. Confirm these settings against
your [Browserbase plan](https://docs.browserbase.com/account/billing/plans) after upgrading.
The bar compares project usage with this configured allowance; paid plans can incur overages,
so the bar does not stop collections or claim to be an enforced spending cap.

Saved links, collected posts and Browserbase profile metadata remain in Firestore;
no database migration or Railway volume is needed. Container restarts can interrupt
in-progress work. Browser sign-in is restored on reconnect, while unsaved Streamlit
session state can be lost. Collection and analysis retain their existing time budgets.

### Vercel retirement

Do not deploy this application to Vercel. `vercel.json` remains only to
[disable automatic Git deployments](https://vercel.com/docs/project-configuration/git-configuration)
on every branch. The Vercel Dockerfile and dependency file have been replaced.
The old Vercel project's Git connection has been disconnected, so older branches
cannot trigger deployments. Railway is deployed, but the old Vercel project still
needs to be paused: Vercel's CLI requires a person to type the project name in an
interactive terminal and refuses automated confirmation. Run:

```sh
vercel project pause sensemaker --scope yanguangchens-projects
```

Type `sensemaker` when prompted. Pausing stops production traffic and function usage;
it retains the old deployments. The old `sensemaker-psi.vercel.app` hostname belongs
to Vercel and does not become the Railway URL. Use the Railway production link above.

## Deploy to Streamlit Community Cloud

This alternative deployment supports Firestore and Gemini/ChatGPT processing.
Hosted Facebook login and collection require the Browserbase settings described above.

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
sign-in provider in Firebase Authentication and add CrisisRadar's deployed hostname
and `localhost` to **Authentication > Settings > Authorized domains**, without a
scheme or port. Existing OYOSHI Google accounts can then sign in to CrisisRadar.

### 4) What works on Streamlit Cloud

| Feature | Cloud | Local |
|---|---|---|
| Gemini/ChatGPT processing (sentiment, risk, translation) | Yes | Yes |
| Firestore read/write | Yes | Yes |
| View processed records | Yes | Yes |
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
GENCITE_GEMINI_MODEL=gemini-2.5-flash
GENCITE_OPENAI_MODEL=gpt-4.1-mini
VELOCITY_DAILY_CHECKS=1
VELOCITY_DAILY_HOUR=2
VELOCITY_DAILY_TIMEZONE=UTC
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
- Velocity cards are checked automatically once a day. `VELOCITY_DAILY_CHECKS=0` turns that off;
  `VELOCITY_DAILY_HOUR` and `VELOCITY_DAILY_TIMEZONE` move the daily time from 02:00 UTC.
- Notifications for those checks use the browser's own Notification API and need no settings at all.

## Run the Dashboard (Local)

```powershell
.\.venv\Scripts\python -m streamlit run dashboard.py
```

The dashboard has **four pages**, accessible from the left toolbar:

### Tab 1 — Scrap data

- Choose one of five source cards: **Facebook** (blue), **Oyoshi on Facebook** (blue), **X** (black),
  **Online news** (teal, Google News monitoring), or **Custom website** (purple, public page links).
  Facebook and X use their brand logos; news and websites use dedicated icons.
  **Reddit** is available under **Other sources**, with its orange brand logo.
  Each card opens its collection flow. **All sources** returns to the cards while keeping
  settings and collected results in the current session.
- **Oyoshi on Facebook** opens the configured direct search for `oyoshi`, including its saved filters, reusing your Facebook login
  and collection results. It skips saved-link selection and defaults to 3 scroll iterations,
  adjustable between 2 and 5. This preset has a strict scroll limit without the normal extra adaptive cycles.
  You can replace its search URL with a filtered Facebook search link; those filters are preserved.
- Facebook and Reddit each keep their own source selections, collection settings and results.
- Facebook auth session capture (opens Chrome for manual login)
- Save a personal library of links, available on future visits and other devices
- Open the **Saved Facebook links** tab beside **Collect data** in **Scrape Data** to inspect your
  entire personal library, search URLs, open them in Facebook, refresh, or download the full list.
  Tick **Delete** beside unwanted URLs and click **Delete … selected links** to remove them
  from your library and pending scrape selection. Previously collected posts and analyses are kept.
  This view does not require a Facebook connection or start collection. The same list appears
  under **Your links → Inspect all saved Facebook links** when setting up a Facebook collection.
- Select saved links, select all, remove links, or paste any number of new links
- Collect large batches with progress tracking; three sources run at a time
- Headless browser toggle + scroll iterations slider (1-50)
- Results table + raw JSON output
- Facebook extraction matches explicit message bodies to their post links, including nested or unmarked
  post wrappers and short posts. Messenger/chat panels,
  navigation, sidebars, author/action labels and link-only items are excluded; unreadable posts produce
  a collection notice instead of a whole-page text fallback. Velocity comment collection uses the same chat exclusions.
- Online news, X and Custom website automatically save collected records. Facebook and Reddit use
  **Save data to cloud**. All collectors use automatic source-and-time names; custom titles are available
  only in **Clean & manage data → Rename dataset**.
- Failed automatic saves retain collected data and show **Retry saving collected records**. New collection is
  paused until saving succeeds. Retrying reuses the same records/title without recollecting or duplicating URL-based records.
  Keep the session open until the cloud-save confirmation; failed saves are not durable across a session reset.

### Tab 2 — Sentiment Analysis

The **Analysis** subtab opens by default. **Start sentiment analysis** opens a focused two-step dialog: **Choose and edit data**, then **Analyze**. Settings remain on their own subtab.

- Choose **Manual selection** or **Last scraped data**. In Manual selection, check **Filter by record sources**, then select one or more source-colored dataset cards. All datasets saved today appear first; when there are none, the six latest appear. **Choose older datasets or adjust selection** opens a searchable picker for the full source-filtered library. Dates use your local timezone, and cards show sources and record counts. Nothing is preselected in a new session; click a selected card again or remove its picker entry to deselect it.
- Choosing datasets immediately opens all matching records, newest first. The separate load button and 50/200-record load cap are removed. **Record status** in Settings defaults to **all** and filters the editor and analysis run.
- Edit **Include** directly in the table. Records start included unless previously excluded. Changes automatically save `analysis_excluded` on the existing records, without changing text, analysis or dataset titles. Exclusions are shared with the dataset and reversible by checking Include again. Save failures block analysis and offer a retry.
- **Last scraped data** uses the exact IDs from the latest completed collection, even if its title was reused. Unsaved collections save before opening. Empty/failed collections never fall back to an older batch. Use Manual selection after reopening the app.
- **Refresh datasets** refreshes the list and records. Changes to dataset, sources, status or selection mode clear stale views and previous results; saved exclusions remain.
- In **Analyze**, enter watch terms and review the exact included count before clicking **Run Gemini** or **Run ChatGPT**. No AI request starts automatically. Excluded records are not sent.
- Saved analyses for included records appear automatically under **View processed records**. The timeline remains scoped to sources.
- **Live progress panel** shows retry status when rate-limited (429 errors)
- **Visual analysis cards** after processing:
  - Color-coded sentiment badges (Anxiety=amber, Anger=red, Joy=green, Neutral=blue)
  - Color-coded risk scores (8-10=red, 5-7=amber, 1-4=green)
  - English translation, topics, original text preview, source link
- **Processed/Error metric counters**
- **View processed records** section to review previously analysed data from Firestore
- Results and processed records open in the **client report templates** (see below);
  the evidence cards above are under **Evidence by record**

#### Client report templates

Analysis results use the layout of the client's appendix templates, auto-filled from the records:

| Tab | Filled from |
|---|---|
| **Comment Sentiment Analysis** | Negative (Anger + Anxiety), Neutral and Positive (Joy) shares; tone from the dominant emotion and topics; key insight, engagement quality, and threat level (High: review priority 8+ or an explicit boycott call; Medium: any Thailand signal or priority 5+) with the main risk |
| **Word cloud** | Floating, draggable words from all analyzed records, including positive and neutral posts when there are no negative posts. Select a word for exact counts and source excerpts. Filter sentiment, switch English translations/original text, hide words, pause motion, or download the frequency CSV. Respects reduced-motion preferences. |
| **Negative Post Reports** | One NPR per negative or flagged record (`NPR-` + the first 6 characters of its record ID): text capture, comments and views vs the prior check, sentiment across the post and its analysed comments, word cloud, and classification |

**Download report (HTML)** saves sentiment analysis and every Negative Post Report as one page to share or print.
**Create PDF** prints the same report on A4 landscape with the server's Chromium (the browser Playwright already
uses for collection), then shows **Download PDF**. Comment Sentiment Analysis and each Negative Post Report fit
one page each. Pages are numbered. Velocity tables live in their own workspace and are not included in sentiment exports.
The PDF is kept for the session until the report changes. While printing, the page may load only Google Fonts
(Inter, plus Noto Sans Khmer and Noto Sans Thai for original-language quotes); every other request is blocked.
Without Chromium (`python -m playwright install chromium`), or an installed Google Chrome, the app explains
this and the HTML report can be printed to PDF from a browser instead.

**Checks.** Every time a collection is saved to the cloud, each Facebook or Reddit post gets a check: the
platform's comment total (Reddit's thread total, or the count shown on the captured Facebook post), views,
reactions and shares when the page shows them, and the number of comments collected. Saving a post that is
already stored adds a check without changing its text or analysis. Records saved before checks existed start
with their original collection as Check 1. These collection checks support the negative-post report.
The dedicated Velocity Tracking workspace has its own likes + comments card runs, described below.

Left blank for the analyst, as on the paper template: reach (not public on these platforms), the poster's
account (not collected), time reported to the Core Team, crisis type, issue source, actions taken and the
tracker decision. Impact (High at priority 8+ or an explicit boycott call) and check cadence are
pre-filled and marked as AI-derived.

### Velocity Tracking

Open **Velocity Tracking** from the left toolbar and click **Create velocity tracking card**.
This immediately saves an empty card; no name or link is required. The cards are landscape paper notepads,
420 × 300 px on desktop (the Scrape Data card dimensions rotated 90 degrees). Click a card to open its
table and controls in a matching modal. Up to **10 cards per account** are saved,
and each card can hold **10 Facebook posts**, one per row. Add links inside a card whenever ready.
All approved users can see each other's existing cards and saved results. Teammates' covers are marked
**SHARED VELOCITY**. Any approved user can remove any card for everyone; collected posts and checks are kept.
The creator keeps the Run, rename, and post-editing controls. Use **Refresh cards & checks** to load another user's new cards or results. Shared cards do not
use your own ten-card allowance, and Facebook login sessions remain private.
Use **Rename card → Card name → Save name** inside the notepad to change its name.
Each notepad has **Velocity** and **Analysis** tabs. Velocity shows **Provoker's post**, the completion
date and time (UTC) of every saved run, **Status**, **Likes + comments change (%)**, and **Remark**.
Each new run appends one date/data column on the right. Existing timestamps, counts, column order,
and column widths stay fixed; scroll horizontally to see later runs. Click **Run** to read likes/reactions and comment totals using your Facebook
connection from **Scrape Data** (Browserbase when hosted). Each run saves their combined total to Firestore.
Status is **alive**, **dying**, or **dead**, calculated from engagement gains once two consecutive totals exist.
**Likes + comments change (%)** compares the latest run with the previous run: `(latest − previous) / previous × 100`.
It shows **—** when either count is missing or the previous count is zero.
A post does not need a sentiment analysis or negative classification to be tracked.

Cards without links show an empty table. Added posts have blank cells until their first run.
**Unavailable** means one or both engagement counts could not be read; **Failed** means the post could not be checked. Neither
is treated as zero. Rounded Facebook counts remain approximate. Each column
represents the same run across all rows. The full history loads from the account's Firestore run archive
when opening the card, including runs older than the eight-check card cache. Refresh failures keep already
loaded columns and offer a retry. Earlier view-count runs are retained but never compared with engagement totals.

**Analysis** shows one row per post: **Negative** (red), **Neutral** (gray), **Positive** (green),
**Remarks**, then **Word cloud**. Run collects up to **20 readable comment texts per post** from the same
browser visit and analyzes them with the selected AI provider/model. This is a sample of comments
Facebook displays, not a complete or representative census. Each text is bounded to 2,000 UTF-8 bytes.
Anger/anxiety map to negative, joy to positive, and neutral stays neutral. Counts and percentages use
only successfully analyzed comments; empty, unreadable, failed, and partial samples are labeled explicitly.
The word cloud ranks AI-extracted topics by the number of sampled comments mentioning them.
Use **Post for remark → Edit remarks** to open the notes modal, then **Save remark**. Notes survive later runs
and appear in both tables; shared cards show saved analysis and notes with creator-only editing.
The tab displays the latest run. Full samples and per-comment results remain in that run's archive;
older analysis summaries are excluded from the card document to keep it within Firestore's size limit.
Velocity comment analysis does not add records to Sentiment Analysis or reset the sentiment timeline.

To share one card outside the workspace, open your card → **Share link → Create share link**.
Copy the URL using the copy button or select **Open shared card**. Anyone holding the link can view
that card’s saved velocity history, latest analysis, post links and remarks without signing in.
The page is read-only and reloads current saved results with **Refresh results**; it cannot run checks,
edit cards, browse other cards or read raw comment samples. Only the creator can enable or revoke
sharing. **Revoke share link**, deleting the card, or removing its creator from the approved-user list
makes the link unavailable. Creating a new link after revocation does not reactivate the old URL.
Sharing uses server-side Firestore access; the existing deny-all client rules remain unchanged.

Share-link browser regression test (requires the Python dependencies, pytest and Chromium):

```sh
python -m playwright install chromium
VELOCITY_SHARING_E2E=1 python -m pytest tests/test_velocity_sharing_e2e.py -q
```

The test starts a temporary local Streamlit server and uses separate creator and anonymous
Chromium sessions, including a mobile-sized viewer. It exercises the real share controls and
public dashboard route: create/copy/open, complete history, latest analysis, fresh saved checks,
read-only isolation, revocation, replacement links, deletion, invalid links and sign-in isolation.
Google sign-in, Firestore and the Facebook/AI worker use synthetic fixtures; no production
credentials, records or services are used. This checks browser-to-app integration, not live Google
or Firestore connectivity. Browser tests are opt-in during local test runs. The
`Velocity sharing regression` GitHub Actions workflow runs them on relevant pushes and pull requests.
Failures save screenshots, Playwright traces and a server log under
`.pytest_cache/velocity-sharing-e2e/` (or `E2E_ARTIFACT_DIR`); CI uploads those diagnostics.

When a count is missing, **Why counts are unavailable** shows the saved reason and a link to the post.
Photo viewers are supported, including layouts with comment articles and links back to a parent post.
Counts belong to the requested photo; the tracker does not substitute its parent post’s totals.
If the post cannot be identified or opened, the run reports **Failed**; if it opens without both readable
engagement counts, it reports **Unavailable**. A Facebook sign-in prompt asks you to reconnect in **Scrape Data**.

Runs continue when switching pages. Use **Refresh cards & checks** to reload saved results. A failed
Firestore save offers **Retry saving check**, which saves the same results without scraping or analyzing again or
creating a duplicate check. **Manage posts → Remove post** removes a row;
**Remove card** removes the preset, including the last card or a card with an active check.
An active check finishes in the background; a failed save remains available through **Retry saving check**
on the workspace, even when no cards remain. Collected posts and check history are kept. Existing single-post cards
remain available and can accept more rows. Cards are independent of the datasets selected in Sentiment Analysis.

#### Automatic daily checks

Every card holding at least one post is checked once a day with nobody present. A worker thread
starts with the container in `hosted.py`, and with the first local dashboard run, then wakes every
five minutes and runs each account's outstanding cards through the same worker the **Run** button
uses. An automatic run therefore shows its progress in the notepad and appends the same kind of
column; its archived run records `trigger: daily` instead of `manual`.

Checks run at **02:00 UTC** by default. Set `VELOCITY_DAILY_HOUR` (0-23) and
`VELOCITY_DAILY_TIMEZONE` (an IANA name such as `Asia/Manila`) to move them, or
`VELOCITY_DAILY_CHECKS=0` to turn them off and keep **Run** manual. An unusable hour or zone falls
back to 02:00 and UTC, so a container without tzdata still runs its checks. The workspace header
states the cadence and the next scheduled time. A container that starts later in the day runs the
day's outstanding cards about half a minute after booting, so deploying after the scheduled hour
triggers a catch-up pass instead of skipping the day.

One card runs at a time per account, always in its creator's own Facebook session: a teammate's
shared card is only ever run by its creator. A manual run or a Facebook collection in progress
defers that card to a later tick rather than scraping the same posts twice, and a manual **Run**
started during an automatic check is refused with the existing message. Each card's outcome for the
scheduled day is saved in `sensemaker_velocity_schedule/{owner_hash}`, so a restart finishes the
day's remaining cards without repeating the ones already saved. A failed check is retried up to
three times a day, at least 30 minutes apart, and a failed Firestore save is retried in place
instead of blocking later cards. Checks run in the service's single replica, as
`.railway/railway.ts` pins: more replicas would each run the day's cards.

An automatic check that saves no column is reported in the dashboard: the cover reads **Daily check
failed · needs attention** and the notepad gives the reason. The usual cause is a Facebook login
that needs a person — open **Scrape Data** and reconnect Facebook. A disconnected hosted login is
detected before any browser starts, so it costs no browser minutes and does not use up the day's
retries: the outstanding checks resume within five minutes of the login being restored.

Automatic checks spend Browserbase browser minutes and AI tokens every day. One card's run is
capped at 12 minutes, so ten full cards can use over an hour of browser time daily. Compare that
with the allowance shown in the Browserbase usage bar before filling ten cards, and set
`VELOCITY_DAILY_CHECKS=0` if the monthly allowance is tight.

#### Notifications when a daily check runs

**Notify me when a daily check runs** sits under the cadence line in **Velocity Tracking**. It is
per approved account, saved in `sensemaker_velocity_alerts/{owner_hash}`, and needs no keys, no
push service and no third-party sender: delivery is the browser's own Notification API.

Each automatic card run queues one notification. Its title is the card name and its body reads the
way the table does — `1,150 likes + comments · +155.6% · 1 Viral · 1 dying · 1 dead` — with
unreadable posts counted separately (`1 of 2 posts unavailable`) rather than treated as zero, and
`saved with gaps` when a run completed only part of its posts. The percentage compares only posts
counted in both the latest and the previous run, so a missing count never invents a change. A check
that saved no column instead says `<card> · check failed` with the reason, which is usually a
Facebook login that needs reconnecting. Earlier view-count runs are never compared with engagement.

Because nothing is pushed while the app is closed, notifications wait in Firestore (the newest 30)
and are announced the next time CrisisRadar is opened — in a browser tab or in its installed app
window, which the existing `static/manifest.webmanifest` provides. Each one is shown once: the
in-page script remembers what it has announced, so a reload or a second tab does not repeat it, and
every notification is also raised as an in-app toast so it is never missed when the browser has no
permission. Click **Enable browser notifications** once and Chrome asks for permission from that
click; a check that arrived beforehand is still announced once permission is granted. Turning the
switch off clears anything waiting.

Desktop Chrome, Edge and Firefox show these notifications. Android and iOS only allow notifications
from a service worker, which Streamlit cannot serve at the site root, so on a phone the in-app toast
carries the message instead. The first open session takes the queue, so with two tabs open the
notification appears in one of them.

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
results. The timeline uses completion time in UTC, with details in **History**.

Every Online News search also adds a point to the same chart, drawn as a separate
teal **Online News** line beside the blue **AI analysis** line. Its score is
`100 × (positive − negative headlines) / headlines`, from the keyword tone scan, so
earlier saved searches appear automatically. Both kinds of point count equally in
the average, and red rings mark points with border-tension or boycott flags.

To start fresh, open **Sentiment Trends → Settings → Reset timeline**, then
**Confirm reset timeline**. This permanently removes saved analysis-run and news-scan
summaries from both timelines for everyone using the shared library. Collected records,
datasets, and per-record analysis results are kept. New analyses and news searches
start a new timeline; **Cancel** keeps the existing history.

### Tab 4 — GenCite

Measure a brand's share of voice in Gemini and ChatGPT answers, get content and keyword
recommendations, and keep saved analyses. See [GenCite: AI share of voice](#gencite-ai-share-of-voice).

## Validated Facebook Workflow

Use this sequence for the most reliable results:

1) Start dashboard:

```powershell
.\.venv\Scripts\python -m streamlit run dashboard.py
```

2) Click **Log in to Facebook** in the connection card, if your account is not already connected.
3) Log into Facebook in the opened Chrome window and wait for capture completion.
   CrisisRadar restores the saved login on future visits and after app restarts on
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
   Use direct group, page or post URLs to skip Facebook search. The collector navigates
   directly to each selected URL; it does not type into Facebook's search bar.
5) New valid links are saved and selected automatically when you finish editing. Click **Collect posts** and inspect results. Use **Clear** to deselect all sources and clear pasted links.
6) Click **Save data to cloud** to persist to Firestore with its automatic dataset name.
7) Switch to the **Sentiment Analysis** tab:
   - Click **Start sentiment analysis**. Choose **Manual selection**, sources and datasets, or **Last scraped data**. Records appear automatically.
   - Uncheck **Include** to exclude unwanted records; exclusions save automatically and are reversible.
   - Choose an AI provider, continue to **Analyze**, check the exact included count, and click **Run Gemini** or **Run ChatGPT**.
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
CrisisRadar reads and writes through the Python Firebase Admin SDK on the server,
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
same CrisisRadar dashboard access. The command-line ingestion pipeline remains a
trusted server job using its service account rather than an interactive user login.

CrisisRadar's posts use `FIREBASE_COLLECTION` (default `raw_posts`), and personal
link libraries use `sensemaker_saved_links/{owner_hash}/links/{url_hash}`.
The owner is derived from the email returned by the server's authentication check;
users see and manage only their own library. Each URL is a separate document, so
large libraries do not share one document's size limit. Writes use batches of up
to 400 documents; retrying a partially completed save does not create duplicates.
Removing saved links leaves collected posts intact. Saved GenCite analyses use
`sensemaker_gencite/{owner_hash}/analyses/{analysis_id}` and recommendations marked as published
use `sensemaker_gencite/{owner_hash}/published/{title_hash}`, with the same owner hash. OYOSHI's `faq/oyoshi` document
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
  "source_type": "container_post|reddit_post|reddit_comment|x_post|x_reply",
  "custom_title": "User-provided title",
  "saved_at": "ISO_TIMESTAMP",
  "target_url": "https://original-input-url.com/...",
  "reported_comment_count": 57,
  "checks": [
    {"at": "ISO_TIMESTAMP", "collected_comments": 12, "comments": 240, "views": 9600, "reactions": 1200, "shares": 48}
  ],
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
