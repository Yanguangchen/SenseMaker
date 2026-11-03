# CrisisRadar tutorial

Use CrisisRadar to collect source material, understand sentiment and Thailand-related signals, track changes, and plan useful content. GenCite adds a separate view of how often AI answers mention your brand.

## Quick start: your first analysis

This walkthrough uses a public website article. You need an approved Google account, working cloud storage and a configured Gemini or ChatGPT provider. The website collector itself needs no API key.

1. Sign in with your approved Google account.
2. Open **Scrape Data → Custom website** from the sidebar.
3. Paste one public article URL into **Website links (one per line)**. Use a page you can read without signing in.
4. Optionally enter a **Dataset title**, such as `Delivery feedback — November baseline`. Leave it blank to use the source and collection time.
5. Click **Collect website pages**. The pages save automatically. Wait for the cloud-save confirmation, then check that the extracted text matches the article.
6. Open **Sentiment Analysis** and click **Start sentiment analysis**. Choose **Manual selection**, your record sources and saved datasets, or choose **Last scraped data** for the latest collection. Records appear automatically.
7. Review the table and uncheck **Include** for any unwanted records. Exclusions save with the dataset and can be reversed. Choose an **AI provider** in Settings if needed, then continue to **Analyze** and add relevant watch terms.
8. Check the included-record count and click **Run Gemini** or **Run ChatGPT**. Successful analyses are saved automatically.
9. Open **Evidence by record** in the results. Read the original excerpt, translation, signal and supporting quotes before drawing conclusions.
10. Open **Content plan**, choose **Latest analysis**, enter a brand/topic and goal, and click **Generate content plan**. Review the drafts and export the plan if you want to keep it.

**What you should have:** a saved source record, a saved analysis, a point on the sentiment timeline, and a draft content plan. Automatic cloud saving does not run AI analysis.

## Sign in and find your way around

Use the Google account approved by your administrator. A Google login grants access to CrisisRadar; connecting Facebook is an additional step used only for Facebook collection. An access-denied message usually means the account is outside the approved-user list.

| Workspace | Use it to |
| --- | --- |
| Scrape Data | Collect Facebook, Reddit, Online news, X or Custom website material. |
| Sentiment Analysis | Choose and edit datasets, analyze included records, review evidence and export reports. |
| Sentiment Trends | Compare saved analysis runs and news scans over time. |
| Content plan | Turn analyzed concerns into keywords, content briefs and drafts. |
| GenCite | Measure brand mentions in sampled AI answers and explore visibility gaps. |
| Manage records | Find saved records, filter a dataset and delete selected records with a preview. |
| Tutorial | Read this guide in the app, browse Contents, move between chapters or search for a control. |

Click the CrisisRadar heading to collapse or expand the sidebar. On small screens it may start collapsed; the icons still open each workspace. Keyboard users can Tab to a workspace, use Up/Down or Home/End to move among navigation buttons, and press Enter to open one. Escape collapses the sidebar.

Open the top-right app menu, choose **Settings**, then select a light, dark or custom theme. Use **Sign out** in the sidebar when finished. Switching workspaces keeps current form values and results in the session; a reload, sign-out, server restart or new device can lose unsaved work.

## Choose sources and save your collection

Choose the tab that matches the material you want to collect. Collection runs when you request it; the platform does not continuously monitor all sources in the background.

| Source tab | Input and coverage | Connection needed |
| --- | --- | --- |
| Facebook | Your selected saved links; browser-visible posts and available comments. | Your Facebook session; hosted browser setup. |
| Reddit | The r/cambodia feed, with configurable post and comment limits. | Approved Reddit API access configured by the administrator. |
| Online news | Google News searches for the enabled watch categories. | No separate news API key. |
| X | Recent posts matching your enabled searches, with optional reply samples. | Server bearer token, read access and API credit. |
| Custom website | Public HTML or plain-text pages at URLs you paste. | No additional API key or social login. |

