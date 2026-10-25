"""Tests de l'application support : métier des tickets et contrôle d'accès.

Exécution : python manage.py test --settings=config.settings.testing
"""
from datetime import datetime, timezone

from bson import ObjectId
from django.contrib.auth.hashers import make_password
from django.test import Client, SimpleTestCase
from django.urls import reverse

from config.mongodb import db

from ..services import (
    TicketNotFound,
    TicketPermissionDenied,
    create_ticket,
    get_ticket,
    list_for_user,
    close_student_ticket,
    set_admin_fields,
)
from ..forms import TicketForm
from .. import repositories

DONNEES = {"title": "Verification de test", "category": "account",
           "priority": "normal", "description": "Description de la demande de test."}


def _user(prefixe: str, role: str = "student") -> dict:
    e_mail = f"{prefixe.lower()}_support_unittest@e.com"
    db.users.replace_one(
        {"email": e_mail},
        {"first_name": prefixe, "last_name": "Test", "email": e_mail,
         "password": make_password("MotDePasse1"), "role": role, "is_active": True,
         "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)},
        upsert=True,
    )
    return db.users.find_one({"email": e_mail})


class TestTicketMetier(SimpleTestCase):
    """3.1 — tests unitaires : validation du formulaire et logique de tickets."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db.users.delete_many({"email": {"$regex": "_support_unittest@"}})
        db["support_tickets"].delete_many({"title": DONNEES["title"]})

    def tearDown(self):
        db.users.delete_many({"email": {"$regex": "_support_unittest@"}})
        db["support_tickets"].delete_many({"title": DONNEES["title"]})
        db["support_notifications"].delete_many({})

    def test_formulaire_invalide_sans_description(self):
        """validation des entrées : description obligatoire (3.2 robustesse)."""
        form = TicketForm({"title": "Titre seul", "category": "technical", "priority": "normal"})
        self.assertFalse(form.is_valid())
        self.assertIn("description", form.errors)

    def test_creation_ticket_puis_vue_par_l_apprenant(self):
        etudiant = _user("EtudiantOK")
        _user("AdminNotif", role="admin")  # destinataires des notifications
        ticket = create_ticket(DONNEES, etudiant)
        liste = list_for_user(etudiant)
        identités = [t["_id"] for t in liste]
        self.assertIn(ticket["_id"], identités)
        # les notifications ont été créées pour les administrateurs
        notifications = db.support_notifications.count_documents({"kind": "ticket_created"})
        self.assertGreater(notifications, 0)

    def test_validation_definit_par_letudiant(self):
        """clôture : l'étudiant résout son ticket ; l'admin ne peut pas via cette voie."""
        etudiant = _user("ClotureOK")
        ticket = create_ticket(DONNEES, etudiant)
        close_student_ticket(ticket["_id"], etudiant)
        self.assertEqual(get_ticket(ticket["_id"], etudiant)["status"], "resolved")
        admin = _user("ClotureAdmin", role="admin")
        with self.assertRaises(TicketPermissionDenied):
            close_student_ticket(ticket["_id"], admin)  # déjà résolu

    def test_ticket_inaccessible_aux_autres_etudiants(self):
        """3.2 — permissions : un autre étudiant ne lit pas le ticket (erreur accès)."""
        auteur = _user("Auteur")
        ticket = create_ticket(DONNEES, auteur)
        inconnu = _user("Inconnu")
        with self.assertRaises((TicketNotFound, TicketPermissionDenied)):
            get_ticket(ticket["_id"], inconnu)

    def test_admin_assigne_et_consulter_tout(self):
        auteur = _user("AssigneAuteur")
        ticket = create_ticket(DONNEES, auteur)
        admin = _user("AssigneAdmin", role="admin")
        set_admin_fields(ticket["_id"], {"status": "in_progress", "priority": "urgent", "assignee": "", "note": "Pris en charge."}, admin)
        consulté = get_ticket(ticket["_id"], admin)
        self.assertEqual(consulté["status"], "in_progress")
        self.assertEqual(consulté["priority"], "urgent")


class TestWebSupport(SimpleTestCase):
    """3.1 — intégration web : parcours d'un étudiant dans le back-office support."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db.users.delete_many({"email": {"$regex": "_support_unittest@"}})

    def tearDown(self):
        db.users.delete_many({"email": {"$regex": "_support_unittest@"}})
        db["support_tickets"].delete_many({"title": DONNEES["title"]})
        db["support_notifications"].delete_many({})

    def _session_etudiant(self) -> tuple[Client, dict]:
        etudiant = _user("WebEtudiant")
        client = Client()
        page = client.get(reverse("users:login"))
        connexion = client.post(reverse("users:login"), {
            "email": etudiant["email"], "password": "MotDePasse1",
            "csrfmiddlewaretoken": page.context["csrf_token"],
        })
        self.assertEqual(connexion.status_code, 302,
                         f"connexion: {connexion.status_code} {(connexion.headers.get('Location') or '')}"
                         f" / ctx={page.context is not None} tok={page.context and page.context.get('csrf_token') is not None}"
                         f" / err={('incorrect' in connexion.content.decode('utf-8', 'replace'))}"
                         f" / form={('<ul class=.errorlist.' in connexion.content.decode('utf-8', 'replace'))}")
        return client, etudiant

    def test_anonyme_redirige_he_carte_du_support_admin(self):
        """3.2 — invité sur le dashboard support admin → connexion (jamais 200)."""
        réponse = Client().get(reverse("support:dashboard"))
        self.assertEqual(réponse.status_code, 302)
        self.assertIn("login", réponse["Location"])

    def test_etudiant_cree_et_consulte_son_ticket_depuis_le_web(self):
        client, _etudiant = self._session_etudiant()
        page = client.get(reverse("support:board"))  # /support/board/ étudiant
        self.assertEqual(page.status_code, 200,
                         f"board: {(page.headers.get('Location') or '')}")
        réponse = client.post(reverse("support:create"), {**DONNEES, "csrfmiddlewaretoken": page.context["csrf_token"]})
        self.assertEqual(réponse.status_code, 302)
        # le ticket apparaît dans la vue étudiant
        board = client.get(reverse("support:list"))
        self.assertEqual(board.status_code, 200)
        self.assertContains(board, DONNEES["title"])
