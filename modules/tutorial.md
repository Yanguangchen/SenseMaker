# CrisisRadar tutorial

Use CrisisRadar to collect source material, analyze sentiment and Thailand-related signals, track trends over time, and plan evidence-backed content.

## Quick start

Run your first end-to-end analysis with a public website article (no social login required):

1. **Sign in** with your approved Google account.
2. Open **Scrape Data → Custom website**, paste a public article URL into **Website links (one per line)**, and click **Next →**.
3. Optionally enter a **Dataset title**, then click **Collect website pages**. Wait for **Saved … record(s) to the cloud**.
4. Open **Sentiment Analysis** and click **Start sentiment analysis**.
5. In **Choose and edit data**, select **Last scraped data** (or **Manual selection** → choose **Custom website** and your dataset card). Keep **Include** checked for the records you want to analyze, then click **Next →**.
6. In **Analyze**, add any Thai watch terms if needed and click **Run Gemini** or **Run ChatGPT**.
7. Review the report and **Evidence by record** on the page, then open **Content plan** to generate keyword and content recommendations from **Latest analysis**.

### Workspaces at a glance

| Workspace | Purpose |
| --- | --- |
| **Scrape Data** | Collect posts, articles, and pages from Facebook, Oyoshi on Facebook, X, Online news, Custom website, or Reddit. |
| **Clean & manage data** | Browse saved datasets, filter records, and delete individual records, bulk selections, or entire datasets. |
| **Sentiment Analysis** | Select and curate datasets, run Gemini or ChatGPT analysis, inspect evidence, and export HTML/PDF/CSV reports. |
| **Sentiment Trends** | Track sentiment scores and Thailand–Cambodia border tension risk over time. |
| **Content plan** | Turn analyzed customer concerns into target keywords, content outlines, and drafts. |
| **GenCite** | Measure brand share of voice in sampled AI answers and close visibility gaps. |
| **Tutorial** | Browse or search this guide by chapter. |

- **Sidebar & theme:** Click the **CrisisRadar** header (or press `Escape`) to collapse/expand the sidebar. Use the top-right menu (**Settings**) to switch between light and dark themes, and **Sign out** in the sidebar when finished.

## Scrape Data: overview & Facebook

Open **Scrape Data** to choose from five main source cards—**Facebook**, **Oyoshi on Facebook**, **X**, **Online news**, and **Custom website**—or expand **Other sources** for **Reddit**. Click **← All sources** at any time to return to the cards without losing your current settings or results.

| Source | What it collects | Cloud saving |
| --- | --- | --- |
| **Facebook** | Posts and visible comments from your selected Facebook group, page, or post links. | Manual (**Save data to cloud**) |
| **Oyoshi on Facebook** | Direct Facebook search results for `oyoshi` (or a custom Facebook search URL) with 2–5 scrolls. | Manual (**Save data to cloud**) |
| **X** | Recent posts (last ~7 days, excluding reposts) and optional reply samples for enabled searches. | Automatic |
| **Online news** | Google News headlines and snippets across enabled watch categories and editions. | Automatic |
| **Custom website** | Main text extracted from up to 100 public web pages per run. | Automatic (plus **Save dataset** to rename) |
| **Reddit** | Newest posts and sampled comments from `r/cambodia`. | Manual (**Save data to cloud**) |

### Facebook (groups, pages & posts)

1. **Connect Facebook:** Open **Scrape Data → Facebook**. If not connected yet, click **Log in to Facebook**, complete sign-in in the browser window, and click **Save Facebook login**. If your status is **Saved**, you can use **Check Facebook connection** to verify it before collecting.
2. **Your links:** Select saved URLs in **Sources** (or **Select all**), or paste direct group, page, or post URLs into **Add links (one per line)**. Valid pasted URLs save and select automatically when you finish editing. Click **Next →**.
3. **Collect posts:** Set **Scroll Iterations** (1–50), click **Collect posts**, then enter a **Custom title** and click **Save data to cloud**.

- **Direct navigation:** The collector opens each selected Facebook URL directly rather than typing into Facebook's search bar.
- **Link management:** **Clear** deselects links and clears pasted input while keeping your saved library. **Remove … saved links** deletes selected URLs from your account library without deleting previously saved posts.
- **Background progress:** On hosted deployments, Facebook collection continues if you switch tabs. Use **Cancel collection** to stop early and keep completed posts.

### Oyoshi on Facebook (direct search)

Open **Scrape Data → Oyoshi on Facebook** to collect directly from the preconfigured Facebook search for `oyoshi` (including its saved filter), reusing your Facebook login without altering your saved link selection:

