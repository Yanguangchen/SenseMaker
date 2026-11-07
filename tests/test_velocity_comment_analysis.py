"""Comment attribution, aggregation, persistence, and the notepad Analysis tab."""

from copy import deepcopy
from contextlib import asynccontextmanager
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

from modules import velocity_comment_analysis as analysis, velocity_jobs as jobs, velocity_tracking as store
from modules.velocity_analysis_table import analysis_table
from modules.velocity_scraper import read_post_comments
from modules.saved_links import library_owner_id
from tests import test_velocity_runs as runs, test_velocity_tracking as fixtures

EMAIL = "owner@example.com"
URL = fixtures.post_url()


def sample_row():
    return {"post_id": "p", "comments": 100, "comment_collection": {"status": "ok"},
            "comment_sample": [{"id": str(i), "text": text} for i, text in enumerate(
                ["Love the service", "Angry about the service", "Worried about prices", "Prices are listed"]) ]}


def fake_analysis(records, **kwargs):
    sentiments = ["Joy", "Anger", "Anxiety", "Neutral"]
    return {record["_id"]: {"sentiment": sentiments[i % 4], "topics": ["Service", "service", "Prices"]}
            for i, record in enumerate(records)}


class CommentAnalysisTests(unittest.TestCase):
    def test_buckets_topics_and_real_record_ids(self):
        row = sample_row()
        with patch.object(analysis, "analyze_posts", side_effect=fake_analysis) as analyze:
            analysis.analyze_comments([row], provider="openai", model_name="configured-model")
        saved = row["comment_analysis"]
        self.assertEqual(saved["counts"], {"negative": 2, "neutral": 1, "positive": 1})
        self.assertEqual(saved["analyzed_count"], 4)
        self.assertEqual(saved["status"], "ok")
        self.assertEqual(saved["keywords"], [{"word": "prices", "count": 4}, {"word": "service", "count": 4}])
        records = analyze.call_args.args[0]
        self.assertEqual(len({record["_id"] for record in records}), 4)
        self.assertTrue(all(record["source_type"] == "facebook_comment" for record in records))
        self.assertEqual(analyze.call_args.kwargs["provider"], "openai")
        self.assertEqual(analyze.call_args.kwargs["model_name"], "configured-model")

    def test_partial_and_failed_analysis_never_invent_neutral_comments(self):
        def partial(records, **kwargs):
            return {records[0]["_id"]: {"sentiment": "Joy", "topics": []},
                    records[1]["_id"]: {"sentiment": "invalid"}}
        row = sample_row()
        with patch.object(analysis, "analyze_posts", side_effect=partial):
            analysis.analyze_comments([row])
        self.assertEqual(row["comment_analysis"]["status"], "partial")
        self.assertEqual(row["comment_analysis"]["failed_count"], 3)
        self.assertEqual(row["comment_analysis"]["counts"]["neutral"], 0)
        with patch.object(analysis, "analyze_posts", side_effect=RuntimeError("secret error")):
            analysis.analyze_comments([row])
        self.assertEqual(row["comment_analysis"]["status"], "error")
        self.assertEqual(row["comments"], 100)
        self.assertNotIn("secret", json.dumps(row))

    def test_empty_and_unreadable_are_distinct_and_do_not_call_ai(self):
        rows = [{"post_id": "empty", "comment_collection": {"status": "empty"}}, {"post_id": "unreadable"}]
        with patch.object(analysis, "analyze_posts") as analyze:
            analysis.analyze_comments(rows)
        analyze.assert_not_called()
        self.assertEqual([row["comment_analysis"]["status"] for row in rows], ["empty", "unavailable"])

    def test_bounds_unicode_and_deduplication(self):
        row = sample_row()
        row["comment_sample"] = [{"id": str(i), "text": "សួស្តី" * 2000} for i in range(30)]
        with patch.object(analysis, "analyze_posts", side_effect=fake_analysis):
            analysis.analyze_comments([row])
        self.assertEqual(row["comment_analysis"]["collected_count"], 20)
        self.assertTrue(all(len(c["text"].encode()) <= 2000 for c in row["comment_sample"]))
        duplicate = {"post_id": "d", "comment_sample": [{"id": "", "text": "same"}] * 5}
        with patch.object(analysis, "analyze_posts", side_effect=fake_analysis):
            analysis.analyze_comments([duplicate])
        self.assertEqual(duplicate["comment_analysis"]["collected_count"], 1)

    def test_table_has_order_colors_percentages_and_escaped_remarks_topics(self):
        row = sample_row()
        with patch.object(analysis, "analyze_posts", side_effect=fake_analysis):
            analysis.analyze_comments([row])
        row["comment_analysis"]["keywords"].append({"word": "<script>topic</script>", "count": 1})
        card = {"posts": [{"id": "p", "url": URL, "remark": '<script>note</script>'}], "runs": [{"results": [row]}]}
        html = analysis_table(card)
        self.assertLess(html.index('>Negative<'), html.index('>Neutral<'))
        self.assertLess(html.index('>Neutral<'), html.index('>Positive<'))
        self.assertLess(html.index('>Remarks<'), html.index('>Word cloud<'))
        self.assertIn('sm-analysis-negative', html)
        self.assertIn('50.0%', html)
        self.assertIn('25.0%', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;note', html)
        self.assertIn('&lt;script&gt;topic', html)
        row["comment_analysis"].update(status="error", analyzed_count=0)
        self.assertNotIn('0.0%', analysis_table(card))


class CommentRunTests(runs.RunWorkerTests):
    # The inherited worker tests also ensure engagement behavior is preserved.
    def test_run_analyzes_before_saving_and_retry_does_not_repeat_either_service(self):
        row = {**runs.run_for(self.card)["results"][0], **sample_row()}
        row["post_id"] = store.card_posts(self.card)[0]["id"]
        def inspect_analysis(records, **kwargs):
            self.assertEqual(jobs.latest_job(EMAIL).status, "analyzing")
            with self.assertRaisesRegex(ValueError, "Finish the current"):
                jobs.start_run(EMAIL, self.card["id"], hosted=True)
            return fake_analysis(records)
        self.db.fail_writes = True
        with patch.object(jobs, "collect_engagement", new=AsyncMock(return_value=[row])) as scrape, \
                patch.object(analysis, "analyze_posts", side_effect=inspect_analysis) as analyze:
            job = jobs.start_run(EMAIL, self.card["id"], hosted=True, provider="openai")
            self.assertEqual(job.status, "save_failed")
            self.db.fail_writes = False
            store.save_remark(self.db, EMAIL, self.card["id"], row["post_id"], "Keep after run")
            jobs.retry_save(EMAIL, job.id)
        self.assertEqual(job.status, "complete")
        self.assertEqual(scrape.await_count, 1)
        self.assertEqual(analyze.call_count, 1)
        saved = store.load_cards(self.db, EMAIL)[0]
        self.assertEqual(store.card_posts(saved)[0]["remark"], "Keep after run")
        self.assertNotIn("comment_sample", saved["runs"][-1]["results"][0])
        self.assertEqual(saved["runs"][-1]["results"][0]["comment_analysis"]["analyzed_count"], 4)
        archive = next(value for key, value in self.db.rows.items() if '/runs/' in key)
        self.assertEqual(len(archive["results"][0]["comment_sample"]), 4)
        self.assertEqual(len(archive["results"][0]["comment_analysis_details"]), 4)
        store.save_run(self.db, EMAIL, self.card["id"], runs.run_for(self.card, 2))
        self.assertNotIn("comment_analysis", store.load_cards(self.db, EMAIL)[0]["runs"][0]["results"][0])
        self.assertIn("comment_analysis", archive["results"][0])


class CommentPipelineTests(fixtures.VelocityTestCase):
    def test_hosted_run_uses_one_browser_and_saves_comment_analysis_after_it_closes(self):
        card = store.add_card(self.db, EMAIL, url=URL)[0]
        closed, calls = [], []
        jobs._jobs.clear()
        self.addCleanup(jobs._jobs.clear)

        @asynccontextmanager
        async def browser_context(playwright, owner, **options):
            calls.append(owner)
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            await context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=(
                f'<article role="article"><a href="{URL}">Post</a><span>12 likes</span><span>1 comment</span>'
                '<article role="article" aria-label="Comment by Person"><div dir="auto">Great service</div>'
                f'<a href="{URL}?comment_id=1">1h</a></article></article>')))
            try:
                yield context
            finally:
                await browser.close()
                closed.append(True)

        def analyze(records, **kwargs):
            self.assertEqual(closed, [True])
            self.assertEqual(records[0]["raw_text"], "Great service")
            return fake_analysis(records)

        with patch.object(jobs.database, "init_db", return_value=self.db), \
                patch.object(jobs, "active_collection", return_value=None), \
                patch('modules.velocity_scraper.remote_facebook_context', side_effect=browser_context), \
                patch.object(analysis, 'analyze_posts', side_effect=analyze):
            job = jobs.start_run(EMAIL, card["id"], hosted=True)
            deadline = time.monotonic() + 15
            while job.status in {"running", "analyzing", "saving"} and time.monotonic() < deadline:
                time.sleep(.05)
        self.assertEqual(job.status, "complete", job.error)
        self.assertEqual(calls, [EMAIL])
        result = store.load_cards(self.db, EMAIL)[0]["runs"][-1]["results"][0]
        self.assertEqual(result["engagement"], 13)
        self.assertEqual(result["comment_analysis"]["counts"], {"negative": 0, "neutral": 0, "positive": 1})

    def test_maximum_comment_sample_stays_below_firestore_document_limit(self):
        row = sample_row()
        row["comment_sample"] = [{"id": str(i), "text": "😀" * 1000} for i in range(20)]
        rows = [{**deepcopy(row), "post_id": str(i)} for i in range(10)]
        with patch.object(analysis, "analyze_posts", side_effect=fake_analysis):
            analysis.analyze_comments(rows)
        run = {"id": "a" * 64, "results": rows}
        self.assertLess(len(json.dumps(run, ensure_ascii=False).encode()), 800_000)


class CommentDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def load(self, body):
        await self.page.route("**/*", lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=body))
        await self.page.goto(URL)

    def comment(self, ident, text):
        return (f'<article role="article" aria-label="Comment by Person"><a href="/person"><span dir="auto">Person</span></a>'
                f'<div dir="auto">{text}</div><a href="{URL}?comment_id={ident}"><span dir="auto">1h</span></a>'
                '<button><span dir="auto">Like</span></button><button>Reply</button></article>')

    async def test_only_requested_post_bodies_and_distinct_comment_ids(self):
        await self.load(f'<article role="article"><a href="{URL}">Post</a><div data-ad-preview="message">Post text</div>'
                        + self.comment(1, 'Great service') + self.comment(2, 'Great service')
                        + '<article role="article" aria-label="Comment by Person" style="display:none"><div dir="auto">Hidden</div></article>'
                        + '</article><article role="article"><a href="/other/posts/999">Other</a>'
                        + self.comment(3, 'Wrong post') + '</article>')
        result = await read_post_comments(self.page, URL, 3)
        self.assertEqual(result["comment_sample"], [{"id": "1", "text": "Great service"}, {"id": "2", "text": "Great service"}])
        self.assertEqual(result["comment_collection"]["status"], "ok")

    async def test_scoped_expansion_and_twenty_comment_limit(self):
        body = f'<article role="article"><a href="{URL}">Post</a>' + self.comment(1, 'First')
        extra = ''.join(self.comment(i, f'Comment {i}') for i in range(2, 30))
        body += '<button id="more" onclick="this.insertAdjacentHTML(\'beforebegin\', document.getElementById(\'template\').innerHTML);this.remove()">View more comments</button></article>'
        body += f'<template id="template">{extra}</template><button onclick="throw Error()">View more comments</button>'
        await self.load(body)
        result = await read_post_comments(self.page, URL, 100)
        self.assertEqual(len(result["comment_sample"]), 20)
        self.assertEqual(result["comment_sample"][-1]["text"], 'Comment 20')

    async def test_parent_comment_and_reply_do_not_duplicate_text(self):
        await self.load(f'<article role="article"><a href="{URL}">Post</a>'
                        '<article role="article" aria-label="Comment by Parent"><div dir="auto">Parent body</div>'
                        f'<a href="{URL}?comment_id=1">1h</a>' + self.comment(2, 'Reply body') + '</article></article>')
        result = await read_post_comments(self.page, URL, 2)
        self.assertEqual([c["text"] for c in result["comment_sample"]], ['Parent body', 'Reply body'])

    async def test_opens_collapsed_comments_using_only_the_posts_count_button(self):
        await self.load(f'<article role="article"><a href="{URL}">Post</a>'
                        '<button onclick="document.getElementById(\'comments\').hidden=false;this.remove()">1 comment</button>'
                        '<button onclick="throw Error()">Comment</button>'
                        '<div id="comments" hidden>' + self.comment(1, 'Now visible') + '</div></article>')
        result = await read_post_comments(self.page, URL, 1)
        self.assertEqual(result["comment_sample"], [{"id": "1", "text": "Now visible"}])

    async def test_empty_unavailable_and_wrong_post(self):
        await self.load(f'<article role="article"><a href="{URL}">Post</a></article>')
        self.assertEqual((await read_post_comments(self.page, URL, 0))["comment_collection"]["status"], "empty")
        self.assertEqual((await read_post_comments(self.page, URL, 9))["comment_collection"]["status"], "unavailable")
        self.assertEqual((await read_post_comments(self.page, fixtures.post_url(999), 9))["comment_sample"], [])


