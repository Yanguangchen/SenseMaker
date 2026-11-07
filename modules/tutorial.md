# CrisisRadar tutorial

Use CrisisRadar to collect source material across social and web channels, clean and curate shared datasets, analyze sentiment and Thailand-related signals with AI, track post velocity and timeline risk, and turn findings into evidence-backed content and AI share-of-voice strategies.

## Quick start & workspace guide

### 5-minute end-to-end quick start

Run your first end-to-end analysis with a public website article—no social media login or external scraper key is required:

1. **Sign in:** Use **Sign in with Google** in the sidebar with an email listed in your team's approved allowlist (`FAQ_EDITOR_EMAILS`).
2. **Open Custom website:** Go to **Scrape Data** → click **Open Custom website →** (or **Start collecting website pages**). Paste a public article URL into **Website links (one per line)** and click **Next →**.
3. **Collect & auto-save:** Optionally enter a **Dataset title**, then click **Collect website pages**. Wait for **Saved … record(s) to the cloud**.
4. **Open Sentiment Analysis:** Select **Sentiment Analysis** in the left navigation rail and click **Start sentiment analysis** on the **Analysis** tab.
5. **Choose and curate records:** In **Step 1 · Choose and edit data**, select **Last scraped data** (or **Manual selection** → check **Custom website** under **Filter by record sources** → click your dataset card). Keep **Include** checked for the rows you want to analyze, then click **Next →**.
6. **Run AI analysis:** In **Step 2 · Analyze**, add any Thai watch terms (one per line) and click **Run Gemini on … included record(s)** or **Run ChatGPT on … included record(s)**.
7. **Review & act:** Inspect **Comment Sentiment Analysis**, **Negative Post Reports**, and **Evidence by record** on the page (or export HTML, PDF, or CSV), then open **Content plan** to generate keyword-led briefs from **Latest analysis**.

> **Tip:** Automatic cloud saving stores raw records in Firestore so your team can review them, but it never triggers AI billing automatically—analysis only runs when you explicitly click **Run Gemini** or **Run ChatGPT**.

### Workspaces at a glance

| Workspace | Primary purpose | Storage scope |
| --- | --- | --- |
| **Scrape Data** | Collect posts, comments, headlines, and web pages from **Facebook**, **Oyoshi on Facebook**, **X**, **Online news**, **Custom website**, or **Reddit**; manage **Saved Facebook links**. | Shared datasets · Private link libraries |
| **Clean & manage data** | Browse dataset cards, filter records by source or status, and review/delete single records, page batches, or entire datasets. | Shared across approved users |
| **Sentiment Analysis** | Configure AI provider (**Gemini** or **ChatGPT**), curate included records, run analysis, inspect literal quote evidence, and export HTML/PDF/CSV reports. | Shared across approved users |
| **Velocity Tracking** | Create and rename up to 10 tracking notepads, add up to 10 Facebook post permalinks per card, and log view counts with run dates, times, and percentage changes. | Shared viewing; creator runs and edits |
| **Sentiment Trends** | Track **Sentiment over time** (`−100` to `+100`) and **Border tension risk over time** (`0`–`100`), or reset shared timeline history. | Shared across approved users |
| **Content plan** | Turn up to 30 analyzed records into target keywords, content outlines, drafts, and verification checklists in **English**, **Khmer**, or **Thai**. | Current browser session · CSV/JSON export |
| **GenCite** | Measure brand share of voice across sampled **Gemini** and **ChatGPT** answers, generate gap-closing recommendations, and save runs. | Private to your account |
| **Tutorial** | Browse or search this interactive guide by chapter, subsection, or keyword. | Built-in reference |

### Navigation, theme & Browserbase usage bar

