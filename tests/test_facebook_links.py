"""The saved Facebook library is complete, account-scoped and independent of scraping."""

import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from modules.saved_links import library_owner_id, load_saved_links, remove_saved_links, save_links
from tests.test_saved_links import MemoryDB


class FacebookLinkInspectorTests(unittest.TestCase):
    script = (
        "import streamlit as st\n"
        "from modules.facebook_links_ui import render_saved_facebook_links\n"
        "render_saved_facebook_links(st.session_state.get('test_email', 'owner@example.com'))\n"
    )
    refresh_key = f"facebook_link_inspector_{library_owner_id('owner@example.com')}_refresh"
    prefix = f"facebook_link_inspector_{library_owner_id('owner@example.com')}"

    def select_rows(self, app, indices, prefix=None):
        key = app.session_state[f"{prefix or self.prefix}_delete_editor_key"]
        app.session_state[key] = {"edited_rows": {i: {"Delete": True} for i in indices},
                                  "added_rows": [], "deleted_rows": []}
        app.run()
        self.assertFalse(app.exception)

    def delete_selected(self, app, prefix=None):
        prefix = prefix or self.prefix
        key = app.session_state[f"{prefix}_delete_editor_key"]
        edits = app.session_state[key]
        app.button(key=f"{prefix}_delete").click()
        # AppTest 1.50 omits data_editor from submitted widget states. Include
        # its real serialized browser value alongside the delete-button click.
        states = app._tree.get_widget_states()
        table = next(frame for frame in app.dataframe if frame.proto.id.endswith(key))
        states.widgets.add(id=table.proto.id, string_value=json.dumps(edits))
        app._run(states)

    def test_delete_selected_links_persists_and_clears_pending_scrape_but_keeps_other_data(self):
        db = MemoryDB()
        urls = [f"https://facebook.com/groups/{name}" for name in ("a", "b", "c")]
        website = "https://example.com/keep"
        save_links(db, "owner@example.com", urls + [website])
        save_links(db, "other@example.com", urls)
        db.records["posts/already-collected"] = {"raw_text": "Collected post", "analysis": {"sentiment": "Joy"}}
        owner = library_owner_id("owner@example.com")
        with patch("modules.facebook_links_ui.init_db", return_value=db):
            app = AppTest.from_string(self.script).run()
            app.session_state["facebook_wizard_targets"] = urls.copy()
            app.session_state[f"links_selection_memo_{owner}_facebook"] = urls.copy()
            app.session_state[f"saved_links_{owner}"] = {"urls": urls + [website], "error": False}
            app.session_state["last_scrape_results"] = [{"url": urls[0], "raw_text": "Collected post"}]
            self.assertTrue(app.button(key=f"{self.prefix}_delete").disabled)
            before = db.records.copy()
            self.select_rows(app, [0, 2])
            self.assertEqual(db.records, before)  # Selection alone does not delete.
            self.delete_selected(app)
            self.assertFalse(app.exception)
            self.assertEqual(set(load_saved_links(db, "owner@example.com")), {urls[1], website})
            self.assertEqual(set(load_saved_links(db, "other@example.com")), set(urls))
            self.assertEqual(db.records["posts/already-collected"], before["posts/already-collected"])
            self.assertEqual(app.session_state["facebook_wizard_targets"], [urls[1]])
            self.assertEqual(app.session_state[f"links_selection_memo_{owner}_facebook"], [urls[1]])
            self.assertEqual(app.session_state[f"saved_links_{owner}"]["urls"], [urls[1], website])
            self.assertEqual(len(app.session_state["last_scrape_results"]), 1)
            self.assertTrue(any("Deleted 2 saved links" in msg.value for msg in app.success))
            reopened = AppTest.from_string(self.script).run()
            self.assertEqual(list(reopened.dataframe[0].value["Facebook URL"]), [urls[1]])
            self.select_rows(reopened, [0])
            self.delete_selected(reopened)
            self.assertFalse(reopened.exception)
            self.assertTrue(any("No Facebook links are saved yet" in msg.value for msg in reopened.info))

    def test_filtering_does_not_apply_selection_to_a_different_link(self):
        db = MemoryDB()
        urls = [f"https://facebook.com/groups/{name}" for name in ("a", "b", "c")]
        save_links(db, "owner@example.com", urls)
        with patch("modules.facebook_links_ui.init_db", return_value=db):
            app = AppTest.from_string(self.script).run()
            self.select_rows(app, [0])
            app.text_input[0].input("/b").run()
            self.assertTrue(app.button(key=f"{self.prefix}_delete").disabled)
            self.select_rows(app, [0])
            self.delete_selected(app)
            self.assertFalse(app.exception)
            self.assertEqual(set(load_saved_links(db, "owner@example.com")), {urls[0], urls[2]})

    def test_delete_failure_keeps_links_selected_for_retry(self):
        db = MemoryDB()
        url = "https://facebook.com/groups/keep-until-confirmed"
        save_links(db, "owner@example.com", [url])
        with patch("modules.facebook_links_ui.init_db", return_value=db):
            app = AppTest.from_string(self.script).run()
            app.session_state["facebook_wizard_targets"] = [url]
            self.select_rows(app, [0])
            db.fail_commit = len(db.commits) + 1
            self.delete_selected(app)
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.assertEqual(load_saved_links(db, "owner@example.com"), [url])
            self.assertEqual(app.session_state["facebook_wizard_targets"], [url])
            db.fail_commit = None
            self.delete_selected(app)
            self.assertFalse(app.exception)
            self.assertEqual(load_saved_links(db, "owner@example.com"), [])

    def test_partial_bulk_delete_reconciles_links_that_really_were_removed(self):
        db = MemoryDB()
        urls = [f"https://facebook.com/groups/{i:03}" for i in range(405)]
        save_links(db, "owner@example.com", urls)
        with patch("modules.facebook_links_ui.init_db", return_value=db):
            app = AppTest.from_string(self.script).run()
            app.session_state["facebook_wizard_targets"] = urls.copy()
            self.select_rows(app, range(len(urls)))
            db.fail_commit = len(db.commits) + 2
            self.delete_selected(app)
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.assertFalse(app.success)
            self.assertEqual(load_saved_links(db, "owner@example.com"), urls[400:])
            self.assertEqual(app.session_state["facebook_wizard_targets"], urls[400:])
            self.assertEqual(list(app.dataframe[0].value["Facebook URL"]), urls[400:])

    def test_delete_inside_your_links_prunes_the_collection_selection(self):
        db = MemoryDB()
        urls = ["https://facebook.com/groups/a", "https://facebook.com/groups/b"]
        save_links(db, "owner@example.com", urls)
        with patch("dashboard.init_db", return_value=db), patch("modules.facebook_links_ui.init_db", return_value=db):
            app = AppTest.from_string(
                "from dashboard import _render_link_workspace\n"
                "_render_link_workspace('owner@example.com', platform='facebook')\n"
            ).run()
            app.button(key="select_all_links_facebook").click().run()
            prefix = f"facebook_link_list_{library_owner_id('owner@example.com')}_facebook"
            self.select_rows(app, [0], prefix)
            self.delete_selected(app, prefix)
            self.assertFalse(app.exception)
            self.assertEqual(app.multiselect[0].value, [urls[1]])
            self.assertEqual(app.multiselect[0].options, [urls[1]])

    def test_complete_library_search_download_and_account_isolation_without_writes(self):
        db = MemoryDB()
        urls = [f"https://www.facebook.com/groups/community-{i}" for i in range(250)]
        save_links(db, "owner@example.com", urls + ["https://example.com/facebook.com"])
        other = "https://www.facebook.com/groups/another-account"
        save_links(db, "other@example.com", [other])
        records = db.records.copy()
        with patch("modules.facebook_links_ui.init_db", return_value=db), \
             patch("modules.facebook_links_ui.st.download_button", wraps=st.download_button) as download:
            app = AppTest.from_string(self.script).run()
            self.assertFalse(app.exception)
            self.assertEqual(set(app.dataframe[0].value["Facebook URL"]), set(urls))
            app.text_input[0].input(" COMMUNITY-24 ").run()
            self.assertEqual(set(app.dataframe[0].value["Facebook URL"]),
                             {url for url in urls if "community-24" in url})
            self.assertEqual(set(download.call_args.args[1].splitlines()), set(urls))
            app.text_input[0].input("does-not-exist").run()
            self.assertFalse(app.dataframe)
            self.assertTrue(any("No saved Facebook links match" in info.value for info in app.info))
            app.session_state["test_email"] = "other@example.com"
            app.run()
            self.assertEqual(list(app.dataframe[0].value["Facebook URL"]), [other])
            self.assertEqual(db.records, records)

    def test_refresh_reflects_additions_and_removals_and_recovers_from_failure(self):
        db = MemoryDB()
        first = "https://facebook.com/groups/first"
        second = "https://m.facebook.com/groups/second"
        save_links(db, "owner@example.com", [first])
        with patch("modules.facebook_links_ui.init_db", return_value=db) as connect:
            app = AppTest.from_string(self.script).run()
            remove_saved_links(db, "owner@example.com", [first])
            save_links(db, "owner@example.com", [second])
            connect.side_effect = RuntimeError("Temporary failure")
            app.button(key=self.refresh_key).click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.warning)
            self.assertEqual(list(app.dataframe[0].value["Facebook URL"]), [first])
            connect.side_effect = None
            app.button(key=self.refresh_key).click().run()
            self.assertFalse(app.warning)
            self.assertEqual(list(app.dataframe[0].value["Facebook URL"]), [second])

    def test_initial_load_failure_is_not_presented_as_an_empty_library(self):
        with patch("modules.facebook_links_ui.init_db", side_effect=RuntimeError("Unavailable")) as connect:
            app = AppTest.from_string(self.script).run()
            self.assertFalse(app.exception)
            self.assertTrue(app.warning)
            self.assertFalse(app.info)
            connect.side_effect = None
            connect.return_value = MemoryDB()
            app.button(key=self.refresh_key).click().run()
            self.assertFalse(app.warning)
            self.assertTrue(any("No Facebook links are saved yet" in info.value for info in app.info))