**Online news, X and Custom website save collected records automatically.** X first asks for a required **Dataset name**, then reveals collection options and a final collection step. For Online news and Custom website, optionally enter a **Dataset title**; a blank title uses the source and collection time. Wait for **Saved … record(s) to the cloud**. All collected records are saved, even if you later filter the preview. They can then be loaded in **Sentiment Analysis** and **Manage records**.

Selecting **X** or **Start X collection** opens a focused window with the surrounding page dimmed and blurred. Escape, the close button or clicking outside closes it; **Resume X collection** restores your draft and current step during the session. After collection and saving, the window closes to show your results.

If cloud saving fails, keep the page open and click **Retry saving collected records**. The collected data and original title are retained; retrying does not scrape again or incur another X collection request. New collection is paused until these records are saved. A reload, sign-out or server restart can still lose data that has not reached the cloud.

For **Facebook and Reddit**, review the results, add a descriptive **Custom title**, then click **Save data to cloud**. A title such as `Brand A — customer questions — 03 Nov` makes a dataset easier to recognize later.

**Collected** means the result is available in the current session. **Saved** means it is in the cloud library. **Processed** means it has a saved AI analysis. Collecting material alone does not run AI analysis.

An unavailable source may produce a warning while other sources succeed. Warnings are not saved as content records. Source coverage and comment samples can be incomplete, so record the limits you used when comparing runs.

## Collect custom website pages

Use **Scrape Data → Custom website** for ordinary articles, blogs, company pages and other public websites.

1. Paste URLs into **Website links (one per line)**. You can enter up to 100 unique links per run; a missing scheme is treated as HTTPS.
2. Optionally enter a **Dataset title**, then click **Collect website pages**. Only the supplied pages and their redirects are requested; links inside each page are not crawled.
3. Check **Pages collected** and any per-link notices. Open each result to read the extracted text and use **Open source page** to compare it with the original.
4. Confirm **Saved … record(s) to the cloud**. If saving failed, use **Retry saving collected records** while keeping the page open.
5. To save after reviewing, edit **Dataset title** above and click **Save dataset** below the results. This saves the collected pages without collecting again. A blank title keeps the current title; a new title moves these records to that dataset without creating copies. Reusing a title groups them with its other records.
6. In **Sentiment Analysis**, load the saved records and select the pages you want to analyze.

**Reuse URL lists with presets:** After pasting links, enter a **New preset name** under **URL presets**
and click **Save preset**. Choose a **Saved URL preset** and click **Load preset** to replace the URL
input with that list. Edit the URLs and use **Update preset** to change the selected list, or give it
a different name and **Save preset** to create another. **Delete preset** asks for confirmation and
only removes the saved list; the current input and collected records are kept. Use **Refresh presets**
to see changes from another device. Presets are private to your account and survive refreshes and
sign-outs. Saving or loading a preset does not collect pages or run analysis.

The collector prefers article/main text and removes common navigation, scripts and hidden elements. It supports HTML and plain text. It does not render JavaScript, sign in, solve CAPTCHA challenges, read PDFs, download media or transcribe audio. A page that works in your browser may still be unavailable to the collector.

Up to 5 pages are collected at once. The limits are 2 MB per page, 60,000 extracted characters per page, up to 5 redirects, 30 seconds per page and a 10-minute batch budget. Each failed or unattempted link has its own notice; completed pages are kept. Long text is marked as truncated. AI analysis has its own excerpt limits, so review any truncation notes in the findings too.

Duplicate input links are removed, and links ending at the same final URL produce one page record in a run. Recollecting and saving a changed website page refreshes its text and resets its earlier analysis to pending; unchanged text keeps its saved analysis. Private/local network links, nonstandard web ports and social media URLs are rejected.

## Collect Facebook posts

