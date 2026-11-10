"""Real SDK integration; opt in with the isolated demo Firebase emulators."""
import json
import os
import unittest
import uuid
from unittest.mock import patch
from urllib.request import Request, urlopen

import firebase_admin
from firebase_admin import auth
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore import Client

from modules.auth import verify_access
from modules.database import delete_records


@unittest.skipUnless(os.getenv("SENSEMAKER_EMULATOR_TESTS") == "1", "requires local Firebase emulators")
class FirebaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name in ("FIRESTORE_EMULATOR_HOST", "FIREBASE_AUTH_EMULATOR_HOST"):
            if not os.environ.get(name, "").startswith("127.0.0.1:"):
                raise RuntimeError(f"{name} must point to a loopback emulator")
        cls.app = firebase_admin.initialize_app(options={"projectId": "demo-sensemaker"}, name="integration")
        cls.db = Client(project="demo-sensemaker", credentials=AnonymousCredentials())

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        firebase_admin.delete_app(cls.app)

    def test_delete_preserves_changed_versions_and_commits_reviewed_records(self):
        collection = 'audit_' + uuid.uuid4().hex
        refs = [self.db.collection(collection).document(name) for name in ('reviewed', 'changed')]
        try:
            for ref in refs:
                ref.set({'custom_title': 'Campaign'})
            versions = {ref.id: ref.get().update_time.isoformat() for ref in refs}
            refs[1].update({'custom_title': 'Changed since review'})
            with patch('modules.database._get_collection_name', return_value=collection):
                result = delete_records(self.db, versions)
            self.assertEqual(result, {'deleted': ['reviewed'], 'skipped': ['changed'], 'missing': []})
            self.assertFalse(refs[0].get().exists)
            self.assertTrue(refs[1].get().exists)
        finally:
            for ref in refs:
                ref.delete()

    def test_sdk_identity_verification_and_allowlist(self):
        email = f'{uuid.uuid4().hex}@example.com'
        user = auth.create_user(email=email, email_verified=True, password='emulator-password', app=self.app)
        try:
            endpoint = 'http://' + os.environ['FIREBASE_AUTH_EMULATOR_HOST']
            request = Request(endpoint + '/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=fake',
                              data=json.dumps({'email': email, 'password': 'emulator-password', 'returnSecureToken': True}).encode(),
                              headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=10) as response:
                token = json.load(response)['idToken']
            with patch('modules.auth.init_firebase', return_value=self.app):
                self.assertEqual(verify_access(token, {email}), email)
                with self.assertRaises(PermissionError):
                    verify_access(token, {'other@example.com'})
                auth.update_user(user.uid, disabled=True, app=self.app)
                with self.assertRaises(auth.UserDisabledError):
                    verify_access(token, {email})
        finally:
            auth.delete_user(user.uid, app=self.app)
