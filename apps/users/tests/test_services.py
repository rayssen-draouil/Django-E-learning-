from unittest.mock import MagicMock

from django.contrib.auth.hashers import check_password
from django.test import SimpleTestCase
from pymongo.errors import DuplicateKeyError, PyMongoError

from apps.users.services import (
    DuplicateEmailError,
    UserStorageError,
    authenticate_user,
    create_user,
    ensure_users_indexes,
)


class UserServiceTests(SimpleTestCase):
    def test_index_setup_preserves_unique_email_constraint(self):
        collection = MagicMock()
        ensure_users_indexes(collection)
        collection.create_index.assert_called_once_with(
            "email", unique=True, name="users_email_unique"
        )

    def test_create_user_does_not_create_indexes_and_keeps_password_hashed(self):
        collection = MagicMock()
        user = create_user(
            first_name="Ada",
            last_name="Lovelace",
            email="ADA@example.com",
            password="A-valid-password-123!",
            collection=collection,
        )
        collection.create_index.assert_not_called()
        collection.insert_one.assert_called_once()
        self.assertEqual(user["email"], "ada@example.com")
        self.assertNotIn("password", user)
        stored_password = collection.insert_one.call_args.args[0]["password"]
        self.assertTrue(check_password("A-valid-password-123!", stored_password))

    def test_create_user_supports_mentor_role_and_rejects_admin_role(self):
        collection = MagicMock()
        mentor = create_user(
            first_name="Amine",
            last_name="Benali",
            email="amine@example.com",
            password="A-valid-password-123!",
            role="mentor",
            collection=collection,
        )
        self.assertEqual(mentor["role"], "mentor")
        with self.assertRaises(ValueError):
            create_user(
                first_name="Admin",
                last_name="User",
                email="admin@example.com",
                password="A-valid-password-123!",
                role="admin",
                collection=collection,
            )

    def test_duplicate_email_is_translated(self):
        collection = MagicMock()
        collection.insert_one.side_effect = DuplicateKeyError("duplicate")
        with self.assertRaises(DuplicateEmailError):
            create_user(
                first_name="Ada",
                last_name="Lovelace",
                email="ada@example.com",
                password="A-valid-password-123!",
                collection=collection,
            )

    def test_storage_failure_has_a_generic_user_facing_error(self):
        collection = MagicMock()
        collection.insert_one.side_effect = PyMongoError("mongodb://secret-host/secret")
        with self.assertRaises(UserStorageError) as raised:
            create_user(
                first_name="Ada",
                last_name="Lovelace",
                email="ada@example.com",
                password="A-valid-password-123!",
                collection=collection,
            )
        self.assertNotIn("secret-host", str(raised.exception))

    def test_authentication_connection_error_is_controlled(self):
        collection = MagicMock()
        collection.find_one.side_effect = PyMongoError("connection details")
        with self.assertRaises(UserStorageError) as raised:
            authenticate_user("ada@example.com", "password", collection=collection)
        self.assertNotIn("connection details", str(raised.exception))