- **Glass navigation rail & keyboard shortcuts:** Click the **CrisisRadar** header (or press `Escape`) to collapse or expand the left sidebar. Keyboard users can press `Tab` to focus the rail, use `Up` / `Down` or `Home` / `End` to move between workspaces, and press `Enter` to switch. Because all workspaces stay mounted, switching tabs preserves your form drafts, unsaved collections, and running jobs.
- **Theme & account status:** Open the top-right menu (**Settings**) to switch between **Light** and **Dark** themes. The bottom of the sidebar displays your connected Facebook status (**Facebook · Connected** or **Facebook · Saved**), your signed-in Google email, and **Sign out**.
- **Active job glow:** Whenever a collection, cloud write, or AI analysis is running, a pulsating gradient glow frames the browser window until the operation finishes.
- **Browserbase usage bar:** Located at the top of the dashboard, this bar shows shared project browser minutes used against your configured allowance (default: **Developer** plan's `6,000` minutes / `100` hours; also supports **Free** `1` hour, **Startup** `500` hours, or a custom `BROWSERBASE_BROWSER_HOURS_LIMIT`), remaining minutes, and percentage used. It refreshes automatically every 60 seconds or immediately when you click **↻**. The meter turns **amber** at `80%+` usage and **red** at `100%+`. If a refresh fails, the last valid reading is kept and marked **Refresh unavailable**.

## Scrape Data: Facebook & Oyoshi

Open **Scrape Data** to switch between the **Collect data** tab and the **Saved Facebook links** tab. Under **Collect data**, choose from five main source cards—**Facebook**, **Oyoshi on Facebook**, **X**, **Online news**, and **Custom website**—or expand **Other sources** for **Reddit**. Click **← All sources** at any time to return to the card grid without losing your current settings or collected records.

### Source comparison

| Source | What it collects | How it runs | Cloud saving |
| --- | --- | --- | --- |
| **Facebook** | Visible posts and comments from selected Facebook group, page, or post URLs (1–50 scrolls). | Focused step modal · Authenticated browser | Manual (**Save data to cloud**) |
| **Oyoshi on Facebook** | Direct Facebook search results for `oyoshi` (or a custom search URL) with a strict 2–5 scroll limit. | Focused step modal · Authenticated browser | Manual (**Save data to cloud**) |
| **X** | Recent public posts (~last 7 days, excluding reposts) and optional reply samples across watch queries. | 3-step focused modal · Official X API | Automatic |
| **Online news** | Google News headlines and snippets across enabled watch categories and editions, plus a risk scan. | Inline workspace · Google News RSS | Automatic |
| **Custom website** | Main article/body text extracted from up to 100 public HTTP/HTTPS web pages per run. | 2-step focused modal · Direct HTTP | Automatic (plus **Save dataset** to rename) |
| **Reddit** | Newest posts and sampled comments from `r/cambodia` feeds or threads. | Inline workspace · Official Reddit API | Manual (**Save data to cloud**) |

### Facebook (groups, pages & posts)

> **Note:** Full Facebook group and page scraping consumes significant Browserbase session time. For quick brand monitoring, prefer **Oyoshi on Facebook** (2–5 scrolls).

Click **Open Facebook →** (or **Start collecting Facebook posts** / **Continue · step …**) to open the guided modal:

1. **Step 1 · Connect Facebook** *(shown only when not yet connected)*:
   - Click **Log in to Facebook**. On hosted deployments, complete Facebook sign-in and any two-factor verification inside the embedded live browser (the window stays open for up to 3 minutes, or click **Open login in a new tab**), then click **Save Facebook login**.
   - If your session status is **Saved**, CrisisRadar automatically verifies it once per visit, or you can click **Check Facebook connection** manually. Your saved login is private to your CrisisRadar account.
2. **Step 2 · Your links**:
   - Select saved URLs in **Sources** (or click **Select all**), or paste direct Facebook group, page, or post URLs into **Add links (one per line)**. Valid pasted URLs save to your personal library and select automatically when you finish editing.
   - Use **Clear** to deselect links and clear the text box while keeping your saved library, **Refresh** to reload from the cloud, or **Remove … saved link(s)** to delete selected URLs from your account library without deleting previously collected posts. Click **Next →**.
3. **Step 3 · Collect posts**:
   - Adjust **Scroll Iterations** (`1`–`50`, default `10`) and **Headless Browser** (always enabled on cloud deployments).
   - Click **Collect posts** in the final navigation row (or **← Previous** before results exist to change links). The collector opens each URL directly and displays live progress: completed sources, collected post count, current source activity, and elapsed time.
   - On hosted deployments, collection continues in the background if you switch workspaces or close the modal. Click **Cancel collection** to stop early and keep completed results.
4. **Save to cloud**:
   - Review **Records collected**, inspect the preview table or **Raw JSON Output**, enter a required **Custom title**, and click **Save data to cloud**. Saving from the modal closes the window and shows the confirmation banner on the overview card.

### Oyoshi on Facebook (direct search)

Open **Scrape Data → Oyoshi on Facebook** to collect directly from the preconfigured Facebook recent-posts search for `oyoshi`, reusing your Facebook session without altering your saved link selections:

- **Skips link selection:** Opens straight to **Collect posts** (after **Connect Facebook** if you are not signed in).
- **Strict scroll budget:** Defaults to **3** scroll iterations and is capped between **2** and **5** with a strict limit—no extra adaptive scroll cycles are added, keeping Browserbase usage predictable.
- **Custom search URLs:** You can replace **Oyoshi Facebook search URL** with any valid Facebook search URL (`/search/…` with a non-empty `q=` query). Click **Open this search on Facebook** to verify the live page in a new tab. Your custom URL and scroll setting persist in the session when switching sources.
- **Collect & save:** Click **Collect posts**, enter a **Custom title**, and click **Save data to cloud**.

### Saved Facebook links

Open the **Saved Facebook links** tab in **Scrape Data** (or expand **Inspect all saved Facebook links** inside **Your links**) to manage your personal Facebook URL library without opening a remote browser:

- **Search & open:** Type in **Find a saved Facebook link** to filter your URLs and click any link to open it directly on Facebook.
- **Delete links:** Tick **Delete** beside unwanted URLs and click **Delete … selected link(s)**. Removed links are cleared from both your saved library and any pending scrape selection, while previously collected posts and AI analyses remain intact.
- **Sync & export:** Click **Refresh saved Facebook links** to load updates from other devices, or **Download all saved Facebook links** to export a plain-text `saved-facebook-links.txt` backup.

## Scrape Data: X, News, Websites & Reddit

**X**, **Online news**, and **Custom website** automatically save collected records to the shared cloud library as soon as collection completes.

> **Safeguard:** If a cloud write fails, your collected records remain cached in the current browser session and new collection is paused—keep the page open and click **Retry saving collected records** to save the cached batch without scraping again or using extra API quota.

### X (recent posts & replies)

Click **Open X →** or **Start X collection** (or **Resume X collection** to continue an unfinished setup) to open the 3-step focused dialog:

1. **1. Name your dataset:** Enter a required **Dataset name** (e.g., `Brand feedback — November`) and click **Continue to options**. Reusing an existing dataset name groups the new posts into that dataset.
2. **2. Choose collection options:**
   - Enable or disable the built-in watch categories (**OYOSHI mentions**, **Thailand–Cambodia border tension**, **Boycott of Thai products in Cambodia**) and edit their search queries (up to 400 characters; `-is:retweet -is:reply` is appended automatically).
   - Set **Posts per search** (`10`–`100`, step `10`) and **Replies per post** (`0`–`10`, default `0`).
   - Review the live upper-bound read estimate and cost (`$0.005` per post read; enabling replies requests at least 10 results per reply page). Click **Continue to collect** (or **Back to dataset name**).
3. **3. Collect posts:** Review your dataset name and read estimate, then click **Collect posts** (or **Back to options**). Posts and sampled replies from approximately the last 7 days save automatically under your dataset name.

### Online news (Google News & border-tension scan)

1. Open **Scrape Data → Online news**.
2. Toggle watch categories (**OYOSHI mentions**, **Thailand–Cambodia border tension**, **Boycott of Thai products in Cambodia**) and customize their search terms.
3. Choose the **Published** time window (`Past 24 hours`, `Past week`, `Past month`, or `Past year`), **Google News editions** (`English`, `Thai`), **Articles per search** (`10`–`100`, default `50`), and an optional **Dataset title**.
4. Click **Search Google News**. CrisisRadar deduplicates articles across searches, scans headlines and snippets for Thailand/Cambodia mentions and risk keywords (`Boycott`, `Border tension`, `Escalation`, `De-escalation`), saves all articles to Firestore, and records a dated point on the **Border tension risk** timeline.
5. Inspect the four summary metrics (**Articles found**, **Boycott flags**, **Border tension flags**, **Border tension risk**), the **Flagged coverage by publication day** chart, and the article table. Filter the on-screen preview by **Show** (`All articles`, `Flagged articles`, `Boycott`, `Border tension & escalation`) or **Category**—all collected articles remain saved in your dataset. Expand **Open these searches in Google News** to open the live Google News feeds.

### Custom website (articles & web pages)

Click **Open Custom website →** or **Start collecting website pages** to open the 2-step guided modal:

1. **Step 1 · Your links:**
   - Paste up to **100** unique public `http` or `https` URLs into **Website links (one per line)** (links without a scheme default to `https://`; social media domains, non-standard ports, and private/local network addresses are rejected).
   - **URL presets:** Save and reuse link lists privately in your account under **URL presets**. Enter a **New preset name** and click **Save preset** to store the current URLs, or pick a **Saved URL preset** to **Preview saved URLs**, **Load preset**, **Update preset**, **Delete preset** (with **Confirm delete preset**), or **Refresh presets**. Click **Next →**.
2. **Step 2 · Collect pages:**
   - Optionally enter a **Dataset title** and click **Collect website pages**.
   - Expand any collected page to read its extracted text or click **Open source page**. To rename the dataset or move the collected pages to another dataset title without re-scraping, update **Dataset title** and click **Save dataset**.
   - **Technical scope:** Collects up to 5 pages concurrently (30s timeout per page, 10-minute batch budget, up to 5 redirects, max 2 MB and 60,000 extracted characters per page). Supports HTML and plain text only; does not execute JavaScript, sign in, read PDFs, or bypass CAPTCHAs.

### Reddit (`r/cambodia`)

1. On the **Scrape Data** card screen, expand **Other sources** and click **Open Reddit**.
2. Set **Reddit posts per source** (`1`–`500`, default `50`) and **Comments per post** (`0`–`100`, default `20`), then click **Collect posts**.
3. The collector fetches newest posts and a bounded sample of newest comments from `r/cambodia` via Reddit's official Data API, keeping parent post context attached to each comment. Enter a **Custom title** and click **Save data to cloud**.

## Clean & manage data

Open **Clean & manage data** in the sidebar to inspect saved datasets, filter records across the shared library, and permanently remove unwanted records or datasets after a safety review.

### Browse dataset cards & search

- **Refresh catalog:** Click the **Refresh datasets** icon (`↻`) in the top-right corner of the workspace at any time to reload the shared catalog from Firestore.
- **Today's datasets / Latest datasets tab:** Displays poker-style dataset cards saved today in your browser's local timezone (or the **6 latest datasets** if none were saved today), ordered newest first and color-coded by source (with multi-source color strips for mixed datasets). Each card shows its title, record count, pending count, source badges, and save time.
- **Find a dataset tab:** Browses all saved datasets at **12 cards per page** using **Previous**, numbered page buttons, and **Next**. Expand **Show / hide filters** to search by dataset name, page title, URL, or record ID—you can collapse the filter panel while keeping your search active, or clear the input to show all datasets again. Click any card's **Open dataset** button to inspect its records, or click **Browse all records** at the bottom.

### Filter & inspect records inside a dataset

When you open a dataset (or click **Browse all records**), use the filter bar to narrow the view:

- **Dataset:** Switch between any saved dataset title or `All datasets`.
- **Source:** Filter by `Custom website`, `Facebook`, `Online news`, `Reddit`, `X`, or `All sources`.
- **Status:** Filter by `pending`, `processed`, `error`, or `All statuses`.
- **Search records:** Match any combination of words across the dataset title, page/post title, URL, or record ID.
- **Pagination & table view:** Choose **Records per page** (`25`, `50`, `100`, or `200`) and **Page**. Expand **View record table** below the cards to inspect full metadata in a dataframe, or click **Back to datasets** to return to the dataset card browser.

### Four ways to delete (with version-checked confirmation)

Every deletion requires a two-step review, applies across all users of the shared library, purges deleted items from active session caches, and verifies record versions so records modified or moved after you loaded the view are safely kept:

| Scope | How to review & confirm |
| --- | --- |
| **Single record** | Click the red **Delete** button beside any row in **Records**, verify the Record ID in the warning banner, and click **Permanently delete record** (or **Cancel deletion**). |
| **Multiple records (page batch)** | Under **Delete multiple records**, select items in **Records to delete** or click **Select all records on this page** (up to 200 per batch; use **Clear selection** to reset). Click **Review deletion of … record(s)**, check the preview table, and click **Permanently delete … record(s)**. |
| **Entire dataset (inside dataset view)** | Choose a single title in **Dataset**, expand **Delete an entire dataset**, click **Review dataset deletion**, inspect the full preview table (covers all pages, sources, and statuses regardless of active filters), and click **Permanently delete dataset**. |
| **Multiple datasets (card browser)** | Turn on **Select datasets to delete** above the dataset tabs. Click cards across either tab, searches, or pages to select them. Review the summary table of dataset names and total record counts, click **Review deletion of … dataset(s)**, optionally expand **Review all selected records**, and click **Permanently delete … dataset(s)** (or **Cancel dataset deletion** / **Clear dataset selection**). |

## Sentiment Analysis & client reports

Open **Sentiment Analysis** in the sidebar to curate records from saved or recent collections, run **Gemini** or **ChatGPT** analysis, inspect quote-verified Thailand monitoring signals, and export client-ready reports.

### 1. Configure analysis settings (`Settings` tab)

Open the **Settings** tab in **Sentiment Analysis** before or between runs:

- **Record status:** Choose which records in your selected datasets are loaded and analyzed: `all` (default), `pending`, `processed`, or `error`.
- **AI provider:** Select **Gemini** or **ChatGPT**.
- **Gemini model selector:** When **Gemini** is active, choose `Automatic · available Flash model` (default) or a specific model, and click the refresh icon (`↻`) to check which text models your server API key can access.

### 2. Choose data, curate records & run analysis (`Analysis` tab)

Click **Start sentiment analysis** (or **Continue · step …**) on the **Analysis** tab to open the 2-step guided modal:

1. **Step 1 · Choose and edit data:**
   - **Data selection mode:**
     - **Manual selection:** Check one or more platforms under **Filter by record sources** (`Facebook`, `Reddit`, `Online news`, `X`, `Custom website`). Click one or more cards under **Today's datasets / Latest datasets** to analyze them together (click a selected card again to deselect it), or expand **Choose older datasets or adjust selection** to search and multiselect across all saved datasets. Click the refresh icon (`↻`) to reload datasets from Firestore.
     - **Last scraped data:** Loads only the records from the most recently completed collection in your current browser session (saving them to Firestore automatically if they have not been saved yet).
   - **Include / exclude records:** Review the interactive table and uncheck **Include** on any record you want to skip. Exclusions save to the shared cloud dataset immediately and persist across sessions (check **Include** again at any time to restore a record, or click **Retry saving changes** if a write fails). Click **Next →**.
2. **Step 2 · Analyze:**
   - Enter **Thai projects, companies and brands to watch (one name or alias per line)** (defaults to `THAILAND_WATCH_TERMS`; leave blank to monitor Thailand generally).
   - Click **Run Gemini on … included record(s)** or **Run ChatGPT on … included record(s)** (up to 499 records per run). When complete, the modal closes and renders the full report on the page.

### 3. Signals, evidence validation & report exports

Each analyzed record receives an English **translation**, **topics**, a **sentiment** label (**Joy**, **Anger**, **Anxiety**, or **Neutral**), a **risk score / review priority** (`1`–`10`), and a **Thailand monitoring assessment** validated against literal quotes in the source text:

| Signal label | Meaning & validation rule |
| --- | --- |
| **No signal detected** | No Thailand-related criticism, tension, or boycott signal established. |
| **Negative sentiment** | Evidence-backed criticism or negative sentiment toward Thailand or a watched target. |
| **Reported tension** | Reports of disputes, border closures, protests, military activity, or escalation. |
| **Explicit boycott call** | Directly urges others not to buy, support, or participate. |
| **Reporting a boycott** | Describes or reports on a boycott without calling for one. |
| **Opposing a boycott** | Argues against a boycott. |
| **Needs review** | Ambiguous intent, or a model classification downgraded because it lacked an exact supporting quote from the record. |

- **Three report tabs** (available for both **Analysis results** and **View processed records**):
  1. **Comment Sentiment Analysis:** Client appendix template showing Negative / Neutral / Positive share percentages, dominant mood and topics, **Key insight**, **Engagement quality**, and **Threat level** (`Low`, `Medium` at priority 5+ or any Thailand signal, `High` at priority 8+ or an explicit boycott call).
  2. **Negative Post Reports (`NPR-xxxxxx`):** Individual exhibit sheets for every negative (`Anger` / `Anxiety`) or flagged record, ordered by priority. Includes the captured excerpt, English translation, AI summary, views and comments vs. prior check, sentiment split, word cloud, dominant themes, and pre-filled **Impact** (`High` → `6h` check cadence; `Low` → `24h` check cadence).
  3. **Evidence by record:** Displays **Records assessed**, **Thailand signals**, and **Boycott calls** metrics, a **Show findings** filter (`All records`, `Flagged signals`, `Boycott calls`, `Boycott reports`, `Negative sentiment`, `Reported tensions`, `Needs review`), **Export findings CSV** (`crisisradar-findings.csv`), and detailed cards with exact quotes, validation notes, translations, and source links.
- **HTML & PDF exports:** Above the report tabs, click **Download report (HTML)** for an immediate standalone file, or click **Create PDF** → **Download PDF** to generate an A4 landscape PDF fitted to one page per sheet.
- **View & delete processed records:** Saved analyses for included records in your selected datasets appear automatically under **View processed records** without re-running the AI model. Expand **Delete processed records** to select and permanently delete processed records and their analyses from the shared library.

## Velocity Tracking

Open **Velocity Tracking** in the sidebar to monitor view-count growth on specific Facebook posts, photos, reels, or videos using landscape notepads. All approved users can see each other's cards and saved results. Covers marked **SHARED VELOCITY** open in view-only mode; each creator retains the Run and editing controls. Click **Refresh cards & checks** to see another user's newly saved cards or results.

### Create cards, add posts & run checks

1. **Create a notepad card:** Click **Create velocity tracking card** in the top toolbar to create an empty card immediately (up to **10 cards** per account; no link or title is required upfront).
2. **Add Facebook post permalinks:** Click a card cover (**Open Velocity tracking card …**) to open its **Velocity notepad** dialog. Paste a direct link to a single Facebook post, photo, reel, or video into **Provoker's post** (group or page home URLs are rejected) and click **Add post**. Each card tracks up to **10 posts** (one per row).
3. **Run a view check:** Click **Run** inside the notepad. CrisisRadar visits each post in the card using your Facebook connection from **Scrape Data**, reads the displayed view count, and saves a column labeled with the run's completion date and time (UTC). The latest eight runs are shown. You can close the notepad or switch workspaces while a check runs.

### Manage posts, cards & check history

- **Rename a card:** Open the notepad, expand **Rename card**, enter a **Card name**, and click **Save name**. The cover and notepad title update, keeping all posts and run history.
- **Refresh & retry:** Click **Refresh cards & checks** to reload your cards from Firestore. The table displays the latest **8** checks, while earlier checks remain archived in Firestore. If saving a completed check fails, click **Retry saving check** to save the cached counts without scraping again or creating a duplicate column.
- **Remove posts or cards:** Expand **Manage posts**, select a URL in **Post to remove**, and click **Remove post** to free a row. Click **Remove card** in the top-right of the notepad to delete the card cover. Removing a post or card never deletes collected records or archived check history.

### Reading check cells & status badges

| Cell / Status | Meaning |
| --- | --- |
| **alive** | Views increased in the latest check, and the gain is equal to or larger than the previous check's gain (or it is the first measured gain). |
| **dying** | Views increased by less than the previous check's gain, or views were flat after growing in the prior interval. |
| **dead** | Views showed zero growth across consecutive checks. |
| **`—` (status)** | Fewer than two consecutive numerical view counts are available for this post. |
| **Views change (%)** | Beside **Status**, shows `(latest − previous) / previous × 100` for the latest two runs. Positive values mean views increased; negative values mean they decreased. Shows **—** if either count is missing or the previous count is zero. |
| **Unavailable / Failed** | **Unavailable** means no view count could be read from the identified post; **Failed** means the post could not be loaded or identified. Expand **Why counts are unavailable** for the saved reason and a link to check the post. Neither is treated as zero, so a missing count never falsely marks a post **dead**. |

## Sentiment Trends & risk timeline

Open **Sentiment Trends** in the sidebar to compare historical AI analysis runs and Google News scans across three tabs:

### 1. Sentiment over time

- **Unified polarity scale (`−100` to `+100`):** Plots every completed **AI analysis** run (blue) and **Online News** scan (teal) over time, with an orange dashed equal-weight average line and red rings marking runs that detected boycott or border-tension signals:
  - **AI analysis points:** `100 × (Joy − (Anger + Anxiety)) / scored records`.
  - **Online News points:** `100 × (Positive − Negative headlines) / total scanned headlines`.
- **Independent of current filters:** Saved history from all record sources loads automatically, regardless of which sources or datasets are currently selected in **Sentiment Analysis**.
- **Metrics & history table:** View **Latest sentiment** (with delta vs. previous point), **Average sentiment**, **Analysis runs**, and **News searches**. Click **Refresh timeline** to reload recent runs, or expand **History** to inspect the full table of runs, record counts, methods, and watch terms.

### 2. Border tension risk over time

- **Risk score (`0`–`100`) & bands:** Plots the border-tension risk score from each Google News scan over shaded **Low** (`< 20`), **Elevated** (`20`–`44.9`), and **High** (`≥ 45`) background bands, paired with a purple dashed **Boycott index** line and **▲ Rising** callouts whenever risk increases by `10+` points between scans.
- **How risk is calculated:** Measures the share of Thailand/Cambodia-linked articles reporting border tension, where escalation terms (clashes, shelling, casualties, evacuations) count double (`level 2`), general tension counts single (`level 1`), and ceasefire/talks coverage lowers an article by one level. Scans with fewer than 10 linked articles are scaled as if they had 10 so a single headline cannot spike the index.
- **Refresh & inspect:** Click **Refresh risk timeline** to update, or expand **Scan history** to compare scan dates, risk deltas, flag counts, time windows, and search queries.

### 3. Settings (Reset timeline)

- Open the **Settings** tab in **Sentiment Trends** and click **Reset timeline** → **Confirm reset timeline** (or **Cancel**) to permanently clear all saved AI analysis run summaries and Online News scan points from both timelines for all users of the shared library.
- **What is kept:** Collected source records, datasets, and per-record AI analysis results remain untouched in Firestore; subsequent analyses and news searches start a fresh timeline.

## Content plan

Open **Content plan** in the sidebar to turn analyzed customer concerns into keyword-led content briefs, outlines, and drafts grounded in your records.

### 1. Select records & define the brief

1. **Choose source records:** Select **Loaded processed records** (from your active dataset selection in Sentiment Analysis) or **Latest analysis**, then pick up to **30** analyzed records in **Records to use** (long records are excerpted to the first 1,200 characters and up to 2 comment excerpts).
2. **Define the brief:** Enter **Brand or topic** (e.g., `OYOSHI customer service`), review or edit **What should the content improve?**, and optionally add a **Focus keyword or concern** (e.g., `delivery delays, product quality`).
3. **Select language & provider:** Choose **Content language** (**English**, **Khmer**, or **Thai**) and **Recommendation provider** (**Gemini** or **ChatGPT**).

### 2. Generate briefs, outlines & drafts

Click **Generate content plan**. Review the **Sentiment baseline in the selected sample** metrics (**Anger**, **Anxiety**, **Neutral**, **Joy**) and the numbered recommendation cards—ordered by cited Anger/Anxiety records—which include:

- **Primary keyword** and **Supporting keywords**
- **Concern to address**, **Format**, **Search intent**, and **Content angle**
- **Content outline** and a complete **Suggested draft**
- **Measure after publishing** guidance

### 3. Verify evidence & export

Expand **Source evidence and facts to verify** on each card to inspect the attached source quotes, open original URLs, and check factual claims before publishing. Click **Download keywords (CSV)** (`sentiment-keywords.csv`) or **Download content plan (JSON)** (`sentiment-content-plan.json`) to save a copy (plans are kept only for the current browser session).

## GenCite (AI share of voice)

Open **GenCite** in the sidebar to measure how often **Gemini** and **ChatGPT** mention your brand when answering customer questions, then close visibility gaps with targeted content and keyword ideas.

### Provider status & access check

At the top of **GenCite**, check which models and server keys are active for **Gemini** and **ChatGPT**. Expand **Check provider access** and click **Run check** to send a small test query to each configured provider and verify structured output support before running a full batch.

### 1. Analyze tab (3-step workflow)

1. **1 · Topic:** Enter a **Query or topic** (e.g., `bottled green tea brands in Cambodia`) and click **Generate query variations** (or **Continue to queries** if variations are already loaded).
2. **2 · Queries to test:** Review the interactive table (`Test`, `Query`, `Intent`). Edit query wording, add custom rows, or use **Select all** / **Clear selection** to pick up to **50** queries. Each selected query is answered **3 times** by every selected provider. Click **Continue to brand** (or **Back to topic**).
3. **3 · Brand:** Enter **Brand to track**, plus optional comma-separated **Aliases** (up to 8), **Competitors** (up to 8), **Market**, and **Region**. Select **Providers** (**Gemini**, **ChatGPT**) and click **Run analysis on … queries** (or **Back to queries**).
4. **Share of voice results:** Review overall **Share of voice (%)**, **Answers mentioning brand**, **Queries**, **Sampling rounds**, per-model metrics, and the per-query chart and progress table. Click **Run again** to sample another round and merge the new answers into the cumulative totals, or enter an **Analysis name** and click **Save analysis**.

### 2. Recommendations tab

- **Content recommendations:** Click **Generate content recommendations** (or **Regenerate**) to create **5** content briefs targeting topics where your brand is mentioned least—scored by **35% gap**, **25% relevance**, **20% cross-provider consistency**, and **10% freshness**. Expand **View brief** to inspect the target audience, angle, supporting terms, requirements, and tested-query evidence. Click **Mark as published** (or **Unmark as published**) to track published pieces in your account.
- **Keyword ideas:** Click **Generate keyword ideas** (or **Regenerate**) to surface **8** new queries to test next—scored by **35% opportunity**, **25% relevance**, **25% novelty**, and **15% coverage type**. Expand **View evidence** to see related query stats, click **Queue for testing** on any ideas, and click **Test … queued keyword(s)** to run a fresh GenCite analysis on those keywords immediately.

### 3. Saved analyses tab

- Browse all GenCite analyses saved under your account in a summary table showing **Name**, **Brand**, **Share of voice**, **Mentions**, **Queries**, **Rounds**, **Providers**, and **Saved** timestamp.
- Select an **Analysis** from the dropdown to **Open** it back into **Analyze** and **Recommendations**, **Rename** it, **Delete** → **Delete permanently**, or click **Refresh** to sync across devices.

## Troubleshooting, privacy & admin setup

### Data storage & privacy scope

| Data item | Scope | Where & how it is stored |
| --- | --- | --- |
| **Facebook login session** | Private to your account | Encrypted/stored per account in Firestore (hosted) or local `storage_state.json` (local dev). |
| **Saved Facebook links & Website URL presets** | Private to your account | Saved in **Your links** / **URL presets**; manage or export in **Saved Facebook links**. |
| **Collected records, datasets & `Include` toggles** | Shared across approved users | Auto-saved (**X**, **Online news**, **Custom website**) or saved via **Save data to cloud** (**Facebook**, **Reddit**); curated in **Sentiment Analysis** and **Clean & manage data**. |
| **AI sentiment analyses & timelines** | Shared across approved users | Saved with each record and in shared run/scan history collections (`_analysis_runs`, `_news_scans`). |
| **Velocity Tracking cards & check history** | Shared viewing across approved users; creator runs and edits | Saved automatically when creating cards, adding posts, or clicking **Run** in **Velocity Tracking**. Existing cards are shared without moving or deleting their history; Facebook login sessions stay private. |
| **GenCite saved analyses & published marks** | Private to your account | Saved via **Save analysis** and **Mark as published** in **GenCite**. |
| **Content plans & exported reports** | Session / local download | Downloaded as `.csv`, `.json`, `.html`, `.pdf`, or `.txt` files. |

### Common issues & resolutions

| Symptom | Resolution |
| --- | --- |
| **Access denied at Google sign-in** | Sign in with a verified Google account listed in `FAQ_EDITOR_EMAILS`. |
| **Facebook status is `Saved` or collection stops** | Open **Scrape Data → Facebook**, click **Check Facebook connection**, or click **Log in to Facebook** → **Save Facebook login** to refresh your session. |
| **Oyoshi search URL rejected** | Use a valid Facebook search URL starting with `/search/…` that includes a non-empty `q=` query parameter, or restore the default Oyoshi URL. |
| **Browserbase usage shows `Refresh unavailable`** | The last successful reading is kept on screen; click **↻** to retry or verify `BROWSERBASE_API_KEY` and `BROWSERBASE_PROJECT_ID`. |
| **Cloud saving failed after collection** | Keep the browser tab open and click **Retry saving collected records**—cached results save without re-scraping. |
| **Dataset exclusions could not be saved** | Click **Retry saving changes** in **Choose and edit data**, or click the refresh icon (`↻`) to reload the latest dataset state. |
| **No records appear in Sentiment Analysis** | In **Choose and edit data**, select at least one source and dataset card (or **Last scraped data**), check **Record status** in **Settings**, or click **↻** to refresh datasets. |
| **Run Gemini / ChatGPT button is disabled** | Confirm at least one record has **Include** checked, dataset edits have finished saving, and the chosen provider's API key is configured on the server. |
| **Velocity check shows `Unavailable`, `Failed`, or save error** | Read **Why counts are unavailable** inside the card. Open the post on Facebook and check whether it displays views; photos or text posts showing only reactions/comments/shares cannot supply view counts. `Failed` means the post could not be opened or identified. Reconnect Facebook in **Scrape Data** if prompted. If saving to Firestore failed, click **Retry saving check**. |
| **PDF export unavailable** | Click **Download report (HTML)** and print to PDF in your browser (A4 landscape), or ask the administrator to install Playwright Chromium (`python -m playwright install chromium`). |

### Administrator configuration & CLI entrypoints

Configure environment variables in your hosting environment (e.g., Railway Variables, `.env.local`, or `.env`):

| Component | Environment variables / requirements |
| --- | --- |
| **Google sign-in & allowlist** | `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_APP_ID`, `FAQ_EDITOR_EMAILS` |
| **Firestore database** | `FIREBASE_PROJECT_ID`, `FIREBASE_COLLECTION` (default `raw_posts`), `FIREBASE_CREDENTIALS` |
| **AI providers & watch terms** | `AI_PROVIDER` (`gemini` or `openai`), `GEMINI_KEY` (or `GEMINI_API_KEY`), optional `GEMINI_MODEL` (default `auto`); `OPENAI_API_KEY`, optional `OPENAI_MODEL` (default `gpt-5.4-mini`); optional `THAILAND_WATCH_TERMS` |
| **Hosted Facebook & usage bar** | `BROWSERBASE_API_KEY`, optional `BROWSERBASE_PROJECT_ID`, `BROWSERBASE_PLAN` (`free`, `developer`, `startup`; default `developer`), or `BROWSERBASE_BROWSER_HOURS_LIMIT` |
| **Reddit & X collectors** | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, optional `REDDIT_REFRESH_TOKEN`; `X_BEARER_TOKEN` |
| **PDF report rendering** | Playwright Chromium on the server (`python -m playwright install chromium`) |
| **Server & CLI entrypoints** | Run `python hosted.py` (or `streamlit run dashboard.py`) for the web dashboard; run `python main.py` with `TARGET_GROUP_URL` set for CLI batch scraping and pending-post processing. |