1. Open **Scrape Data → Facebook** and click **Log in to Facebook**. On the hosted platform, complete Facebook sign-in and any verification in the embedded browser, then click **Save Facebook login**. Keep that browser open until saving finishes.
2. If the connection says **Saved**, use **Check Facebook connection** before collecting. A saved login may need renewal.
3. In **Your links**, paste URLs into **Add links (one per line)**. Valid links are saved and selected when you finish editing. Use **Sources** or **Select all** to choose the links for this run.
4. Set **Scroll Iterations**. More scrolling may expose more posts but takes longer and does not guarantee complete coverage. Hosted collection uses the headless setting shown in the interface.
5. Click **Collect posts**, wait for results, then enter a custom title and **Save data to cloud**.

During hosted Facebook collection, you can switch workspaces and return to its progress. **Cancel collection** stops the run and keeps completed results where available. An expired Facebook connection can stop the remaining sources; reconnect and retry those links.

**Clear** deselects sources and clears pasted input; saved links remain. **Remove … saved links** removes the selected links from your source library, while previously collected posts remain saved. **Refresh** reloads your saved links.

Facebook comments may be stored with their parent post. Read the evidence source label to distinguish the post author's words from a comment. Collection depends on what Facebook exposes to the connected account.

## Collect Reddit, Online news and X

### Reddit

Open **Scrape Data → Reddit**. The current collector targets **r/cambodia**. Set **Reddit posts per source** and **Comments per post**, then click **Collect posts**. Review the returned posts/comments, enter a custom title and save the results.

The collector uses approved Reddit API access. It defaults to newest posts and samples returned comments; it does not guarantee every comment in a thread. Posts and comments are separate records, with parent context retained for comments. If Reddit is not connected, the administrator must configure access before collection can succeed.

### Online news

Open **Scrape Data → Online news**. Enable the categories you want, edit their search terms, choose **Published**, select **Google News editions**, set **Articles per search**, optionally enter a **Dataset title**, and click **Search Google News**.

Review the cloud-save confirmation, article counts, keyword flags, risk indicator and publication-day chart. The entire collected article set saves automatically. The **Show** and **Category** controls only filter the preview.

A news record contains the returned headline/snippet and source metadata; it is not necessarily the full publisher article. For full page text, use a direct publisher URL in **Custom website**. The articles and the timeline snapshot are saved separately and each reports its save result. Recollecting an article updates its dataset and metadata; unchanged text keeps its saved analysis, while changed text returns to pending analysis.

### X

Open **Scrape Data → X** and follow three steps:

1. **Name your dataset:** Enter a **Dataset name** and click **Continue to options**. The name is required; collection options appear only after you continue.
2. **Choose collection options:** Enable the searches you want and edit their terms. Set **Posts per search** and **Replies per post**, review the read/cost estimate, then click **Continue to collect**. At least one enabled search needs search terms.
3. **Collect posts:** Review the dataset name and collection summary, then use the separate **Collect posts** button to start. Posts and collected replies save automatically under your chosen name; check the cloud-save confirmation.

Use **Back to options** or **Back to dataset name** to revise your choices without losing them. Moving between steps does not collect posts. If cloud saving fails, **Retry saving collected records** stays available even while you edit an earlier step, and uses the original collection's name.

Recent search covers about the last seven days. Reposts are excluded. Replies are optional, capped samples and are off by default. The API may return more records than the app retains because its smallest reply page is larger than some requested samples. The displayed estimate is a guide; actual access, rates and credit depend on the provider account.

X replies are separate records with parent context. If a search fails, completed records remain available and the collection notice explains the problem.

## Run sentiment analysis

