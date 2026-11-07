import asyncio
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from streamlit.testing.v1 import AppTest

from modules import collection_jobs as jobs


class CollectionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.scope = patch.dict(jobs._jobs, {}, clear=True)
        self.scope.start()
        self.addCleanup(self.scope.stop)

    def wait_for_finish(self, job):
        deadline = time.monotonic() + 3
        while job.status in jobs.ACTIVE_STATUSES and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotIn(job.status, jobs.ACTIVE_STATUSES)

    def test_repeat_clicks_and_other_tabs_reuse_one_owner_job(self):
        with patch("modules.collection_jobs.threading.Thread") as thread:
            first = jobs.start_collection("editor@example.com", ["first"], AsyncMock())
            second = jobs.start_collection("EDITOR@example.com", ["changed"], AsyncMock())
            self.assertEqual(first, second)
            self.assertEqual(thread.call_count, 1)
            self.assertEqual(jobs.collection_job("editor@example.com", first).urls, ("first",))
            self.assertIsNone(jobs.collection_job("other@example.com", first))
            self.assertNotEqual(jobs.start_collection("other@example.com", ["other"], AsyncMock()), first)

    def test_cancel_waits_for_cleanup_keeps_completed_sources_and_is_idempotent(self):
        started, cleaning, finish_cleanup = threading.Event(), threading.Event(), threading.Event()
        async def run(progress, partial, activity):
            partial([{"url": "finished-source", "raw_text": "completed post"}])
            progress(1, 2, "finished-source")
            activity("second", "Scroll 2 of 5", 3)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                while not finish_cleanup.is_set():
                    await asyncio.sleep(0.01)
        job_id = jobs.start_collection("editor@example.com", ["first", "second"], run)
        job = jobs.collection_job("editor@example.com", job_id)
        self.assertTrue(started.wait(2))
        self.assertEqual(job.completed, 1)
        self.assertEqual(job.activity, {"second": ("Scroll 2 of 5", 3)})
        jobs.cancel_collection("other@example.com", job_id)
        self.assertEqual(job.status, "running")
        jobs.cancel_collection("editor@example.com", job_id)
        self.assertTrue(cleaning.wait(2))
        jobs.cancel_collection("editor@example.com", job_id)
        self.assertEqual(job.status, "cancelling")
        self.assertIs(jobs.active_collection("editor@example.com"), job)
        finish_cleanup.set()
        self.wait_for_finish(job)
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.results[0]["url"], "finished-source")
        self.assertIsNone(jobs.active_collection("editor@example.com"))

    def test_completed_results_remain_available_to_the_owner_across_tab_reruns(self):
        async def run(progress, partial, activity):
            progress(1, 1, "first")
            return [{"url": "first"}]
        job_id = jobs.start_collection("editor@example.com", ["first"], run)
        job = jobs.collection_job("editor@example.com", job_id)
        self.wait_for_finish(job)
        self.assertEqual(job.status, "complete")
        self.assertEqual(job.results, [{"url": "first"}])
        self.assertIsNone(jobs.active_collection("editor@example.com"))