- **Skips link selection:** Opens straight to **Collect posts** (or **Connect Facebook** first if you are not signed in).
- **Strict scroll budget:** Defaults to **3** scroll iterations (adjustable between **2** and **5**) with a strict limit—no extra adaptive scroll cycles are added.
- **Custom search URLs:** Replace **Oyoshi Facebook search URL** with any filtered Facebook search link (`/search/…` containing a `q=` query). Your custom URL and scroll count stay saved in the session when switching sources.
- **Preview & save:** Click **Open this search on Facebook** to inspect the live search page, or click **Collect posts**, enter a **Custom title**, and click **Save data to cloud**.

### Saved Facebook links

Open **Scrape Data → Saved Facebook links**, beside the **Collect data** tab (or expand **Inspect all saved Facebook links** in **Your links**):

- **Search & inspect:** Filter your entire personal library with **Find a saved Facebook link** and click any URL to open it in Facebook.
- **Delete saved links:** Tick **Delete** beside the unwanted URLs, then click **Delete … selected links**. Deleted links are removed from your saved library and pending scrape selection. Previously collected posts and analyses stay intact.
- **Refresh & export:** Click **Refresh saved Facebook links** to sync changes from other sessions, or **Download all saved Facebook links** to export a `.txt` list.
- **No browser needed:** Viewing or refreshing your link library does not open a remote Facebook browser or start collection.

## Scrape Data: X, News, Websites & Reddit

**X**, **Online news**, and **Custom website** save collected records to the cloud automatically. If a cloud write fails, keep the page open and click **Retry saving collected records** to save the cached results without scraping again.

### X (recent posts)

Click **Scrape Data → X** (or **Start X collection** / **Resume X collection**) to open the 3-step modal:

1. **Name your dataset:** Enter a required **Dataset name** and click **Continue to options**.
2. **Choose collection options:** Toggle watch searches, edit queries, set **Posts per search** (10–100) and **Replies per post** (0–10), check the estimated read count/cost, and click **Continue to collect**.
3. **Collect posts:** Review the summary and click **Collect posts**. Results save automatically under your dataset name.

### Online news (Google News)

1. Open **Scrape Data → Online news**, enable watch categories, and customize their search queries if needed.
2. Choose the **Published** time window, **Google News editions**, **Articles per search**, and an optional **Dataset title**.
3. Click **Search Google News**. Both the collected articles and the border-tension risk point save automatically.
4. Filter the on-screen preview by **Show** (`All articles`, `Flagged articles`, `Boycott`, `Border tension & escalation`) or **Category**—all collected articles remain saved in the dataset.

### Custom website

1. Open **Scrape Data → Custom website** and paste up to 100 public HTTP/HTTPS URLs into **Website links (one per line)** (social media and private-network links are rejected). Click **Next →**.
2. In **Collect pages**, enter an optional **Dataset title** and click **Collect website pages**.
3. Expand any collected page to inspect its extracted text or click **Open source page**. To rename or move the batch to another dataset title without re-scraping, update **Dataset title** and click **Save dataset**.

- **URL presets:** In **Your links**, save reusable URL lists under **URL presets** (**Save preset**, **Load preset**, **Update preset**, **Delete preset**, **Refresh presets**). Presets are private to your account.
- **Extraction scope:** Extracts main HTML/plain-text content (up to 60,000 characters and 2 MB per page, 5 pages concurrently). Does not execute JavaScript, sign in, read PDFs, or bypass CAPTCHAs.

### Reddit (`r/cambodia`)

1. Open **Scrape Data → Other sources → Reddit**.
2. Set **Reddit posts per source** (1–500) and **Comments per post** (0–100), then click **Collect posts**.
3. Enter a **Custom title** and click **Save data to cloud**.

## Clean & manage data

Open **Clean & manage data** in the sidebar to inspect datasets and permanently remove unwanted records from the shared library.

### Browse and filter

- **Dataset cards:** Shows all datasets saved today in your local timezone (or the 6 latest datasets if none were saved today). Click **Refresh datasets** to reload from the cloud.
- **Find older datasets:** Open **Find a dataset** to browse all datasets as cards, 12 per page. Use the numbered page buttons below the cards, or **Previous** and **Next**, to change pages. Expand **Show / hide filters** to search by dataset title, page title, URL, or record ID. Collapse the panel to keep browsing filtered cards, or clear the search to show every dataset again. Click **Open dataset** to review one, or **Browse all records**.
- **Filter inside a dataset:** Narrow records by **Dataset**, **Source**, **Status** (`pending`, `processed`, `error`), or **Search records**. Click **Back to datasets** to return to the card view.

