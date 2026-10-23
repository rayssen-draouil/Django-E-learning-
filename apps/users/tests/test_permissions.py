from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from apps.users.decorators import roles_required


class RolePermissionTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/private/")
        self.request.session = {"user_id": "user-id"}

    @patch("apps.users.decorators.get_active_user")
    def test_allowed_role_reaches_view(self, get_active_user):
        get_active_user.return_value = {"role": "student"}
        view = roles_required("student")(lambda request: HttpResponse("ok"))

        response = view(self.request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    @patch("apps.users.decorators.get_active_user")
    def test_disallowed_role_is_forbidden(self, get_active_user):
        get_active_user.return_value = {"role": "instructor"}
        view = roles_required("student")(lambda request: HttpResponse("ok"))

        response = view(self.request)

        self.assertEqual(response.status_code, 403)
