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
        async def run(progress, partial):
            partial([{"url": "finished-source", "raw_text": "completed post"}])
            progress(1, 2, "finished-source")
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
        async def run(progress, partial):
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
            self.assertFalse(list(app.exception))
            self.assertTrue(app.button(key="collect_facebook_posts").disabled)
            self.assertFalse(app.button(key="cancel_facebook_collection").disabled)
            self.assertFalse(list(app.warning))
            self.assertEqual(app.session_state["facebook_collection_job"], "existing")

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
            app.button(key="collect_facebook_posts").click().run()
            self.assertFalse(list(app.exception))
            launch.assert_called_once()
            self.assertIsNone(app.session_state["last_scrape_results"])
            self.assertFalse(list(app.warning))
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