1. Open **Sentiment Analysis** and click **Start sentiment analysis** (or **Continue**) to open the two-step dialog.
2. In **Choose and edit data**, choose **Manual selection** or **Last scraped data**. For manual selection, choose **Record sources**, then one or more **Datasets to analyze**. Dates and times are shown in UTC. No sources or datasets are preselected in a new session. Reusing a title groups records together; older untitled records appear under **Untitled (older records)**.
3. Records appear immediately below your choices. There is no load button or record limit. Only the selected sources and datasets appear. **Last scraped data** includes exactly that collection's records and saves an unsaved collection before opening it. **Refresh datasets** refreshes both the dataset list and its records.
4. Edit the **Include** checkboxes directly in the table. Eligible records start included unless previously excluded. Unchecking a record saves a reversible exclusion with the shared dataset; check it again to restore it. Source text and saved analysis are kept. Exclusions persist when you reopen the dataset. If saving fails, use **Retry saving changes**; analysis stays blocked until the edits save.
5. The **Settings** tab contains **Record status** (default **all**), **AI provider**, and the Gemini model controls. Use **pending** or **error** when you want to analyze only those statuses. Gemini and ChatGPT use server credentials.
6. Click **Next** to **Analyze**, enter any Thai projects, companies or brands to watch, and check the included count on **Run Gemini** or **Run ChatGPT**. Only those included records are sent when you click Run. The dialog closes after the run and the processed/error summary appears on the page. Successful analyses are saved automatically.

The results include original text, an English translation, topics, a sentiment label, a priority/risk score and an evidence-backed Thailand monitoring assessment. The current sentiment categories are **Joy**, **Anger**, **Anxiety** and **Neutral**. Sentiment and Thailand relevance are separate: negative language alone does not establish a Thailand-related signal.

| Signal | How to read it |
| --- | --- |
| Negative sentiment | Supported criticism or negative feelings about a relevant target. |
| Reported tension | Supported disputes, protests or escalation. |
| Explicit boycott call | The source itself advocates refusing to buy, support or participate. |
| Reporting a boycott | The source describes someone else's boycott. |
| Opposing a boycott | The source argues against a boycott. |
| Needs review / unclear | The connection, intent or evidence is insufficient or ambiguous. |

Confidence is a model assessment, not a calibrated probability. Check exact source quotes and validation notes. A comment should not inherit the parent's position merely because it appears under that post. Long inputs may be excerpted; a translation is not a substitute for the original evidence.

To reopen work later, choose your sources and datasets in **Manual selection**. Their included records' saved analyses appear automatically under **View processed records**, without another model run. Changing datasets, sources, selection mode or record status refreshes the view and clears previous run results; saved exclusions remain. The sentiment timeline summarizes runs within the selected sources. **Last scraped data** is available only for the current browser session and shows the full timeline.

## Review evidence and export reports

Both the latest results and loaded processed records offer report views:

- **Comment Sentiment Analysis** summarizes sentiment, signals and the selected sample.
- **Velocity Tracker** shows engagement changes for eligible posts with saved collection checks.
- **Negative Post Reports** gives an individual report for each negative or flagged record.
- **Evidence by record** lets you inspect translations, original excerpts, exact quotes and assessment details.

In **Evidence by record**, use **Show findings** to inspect flagged signals, boycott calls, boycott reports, negative sentiment, tensions or records needing review. **Export findings CSV** exports the findings matching that filter. Record counts describe the collected sample; they do not measure unique people or population-wide opinion.

Use **Download report (HTML)** for a shareable report, or click **Create PDF** and then **Download PDF** when it is ready. Reports use the result set loaded into that report view; the evidence filter does not narrow the whole report download. To change the report's scope, change which records you analyze or load.

PDF export depends on the server's browser installation. If it is unavailable, download the HTML report and use your browser's print/save-as-PDF feature. Exports are copies: later corrections or deletions in the app do not update downloaded files.

## Read trends and repeat checks

Open **Sentiment Trends** after running an analysis or a news search. Use **Refresh timeline** if a recent result is missing, and expand **History** to inspect the saved points.

**Sentiment over time** has one point per saved AI analysis or Online News scan. The scale runs from −100 to +100: higher is more positive. AI points count Joy as positive and Anger/Anxiety as negative, using successfully analyzed records. News points use positive minus negative headline classifications from a keyword scan. These are different methods; check the point's source before comparing them. The dashed average gives each point equal weight, regardless of sample size.