class CollectionProgressUITests(unittest.TestCase):
    script = (
        "from dashboard import _render_collection_workspace\n"
        "from modules.facebook_session import FacebookSession\n"
        "_render_collection_workspace('facebook', 'editor@example.com', FacebookSession('connected', '123'))\n"
    )

    def setUp(self):
        self.addCleanup(patch.stopall)
        patch("dashboard.IS_CLOUD", True).start()
        patch("dashboard._render_facebook_connection").start()
        patch("dashboard._render_link_workspace", return_value=(["https://www.facebook.com/groups/test"], True)).start()
        patch("modules.auth.verify_access", return_value="editor@example.com").start()

    def test_running_job_disables_duplicate_collection_and_hides_old_failure(self):
        job = jobs.CollectionJob("existing", ("https://www.facebook.com/groups/test",))
        with patch("dashboard.active_collection", return_value=job), patch("dashboard.collection_job", return_value=job):
            app = AppTest.from_string(self.script)
            app.session_state["last_scrape_results"] = [{"source_type": "batch_error", "raw_text": "Old busy error"}]
            app.run()
            app.button(key="open_facebook_steps").click().run()
            self.assertFalse(list(app.exception))
            self.assertTrue(app.button(key="collect_facebook_posts").disabled)
            self.assertEqual(app.button(key="collect_facebook_posts").label, "Collecting posts…")
            self.assertTrue(app.button(key="facebook_wizard_step_previous").disabled)
            self.assertNotIn("facebook_wizard_step_next", [button.key for button in app.button])
            self.assertFalse(app.button(key="cancel_facebook_collection").disabled)
            self.assertFalse(any("Old busy error" in warning.value for warning in app.warning))
            self.assertEqual(app.session_state["facebook_collection_job"], "existing")

    def test_running_progress_refreshes_scroll_posts_and_elapsed_in_overview_and_dialog(self):
        url = "https://www.facebook.com/groups/test"
        job = jobs.CollectionJob("existing", (url,), started_at=100,
                                 activity={url: ("Scroll 2 of up to 16", 7)})
        with patch("dashboard.active_collection", return_value=job), \
             patch("dashboard.collection_job", return_value=job), patch("dashboard.time.time", return_value=175):
            app = AppTest.from_string(self.script).run()
            self.assertFalse(app.exception)
            progress = app.get("progress")[0].proto
            self.assertEqual(progress.value, 0)
            self.assertIn("0 of 1 sources finished · 7 posts collected · 1m 15s elapsed", progress.text)
            self.assertTrue(any("Scroll 2 of up to 16" in caption.value for caption in app.caption))
            job.activity = {url: ("Scroll 3 of up to 16", 12)}
            app.button(key="open_facebook_steps").click().run()
            self.assertFalse(app.exception)
            self.assertIn("12 posts collected", app.get("progress")[0].proto.text)
            self.assertTrue(any("Scroll 3 of up to 16" in caption.value for caption in app.caption))

    def test_stopping_progress_keeps_partial_count_and_disables_cancel(self):
        job = jobs.CollectionJob("existing", ("first", "second"), status="cancelling", completed=1,
                                 results=[{"url": "post", "raw_text": "Kept"}, {"source_type": "batch_error"}])
        with patch("dashboard.active_collection", return_value=job), patch("dashboard.collection_job", return_value=job):
            app = AppTest.from_string(self.script).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.get("progress")[0].proto.value, 50)
            self.assertIn("Stopping collection · 1 of 2 sources finished · 1 posts collected", app.get("progress")[0].proto.text)
            self.assertTrue(app.button(key="cancel_facebook_collection").disabled)

    def test_new_collection_clears_the_previous_error_and_attaches_to_worker(self):
        job = jobs.CollectionJob("new", ("https://www.facebook.com/groups/test",))
        started = False
        def start(*args):
            nonlocal started
            started = True
            return job.id
        with patch("dashboard.active_collection", side_effect=lambda email: job if started else None), \
             patch("dashboard.collection_job", return_value=job), patch("dashboard.start_collection", side_effect=start) as launch:
            app = AppTest.from_string(self.script)
            app.session_state["last_scrape_results"] = [{"source_type": "batch_error", "raw_text": "Old busy error"}]
            app.run()
            app.button(key="open_facebook_steps").click().run()
            app.button(key="facebook_wizard_step_next").click().run()
            app.button(key="collect_facebook_posts").click().run()
            self.assertFalse(list(app.exception))
            launch.assert_called_once()
            self.assertIsNone(app.session_state["last_scrape_results"])
            self.assertFalse(any("Old busy error" in warning.value for warning in app.warning))
            self.assertTrue(app.button(key="collect_facebook_posts").disabled)

    def test_cancel_button_requests_owner_cleanup(self):
        job = jobs.CollectionJob("existing", ("https://www.facebook.com/groups/test",))
        with patch("dashboard.active_collection", return_value=job), patch("dashboard.collection_job", return_value=job), \
             patch("dashboard.cancel_collection") as cancel:
            app = AppTest.from_string(self.script).run()
            app.button(key="cancel_facebook_collection").click().run()
            self.assertFalse(list(app.exception))
            cancel.assert_called_once_with("editor@example.com", "existing")

    def test_finished_worker_updates_results_without_starting_another_collection(self):
        job = jobs.CollectionJob("done", ("https://www.facebook.com/groups/test",), status="cancelled",
                                 results=[{"source_type": "batch_warning", "raw_text": "Completed result"}])
        with patch("dashboard.active_collection", return_value=None), patch("dashboard.collection_job", return_value=job), \
             patch("dashboard.start_collection") as launch:
            app = AppTest.from_string(self.script)
            app.session_state["facebook_collection_job"] = "done"
            app.run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.session_state["last_scrape_results"], job.results)
            self.assertIn("cancelled", app.session_state["last_scrape_error"])
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
