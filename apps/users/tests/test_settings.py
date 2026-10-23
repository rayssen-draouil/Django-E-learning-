import os
from unittest.mock import patch

from django.test import SimpleTestCase


class SettingsEnvironmentTests(SimpleTestCase):
    def test_environment_parsers_handle_boolean_and_host_list(self):
        from config.settings.base import _env_bool, _env_list

        with patch.dict(
            os.environ,
            {"TEST_FLAG": "false", "TEST_HOSTS": "one.example, two.example"},
        ):
            self.assertFalse(_env_bool("TEST_FLAG", True))
            self.assertEqual(
                _env_list("TEST_HOSTS", "localhost"),
                ["one.example", "two.example"],
            )
