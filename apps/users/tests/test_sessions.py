from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from config.mongodb import db
from apps.users.sessions import SessionStore


class MongoSessionStoreTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        cls.collection_name = f"test_sessions_{uuid4().hex}"
        cls.settings_override = override_settings(
            MONGODB_SESSIONS_COLLECTION=cls.collection_name
        )
        cls.settings_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        db.drop_collection(cls.collection_name)
        cls.settings_override.disable()
        super().tearDownClass()

    def test_data_persists_between_store_instances(self):
        original = SessionStore()
        original["user_id"] = "user-123"
        original.save()

        restored = SessionStore(original.session_key)
        self.assertEqual(restored["user_id"], "user-123")

    def test_cycle_key_renews_key_and_preserves_data(self):
        session = SessionStore()
        session["user_id"] = "user-123"
        session.save()
        old_key = session.session_key

        session.cycle_key()

        self.assertNotEqual(session.session_key, old_key)
        self.assertFalse(SessionStore(old_key).exists(old_key))
        self.assertEqual(SessionStore(session.session_key)["user_id"], "user-123")

    def test_expired_session_is_not_loaded(self):
        session = SessionStore()
        session["user_id"] = "user-123"
        session.set_expiry(-1)
        session.save()

        self.assertEqual(SessionStore(session.session_key).load(), {})

    def test_flush_invalidates_session(self):
        session = SessionStore()
        session["user_id"] = "user-123"
        session.save()
        old_key = session.session_key

        session.flush()

        self.assertFalse(SessionStore(old_key).exists(old_key))
        self.assertIsNone(session.session_key)