Red rings mark border-tension or boycott signals. **Border tension risk over time** is a separate news-scan indicator, with its own **Refresh risk timeline** control. Its levels and alerts are keyword-derived leads for review, not predictions or confirmation that an event occurred.

For meaningful comparisons, repeat collection using similar sources, date windows, limits and AI provider/model. A change in the source mix or sample size can move the result even when underlying sentiment has not changed. Source scope in **Sentiment Analysis** also affects which analysis runs appear in the timeline.

To build a **Velocity Tracker** history, recollect the same eligible posts at later times and save each collection. Running AI again on an unchanged saved snapshot does not create a new engagement observation. Two observations are needed to compare change; Live/Slowing/Dead describe measured growth between checks. Missing counts are not zero. Standalone comments, news articles and custom website pages are not engagement-tracked posts.

## Create a sentiment-based content plan

The sidebar's **Content plan** workspace is titled **Content recommendations**. It uses analyzed source records to suggest ways to address recurring concerns.

1. In **Sentiment Analysis**, choose a dataset with saved analyses or run an analysis.
2. Open **Content plan** and choose **Latest analysis** or **Loaded processed records** under **Use sentiment records from**.
3. Select up to 30 records in **Records to use**. Include the concerns you want the plan to address.
4. Enter **Brand or topic** and **What should the content improve?**. Optionally add a **Focus keyword or concern**.
5. Choose **Content language** (English, Khmer or Thai) and a configured **Recommendation provider**.
6. Click **Generate content plan**. Review each primary keyword, supporting keywords, concern, format, outline and suggested draft.
7. Open **Source evidence and facts to verify**. Check the quoted records and verify operational facts before publishing. Use **Download keywords (CSV)** and **Download content plan (JSON)** to keep the results.

**Example inputs:** Brand/topic: `Example Brand delivery service`. Goal: `Explain delivery timelines and answer refund questions using verified policies.` Focus: `delivery delays`. Use actual analyzed customer concerns as evidence; the example inputs do not supply company facts.

The plan's baseline counts describe selected records. Keywords are suggested phrases without measured search volume, and suggested content does not guarantee improved sentiment. Some incomplete or unsupported suggestions may be omitted. Change the source selection, brief or provider/model to generate a plan for a new context; an old plan can disappear when that context changes.

Plans stay in the session unless exported. The platform generates drafts; publishing them is a separate action. After publishing through your normal channels, collect comparable feedback and compare subsequent sentiment runs.

## Use GenCite for AI share of voice

GenCite tests how often model answers mention your brand. It starts from customer queries and can be used without collecting social posts or website records first.

1. Open **GenCite → Analyze** and enter a **Query or topic**, such as `best delivery services for small businesses`.
2. Click **Generate query variations**, or add/edit rows in **Queries to test**. Tick the **Test** column for the queries you want. Start with a small set; up to 50 queries can be selected.
3. Enter **Brand to track**. Add comma-separated aliases and competitors if useful, plus an optional market and region.
4. Select the configured **Providers**. Each selected provider generates five sampled answers per query per round.
5. Click **Run analysis on … queries**. Review overall share of voice, answers mentioning the brand, the provider breakdown and the per-query table.
6. Enter an **Analysis name** and click **Save analysis** to keep the result in your account. Use **Saved analyses → Open** to restore it later.

Share of voice is the percentage of sampled AI answers that mention your brand or an alias, using case-insensitive matching. For example, 6 mentions in 10 returned answers is 60%. It is not market share, verified source citation coverage or a search-engine ranking. Failed provider requests can reduce the sample; read any provider notices.

**Run again** adds another sampling round to the result's totals. Save again if you want to preserve that updated result; saving creates a new copy. Samples can vary even with the same queries.

