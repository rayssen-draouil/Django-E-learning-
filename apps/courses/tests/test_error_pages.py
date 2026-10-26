from django.test import Client, SimpleTestCase, override_settings


@override_settings(DEBUG=False, ROOT_URLCONF="apps.courses.tests.error_urls")
class ErrorPageTests(SimpleTestCase):
    def assert_error_page(self, response, *, status, heading, sentence):
        self.assertEqual(response.status_code, status)
        self.assertContains(response, heading, status_code=status)
        self.assertContains(response, sentence, status_code=status)
        self.assertEqual(response.content.count(b"<h1"), 1)
        self.assertContains(response, 'href="/"', status_code=status)

    def test_404_page_is_french_accessible_and_wired_when_debug_is_false(self):
        response = self.client.get("/__test__/missing/")
        self.assert_error_page(
            response,
            status=404,
            heading="Page introuvable",
            sentence="La page demandée n'existe pas ou n'est plus accessible.",
        )
        self.assertNotContains(response, "Page not found", status_code=404)

    def test_403_page_is_french_accessible_and_wired_when_debug_is_false(self):
        response = self.client.get("/__test__/forbidden/")
        self.assert_error_page(
            response,
            status=403,
            heading="Accès refusé",
            sentence="Vous n'avez pas les droits nécessaires pour accéder à cette page.",
        )

    def test_500_page_is_french_accessible_and_hides_the_exception(self):
        with self.assertLogs("django.request", level="ERROR"):
            response = Client(raise_request_exception=False).get("/__test__/boom/")
        self.assert_error_page(
            response,
            status=500,
            heading="Une erreur est survenue",
            sentence="Le service a rencontré un problème inattendu.",
        )
        self.assertNotContains(response, "Erreur de test 500", status_code=500)
        self.assertNotContains(response, "Traceback", status_code=500)