### Delete records or datasets

All deletions require confirmation, apply across all users of the shared library, and automatically clear deleted records from active session caches:

- **Delete a single record:** Click **Delete** beside any row in **Records**, then click **Permanently delete record** (or **Cancel deletion**). Expand **View record table** to inspect full metadata.
- **Delete multiple records:** Under **Delete multiple records**, pick items in **Records to delete** or click **Select all records on this page** (up to 200 per batch). Click **Review deletion of … record(s)**, verify the preview table, and click **Permanently delete … record(s)**.
- **Delete an entire dataset:** Select a single title in **Dataset**, expand **Delete an entire dataset**, click **Review dataset deletion**, and confirm with **Permanently delete dataset**. This removes all reviewed records in that dataset across all pages, sources, and statuses.
- **Delete multiple datasets:** Turn on **Select datasets to delete** above the dataset cards, then select cards across either tab, searches, or pages. Review the selected names and full record counts, click **Review deletion of … dataset(s)**, and confirm with **Permanently delete … dataset(s)**. Use **Cancel dataset deletion** to cancel the review or **Clear dataset selection** to start over. Changed, moved, and newly added records are kept.

## Sentiment Analysis & reports

### 1. Configure settings (optional)

Open the **Settings** tab in **Sentiment Analysis** to adjust:
- **Record status:** `all` (default), `pending`, `processed`, or `error`.
- **AI provider:** **Gemini** (with model selector) or **ChatGPT**, using server-configured API keys.

### 2. Choose data and run analysis

Click **Start sentiment analysis** (or **Continue**) on the **Analysis** tab to open the 2-step guided modal:

1. **Choose and edit data:**
   - Select **Manual selection** (check **Filter by record sources**, then click one or more dataset cards or use **Choose older datasets or adjust selection**) or **Last scraped data** (the most recent collection from this browser session).
   - Review the loaded table and uncheck **Include** on any record you want to exclude. Exclusions save to the cloud immediately and persist across sessions. Click **Next →**.
2. **Analyze:**
   - Enter any Thai projects, companies, or brands to watch (one per line).
   - Click **Run Gemini on … included records** or **Run ChatGPT on … included records**. When finished, the modal closes and displays the results on the page.

### 3. Signals, evidence & exports

Each analyzed record receives an English translation, topics, a sentiment label (**Joy**, **Anger**, **Anxiety**, or **Neutral**), a risk score (1–10), and a Thailand monitoring signal:

| Signal | Meaning |
| --- | --- |
| **Negative sentiment** | Evidence-backed criticism or negative sentiment toward a relevant target. |
| **Reported tension** | Reports of disputes, protests, or escalation. |
| **Explicit boycott call** | Directly urges others not to buy, support, or participate. |
| **Reporting a boycott** | Describes or reports on a boycott without calling for one. |
| **Opposing a boycott** | Argues against a boycott. |
| **Needs review / unclear** | Ambiguous intent or insufficient evidence. |

- **Report tabs:** Inspect **Comment Sentiment Analysis**, **Velocity Tracker**, **Negative Post Reports**, and **Evidence by record**.
- **Filter & export:** In **Evidence by record**, filter by **Show findings** and click **Export findings CSV**. Above the report, use **Download report (HTML)** or **Create PDF** → **Download PDF**.
- **Reopen saved analyses:** Under **View processed records**, saved analyses for the included records in your selected datasets appear automatically without re-running the AI model.

## Sentiment Trends & risk timeline

Open **Sentiment Trends** to compare runs over time:

- **Sentiment over time:** Plots each saved AI analysis run and Online News scan from **−100** (all negative) to **+100** (all positive), with a dashed equal-weight average and red rings marking boycott or border-tension signals. Saved history from all record sources appears automatically, independently of your selections in Sentiment Analysis. Use **Refresh timeline** to reload recent runs.
- **Border tension risk over time:** Tracks headline risk scores (`Low < 20 ≤ Elevated < 45 ≤ High`) and boycott article counts from Google News scans. Use **Refresh risk timeline** to update.
- **Velocity Tracker:** To measure engagement growth (`Live`, `Slowing`, `Dead`) in Sentiment Analysis reports, recollect and save the same eligible Facebook/Reddit/X posts at a later time so at least two snapshots exist.

## Content plan

Open **Content plan** in the sidebar to turn analyzed customer concerns into keyword-led content briefs and drafts:

