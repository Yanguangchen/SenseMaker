"""Capability scope, revocation, fresh results, and read-only share UI."""

from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from modules import velocity_sharing as sharing, velocity_tracking as store
from tests.test_velocity_tracking import VelocityTestCase, post_url


class SharingTests(VelocityTestCase):
    def setUp(self):
        super().setUp()
        allow = patch('modules.auth.allowed_emails', return_value={'owner@example.com', 'other@example.com'})
        allow.start()
        self.addCleanup(allow.stop)
        self.email = 'owner@example.com'
        self.card = store.add_card(self.db, self.email, 'Watch this', post_url())[0]

    def enable(self):
        return sharing.set_sharing(self.db, self.email, self.card['id'], True)[0]['share_token']

    def test_opt_in_stable_link_and_revocation_with_new_token(self):
        self.assertIsNone(sharing.load_shared_card(self.db, self.card['id']))
        token = self.enable()
        self.assertEqual(token, self.enable())
        self.assertEqual(sharing.load_shared_card(self.db, token)['name'], 'Watch this')
        sharing.set_sharing(self.db, self.email, self.card['id'], False)
        self.assertIsNone(sharing.load_shared_card(self.db, token))
        replacement = self.enable()
        self.assertNotEqual(replacement, token)
        self.assertIsNone(sharing.load_shared_card(self.db, token))
        self.assertIsNotNone(sharing.load_shared_card(self.db, replacement))

    def test_only_creator_can_share_and_unapproved_owner_links_stop_working(self):
        with self.assertRaises(ValueError):
            sharing.set_sharing(self.db, 'other@example.com', self.card['id'], True)
        with self.assertRaises(PermissionError):
            sharing.set_sharing(self.db, 'outside@example.com', self.card['id'], True)
        token = self.enable()
        with patch('modules.auth.allowed_emails', return_value={'other@example.com'}):
            self.assertIsNone(sharing.load_shared_card(self.db, token))
        self.assertIsNotNone(sharing.load_shared_card(self.db, token))
        store.remove_card(self.db, self.email, self.card['id'])
        self.assertIsNone(sharing.load_shared_card(self.db, token))

    def test_scoped_projection_and_new_saved_results(self):
        token = self.enable()
        other = store.add_card(self.db, self.email, 'Private other card', post_url(2))[1]
        post = store.card_posts(self.card)[0]
        store.save_remark(self.db, self.email, self.card['id'], post['id'], 'Shared remark')
        for index in range(10):
            store.save_run(self.db, self.email, self.card['id'], {
                'id': f'{index:064x}', 'at': f'2026-11-09T01:{index:02d}:00+00:00',
                'metric': 'reactions_comments', 'results': [{
                    'post_id': post['id'], 'reactions': index, 'comments': 1, 'outcome': 'ok',
                    'comment_sample': [{'text': 'Raw comment must stay private'}],
                    'comment_analysis': {'status': 'ok', 'analyzed_count': 1, 'counts': {'positive': 1},
                                         'private_detail': 'Internal data'},
                }],
            })
        public = sharing.load_shared_card(self.db, token)
        self.assertEqual(len(public['runs']), 10)
        self.assertEqual(public['posts'][0]['remark'], 'Shared remark')
        self.assertEqual(public['runs'][-1]['results'][0]['reactions'], 9)
        for private in [token, 'Raw comment', 'Internal data', other['id'], self.email, 'Private other card']:
            self.assertNotIn(private, str(public))
        self.assertEqual(public['analysis_results'][0]['comment_analysis']['counts'], {'positive': 1})

    def test_url_discards_existing_query_and_invalid_tokens_never_read_database(self):
        token = self.enable()
        self.assertEqual(sharing.share_url('https://app.example/path?auth=private#section', token),
                         f'https://app.example/path?velocity_share={token}')
        self.db.fail_reads = True
        for value in ['', '../bad', 'f' * 63, 'f' * 65, None]:
            self.assertIsNone(sharing.load_shared_card(self.db, value))

    def test_public_page_is_read_only_and_rechecks_revocation(self):
        token = self.enable()
        script = ('from modules.velocity_sharing_ui import render_shared_card_page\n'
                  f'render_shared_card_page({token!r})')
        with patch('modules.velocity_sharing_ui.database.init_db', return_value=self.db):
            app = AppTest.from_string(script).run()
            self.assertFalse(list(app.exception))
            self.assertEqual([button.label for button in app.button], ['Refresh results'])
            self.assertEqual(app.subheader[0].value, 'Watch this')
            sharing.set_sharing(self.db, self.email, self.card['id'], False)
            app.button[0].click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.subheader), 0)
            self.assertIn('no longer available', app.warning[0].value)

    def test_creator_controls_create_and_revoke(self):
        script = ("from modules import velocity_tracking as store\n"
                  "from modules.velocity_sharing_ui import render_share_controls\n"
                  "from modules.database import init_db\n"
                  "import streamlit as st\n"
                  "card = store.load_cards(init_db(), 'owner@example.com')[0]\n"
                  "if render_share_controls('owner@example.com', card) is not None: st.rerun()")
        with patch('modules.database.init_db', return_value=self.db):
            app = AppTest.from_string(script).run()
            self.assertFalse(list(app.exception))
            app.button[0].click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.button[0].label, 'Revoke share link')
            token = store.load_cards(self.db, self.email)[0]['share_token']
            app.button[0].click().run()
            self.assertFalse(list(app.exception))
            self.assertIsNone(sharing.load_shared_card(self.db, token))


class SharedRouteTests(VelocityTestCase):
    def test_share_route_returns_before_normal_workspace_and_other_urls_require_auth(self):
        import ast
        from pathlib import Path
        from unittest.mock import Mock

        # Execute the real entry function without importing its background scheduler.
        source = ast.parse(Path('dashboard.py').read_text())
        main = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        namespace = {"__file__": str(Path('dashboard.py').resolve()), "Path": Path,
                     "load_environment": Mock(), "_bridge_secrets_to_env": Mock(),
                     "_apply_ui_theme": Mock(), "require_access": Mock(return_value=None)}
        st = Mock()
        st.query_params = {sharing.QUERY_KEY: 'f' * 64}
        namespace['st'] = st
        exec(compile(ast.Module(body=[main], type_ignores=[]), 'dashboard.py', 'exec'), namespace)
        with patch('modules.velocity_sharing_ui.render_shared_card_page') as render:
            namespace['main']()
            render.assert_called_once_with('f' * 64)
            namespace['require_access'].assert_not_called()
            st.query_params = {}
            namespace['main']()
            namespace['require_access'].assert_called_once()
            self.assertEqual(render.call_count, 1)