class AnalysisUITests(fixtures.VelocityUITests):
    def test_tabs_and_cancelled_remark_do_not_change_saved_notes(self):
        card = store.add_card(self.db, EMAIL, url=URL)[0]
        post = store.card_posts(card)[0]
        store.save_remark(self.db, EMAIL, card["id"], post["id"], "Saved")
        app = self.app()
        app.button(key=f"{self.key}_open_{card['id']}").click().run()
        self.assertEqual([tab.label for tab in app.tabs], ["Velocity", "Analysis"])
        app.button(key=f"{self.key}_{card['id']}_edit_remark").click().run()
        self.assertFalse(list(app.exception))
        app.text_area[0].set_value("Unsaved")
        next(button for button in app.button if button.label == "Cancel").click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(store.card_posts(store.load_cards(self.db, EMAIL)[0])[0]["remark"], "Saved")
        self.assertEqual([tab.label for tab in app.tabs], ["Velocity", "Analysis"])

    def test_shared_analysis_is_visible_without_edit_controls(self):
        card = store.add_card(self.db, "teammate@example.com", url=URL)[0]
        row = sample_row()
        row["post_id"] = store.card_posts(card)[0]["id"]
        with patch.object(analysis, "analyze_posts", side_effect=fake_analysis):
            analysis.analyze_comments([row])
        store.save_run(self.db, "teammate@example.com", card["id"], {**runs.run_for(card), "results": [row]})
        with patch("modules.auth.allowed_emails", return_value={EMAIL, "teammate@example.com"}):
            app = self.app()
            next(button for button in app.button if button.label.startswith("Open Velocity")).click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual([tab.label for tab in app.tabs], ["Velocity", "Analysis"])
            self.assertFalse(any(button.label in {"Run", "Edit remarks"} for button in app.button))
            self.assertTrue(any('50.0%' in el.proto.body for el in app.get("html")))


if __name__ == "__main__":
    unittest.main()