1. **Choose source records:** Select **Loaded processed records** or **Latest analysis**, then pick up to 30 records in **Records to use**.
2. **Define the brief:** Enter **Brand or topic**, **What should the content improve?**, and an optional **Focus keyword or concern**.
3. **Select language & model:** Choose **English**, **Khmer**, or **Thai** and a configured **Recommendation provider** (**Gemini** or **ChatGPT**).
4. **Generate & verify:** Click **Generate content plan**. Review each card's primary/supporting keywords, concern, outline, and draft, and open **Source evidence and facts to verify** to check quotes and factual claims before publishing.
5. **Export:** Click **Download keywords (CSV)** or **Download content plan (JSON)** to save a copy (plans are kept only for the current session).

## GenCite (AI share of voice)

Open **GenCite** to measure how often AI models mention your brand when answering customer questions:

- **Analyze:** Follow one step at a time. Enter a **Query or topic** and click **Generate query variations** to open **Queries to test**. Edit the table and select up to 50 queries, then click **Continue to brand**. Enter **Brand to track** (plus optional aliases, competitors, market, and region), choose **Providers**, and click **Run analysis on … queries**. Back buttons preserve your entries and selections. Use **Run again** to add another sampling round, or enter an **Analysis name** and click **Save analysis**.
- **Recommendations:** Generate content briefs or keyword ideas targeting low-visibility queries. Open **View brief** / **View evidence**, or select **Queue for testing** → **Test … queued keywords** to run those keywords as a new query set.
- **Saved analyses:** Open, rename, refresh, or delete your private saved GenCite runs. Use **Check provider access → Run check** in **Analyze** to test server AI keys.

## Troubleshooting & admin setup

### Where data is stored

| Item | Scope | How it is saved |
| --- | --- | --- |
| **Facebook links & Website presets** | Private to your account | Saved in **Your links** / **URL presets**; inspect or export in **Saved Facebook links**. |
| **Collected records & AI analyses** | Shared across approved users | Auto-saved (X, News, Websites) or via **Save data to cloud** (Facebook, Reddit). |
| **Exclusions (`Include` toggle)** | Shared with the dataset | Saved immediately when toggled in **Choose and edit data**. |
| **GenCite saved analyses** | Private to your account | Saved via **Save analysis** in **GenCite → Analyze**. |
| **Content plans & downloaded reports** | Session / local download | Exported as CSV, JSON, HTML, or PDF files. |

### Common issues

| Issue | Resolution |
| --- | --- |
| **Access denied at sign-in** | Sign in with an email listed in `FAQ_EDITOR_EMAILS`. |
| **Facebook login is Saved or expired** | Open **Scrape Data → Facebook**, click **Check Facebook connection**, or **Log in to Facebook** → **Save Facebook login**. |
| **Oyoshi Facebook search URL rejected** | Use a valid Facebook search URL (`/search/…` with a non-empty `q=` query), or restore the default Oyoshi URL. |
| **Saved Facebook links could not load** | Click **Refresh** in **Your links** or **Refresh saved Facebook links** in the link inspector. |
| **Cloud saving failed after collection** | Keep the tab open and click **Retry saving collected records**—cached results save without re-scraping. |
| **No records in Sentiment Analysis** | Select sources and datasets in **Manual selection** (or **Last scraped data**), check **Record status** in **Settings**, or click **Refresh datasets**. |
| **Run Gemini / ChatGPT button disabled** | Ensure at least one record has **Include** checked, edits have finished saving, and the provider key is configured. |
| **PDF export unavailable** | Use **Download report (HTML)** and print to PDF in your browser, or install Playwright Chromium on the server. |

### Administrator configuration

Configure secrets in your hosting environment (e.g., Railway Variables or `.env.local`):

| Feature | Required settings |
| --- | --- |
| **Google sign-in** | `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_APP_ID`, `FAQ_EDITOR_EMAILS` |
| **Firestore library** | `FIREBASE_PROJECT_ID`, `FIREBASE_COLLECTION`, `FIREBASE_CREDENTIALS` |
| **Gemini / ChatGPT** | `GEMINI_KEY` (or `GEMINI_API_KEY`), optional `GEMINI_MODEL`; `OPENAI_API_KEY`, optional `OPENAI_MODEL` |
| **Hosted Facebook** | `BROWSERBASE_API_KEY` |
| **Reddit / X** | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` (optional `REDDIT_REFRESH_TOKEN`); `X_BEARER_TOKEN` |
| **PDF reports** | Server Playwright + Chromium (`playwright install chromium`) |