In **Recommendations**, generate content recommendations or keyword ideas based on measured query gaps. Competitor inputs help reveal those gaps. Open **View brief** or **View evidence** to inspect the supporting queries. **Mark as published** records a tracking status; it does not publish content to an external site. **Queue for testing**, followed by **Test … queued keywords**, runs those keywords as a new query set; review the resulting analysis in **Analyze**.

**Saved analyses** offers Refresh, Open, Rename and Delete. Deleting requires confirmation. Saved GenCite analyses are private to your account. Use **Check provider access → Run check** if a configured model cannot answer.

## Save, share and manage your work

| Item | How to keep it | Where it lives |
| --- | --- | --- |
| Facebook source links | Add valid links in Your links. | Your account's saved source library. |
| Website URL presets | Enter a New preset name and Save preset; use Update preset after editing. | Your account's saved URL presets, available on any device. |
| Collected posts, articles and pages | Online news, X and Custom website save automatically; Facebook and Reddit use Save data to cloud. | The shared cloud record library. |
| Successful sentiment analysis | Saved automatically after processing. | With the source record; choose the dataset to view its saved analyses. |
| Trend points | Saved automatically when an analysis/news scan succeeds and storage is available. | Sentiment/news history. |
| Sentiment content plans | Download keywords CSV and/or content-plan JSON. | Exported files; the on-screen plan is session-only. |
| GenCite results | Give the result a name and click Save analysis. | Your account's Saved analyses. |
| Reports | Download HTML, PDF or findings CSV. | Your downloaded copies. |

Approved users share the cloud source-record library. A **Custom title** labels a record; it does not create a private project or restrict access. Facebook source-link libraries and saved GenCite analyses are account-specific.

To manage or delete saved records:

1. Open **Manage records** in the sidebar and click **Load / refresh records**.
2. Choose a **Dataset**, **Source** and/or **Status**, or use **Search records** to find a saved title, page title, URL or record ID. All analysis statuses are available, including pending and error records.
3. Review the table. Choose individual **Records to delete**, or click **Select all records on this page**. Nothing is selected automatically. The displayed count tells you exactly how many records are included.
4. Click **Review deletion of … record(s)** and check the preview. Click **Permanently delete … record(s)** to proceed, or **Cancel deletion** to keep them. Deletion cannot be undone and affects every user of the shared library.
5. Read the result. Records changed by another operation after you loaded them are kept; refresh to review their current versions. If a deletion cannot be confirmed, reload the library before retrying.

To delete a whole dataset, choose its title under **Dataset**, click **Review dataset deletion**, inspect the full preview, then click **Permanently delete dataset**. **Cancel dataset deletion** keeps the data. This action includes all reviewed records under that title, across all pages, sources and statuses; table search and source/status filters do not restrict it. Large datasets are processed in batches under one confirmation, so you do not need to select each page. Records changed or added since loading are kept, and you can refresh and review them again. A dataset disappears from the list when no records remain.

If a batch fails, the result shows how many records were confirmed deleted and how many remaining deletions could not be confirmed. Refresh the library before retrying. Individual-record deletion still supports up to 200 selected records per confirmation. Changing a filter, search or page cancels the previous deletion review.

Deleting a record removes its saved sentiment analysis too. Timeline summaries, saved source links, saved GenCite analyses and downloaded exports remain. The current session's collection copies and loaded results are cleared of deleted records. Collecting and saving a URL again can add it back to the library. An analysis already running cannot recreate a source that has been deleted.

The earlier shortcut is also available: in **Sentiment Analysis**, load processed records, expand **Delete processed records**, select records, and review and confirm deletion. This shortcut handles processed records only; **Manage records** covers all statuses.

Removing Facebook source links is different from deleting source records. Sign-out clears the current session's loaded data; saved cloud records remain available subject to access permissions. Save or export work before leaving a session.

## Troubleshooting and common questions