class FacebookLinkNavigationTests(unittest.TestCase):
    def test_unchecked_saved_login_can_inspect_without_browser_and_revocation_blocks_refresh(self):
        from dashboard import _auto_check_facebook
        from modules.saved_links import load_saved_links

        db = MemoryDB()
        url = "https://www.facebook.com/groups/community"
        save_links(db, "owner@example.com", [url])
        with ExitStack() as stack:
            stack.enter_context(patch("dashboard.IS_CLOUD", True))
            stack.enter_context(patch("dashboard.init_db", return_value=db))
            stack.enter_context(patch("modules.facebook_links_ui.init_db", return_value=db))
            stack.enter_context(patch("dashboard.active_collection", return_value=None))
            stack.enter_context(patch("modules.auth.allowed_emails", return_value={"owner@example.com"}))
            stack.enter_context(patch("modules.auth.web_config", return_value={
                "apiKey": "public", "authDomain": "example.firebaseapp.com",
                "projectId": "example", "appId": "public",
            }))
            stack.enter_context(patch("streamlit.components.v1.declare_component", return_value=lambda **kwargs: None))
            verify = stack.enter_context(patch("modules.auth.verify_access", return_value="owner@example.com"))
            check = stack.enter_context(patch("dashboard._auto_check_facebook", wraps=_auto_check_facebook))
            loader = stack.enter_context(patch("modules.facebook_links_ui.load_saved_links", wraps=load_saved_links))
            browser = stack.enter_context(patch("dashboard.start_login"))
            collect = stack.enter_context(patch("dashboard.start_collection"))
            app = AppTest.from_string(
                "from dashboard import _render_scrape_workspace, require_access\n"
                "from modules.facebook_session import FacebookSession\n"
                "email = require_access()\n"
                "if email:\n"
                "    _render_scrape_workspace(email, FacebookSession('saved'))\n"
            )
            app.session_state["auth_token"] = "signed-token"
            app.run()
            self.assertFalse(app.exception)
            # A saved login is not checked in Browserbase just to visit the source cards.
            check.assert_not_called()
            self.assertEqual([tab.label for tab in app.tabs], ["Collect data", "Saved Facebook links"])
            self.assertEqual(list(app.tabs[1].dataframe[0].value["Facebook URL"]), [url])
            self.assertFalse(app.tabs[0].dataframe)
            self.assertNotIn("focus_workspace_modal", app.session_state)
            check.assert_not_called()
            browser.assert_not_called()
            collect.assert_not_called()

            # Neither deletion nor refresh may access data before authorization.
            for action in ("delete", "refresh"):
                if action == "refresh":
                    verify.side_effect = None
                    app.session_state["auth_token"] = "signed-token"
                    app.run()
                else:
                    editor_key = app.session_state[f"{FacebookLinkInspectorTests.prefix}_delete_editor_key"]
                    app.session_state[editor_key] = {"edited_rows": {0: {"Delete": True}},
                                                     "added_rows": [], "deleted_rows": []}
                    app.run()
                loader.reset_mock()
                before = db.records.copy()
                verify.side_effect = PermissionError("Access revoked")
                app.button(key=f"{FacebookLinkInspectorTests.prefix}_{action}").click().run()
                self.assertFalse(app.exception)
                loader.assert_not_called()
                self.assertEqual(db.records, before)
                self.assertFalse(app.tabs)

    def test_selecting_facebook_still_checks_the_saved_connection(self):
        from modules.facebook_session import FacebookSession

        with patch("dashboard.IS_CLOUD", True), \
             patch("dashboard.init_db"), \
             patch("modules.facebook_links_ui.init_db"), \
             patch("dashboard.active_collection", return_value=None), \
             patch("dashboard._auto_check_facebook", return_value=FacebookSession()) as check:
            app = AppTest.from_string(
                "import streamlit as st\n"
                "from dashboard import _render_scrape_workspace\n"
                "from modules.facebook_session import FacebookSession\n"
                "st.session_state['scrape_source'] = 'facebook'\n"
                "_render_scrape_workspace('owner@example.com', FacebookSession('saved'))\n"
            ).run()
            self.assertFalse(app.exception)
            check.assert_called_once()


if __name__ == "__main__":
    unittest.main()