| What you see | What to do |
| --- | --- |
| Access denied at Google sign-in | Use the approved account. Ask the administrator to check the allowed-user list if needed. |
| Facebook login is Saved or disconnected | Click Check Facebook connection. If renewal is requested, Log in to Facebook, finish verification and Save Facebook login. |
| A source is not configured | Ask the administrator to enable that source's credentials. Other configured collectors remain usable. |
| Website link rejected | Use a public HTTP/HTTPS page on a standard web port. Social media and private-network links are not accepted here. |
| No readable website text, HTTP 403, or verification page | Try a direct public article URL. JavaScript-only, login and CAPTCHA pages may be unavailable. |
| Website collection reaches a size/time limit | Try fewer links or shorter pages. Save completed results before starting another run. |
| Cloud saving failed after collection | Keep the page open and click Retry saving collected records. The original data/title are reused; collection stays paused until saving succeeds. |
| No records in Sentiment Analysis | Choose sources and datasets in Manual selection, or Last scraped data. Records open automatically; check Record status, Refresh datasets, or use Retry opening dataset after a connection failure. |
| No included records | Check Include beside the records you want to analyze in Choose and edit data, then continue to Analyze. |
| Source library could not be loaded | Refresh Your links in the Facebook tab, then retry loading. If storage still fails, contact the administrator. |
| AI run control disabled | Check the selected provider's configuration and that eligible records are selected. Credentials are managed on the server. |
| Provider timeout, quota or rate-limit notice | Try a smaller batch or retry later. The administrator may need to check provider access or billing. |
| Incomplete plan or unsupported source evidence | Retry with fewer relevant analyzed records and a clear goal. Read any detailed validation notice; switch to another configured provider if it persists. |
| Content plan shows no records | Choose a dataset with saved analyses or run sentiment analysis first, then choose the matching source mode in Content plan. |
| A plan disappeared | Its source selection, brief, provider or model may have changed, or the session restarted. Regenerate it and export a copy. |
| Trends are empty or stale | Complete an analysis or news scan, refresh the timeline and check source scope. A failed history write may need an administrator's help. |
| PDF creation fails | Download the HTML report; ask the administrator to check the server's Playwright/Chromium installation. |
| GenCite run button is disabled | Enter a brand, select at least one query and choose a configured provider. |
| GenCite score changes between runs | Sampling varies. Check queries, aliases, providers, completed answers and whether Run again added a round. |

When reporting a problem, include the workspace, action, provider/source, error message and approximate time. Avoid including API keys, passwords or login cookies. You can describe a sensitive source instead of sharing its full contents.

## Administrator setup and maintenance

Users do not enter API secrets in collection or analysis forms. Configure credentials in the hosting service's secret settings; for Railway, use the service's Variables panel. The repository README contains the detailed deployment instructions.

| Capability | Administrator configuration |
| --- | --- |
| Google sign-in | Firebase web configuration and the approved-user list (`FAQ_EDITOR_EMAILS`). |
| Cloud library | Firebase project, collection and server Admin SDK credentials with appropriate access. |
| Gemini | `GEMINI_KEY` or `GEMINI_API_KEY`; optional model configuration. |
| ChatGPT | `OPENAI_API_KEY`; optional model configuration. |
| Hosted Facebook browser | `BROWSERBASE_API_KEY` and `BROWSERBASE_PROJECT_ID`; users then connect their own Facebook sessions. |
| Reddit | Approved Reddit app credentials and user agent; a refresh token when the approved app uses it. |
| X | `X_BEARER_TOKEN`, recent-search access and available API credit. |
| Custom websites and news | Server network access to the requested sites; no separate collection API key. |
| PDF export | Playwright and its compatible Chromium browser installed on the server. |

The same Gemini/OpenAI server keys support sentiment analysis, Content plan and GenCite. Providers, hosted browsers and X reads may incur charges; review usage in their respective accounts. A configured credential does not by itself guarantee model access, quota or provider credit.

After configuration or deployment, verify sign-in, a small collection/save, one-record analysis and report export. Keep an eye on storage errors and provider notices. Only enable sources that are ready to use; no tutorial action automatically deploys the application or changes server settings.
