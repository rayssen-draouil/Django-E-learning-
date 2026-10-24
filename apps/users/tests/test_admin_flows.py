"""Tests de l'application users : logique métier, flux web, sécurité des accès.

Exécution : python manage.py test --settings=config.settings.testing
La suite travaille dans la base MongoDB ``e_learning_test``.
"""
from datetime import datetime, timezone

from bson import ObjectId
from django.contrib.auth.hashers import identify_hasher, make_password
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from config.mongodb import db

from ..services import (
    AdminActionError,
    DuplicateEmailError,
    UserNotFoundError,
    authenticate_user,
    admin_change_role,
    admin_create_account,
    admin_list_users,
    admin_set_active,
    consume_password_reset,
    create_password_reset,
    create_user,
    create_admin_user,
    ensure_users_indexes,
    is_password_reset_valid,
    update_user_profile,
)


def _creer(email: str, *, role: str = "student", password: str = "MotDePasse1", actif: bool = True) -> dict:
    """Insère un compte de test dans la base de test (préfixe _unittest pour purge)."""
    db.users.replace_one(
        {"email": email},
        {"first_name": email.split("@")[0], "last_name": "Test", "email": email,
         "password": make_password(password), "role": role, "is_active": actif,
         "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)},
        upsert=True,
    )
    return db.users.find_one({"email": email})


class TestCreationCompte(SimpleTestCase):
    """3.1 — tests unitaires : validations et contraintes du service de comptes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db.users.delete_many({"email": {"$regex": "_unittest@"}})
        ensure_users_indexes()

    def tearDown(self):
        db.users.delete_many({"email": {"$regex": "_unittest@"}})
        db.password_resets.delete_many({})

    def test_creation_etudiant_role_par_defaut(self):
        "#{create_user} crée un étudiant actif avec des timestamps"
        user = create_user(first_name="Salem", last_name="Diber", email="salem_unittest@e.com", password="MotDePasse1")
        self.assertEqual(user["role"], "student")
        self.assertTrue(user["is_active"])

    def test_mot_de_passe_hashement_mais_jamais_en_clair(self):
        """3.2 — sécurité : le mot de passe est stocké hashé (OWASP A02)."""
        user = create_user(first_name="A", last_name="B", email="hash_unittest@e.com", password="MotDePasse1")
        stocke = db.users.find_one({"_id": user["_id"]})["password"]
        self.assertNotEqual(stocke, "MotDePasse1")
        # format Django encodé (hasheur identifié par son magasin) : jamais de clair
        identifie = identify_hasher(stocke)
        self.assertIn(identifie.algorithm, ("pbkdf2_sha256", "md5", "argon2", "scrypt"), stocke[:20])

    def test_email_dupliqué_rejeté(self):
        """contrainte : l'unicité de l'e-mail sur l'index Mongo renvoie erreur métier."""
        create_user(first_name="X", last_name="Y", email="dup_unittest@e.com", password="MotDePasse1")
        with self.assertRaises(DuplicateEmailError):
            create_user(first_name="X2", last_name="Y2", email="dup_unittest@e.com", password="MotDePasse1")

    def test_role_admin_interdit_et_rôle_inconnu_interdit(self):
        """l'inscription publique ne peut pas créer d'administrateur (contrôle métier)."""
        with self.assertRaises(ValueError):
            create_user(first_name="H", last_name="X", email="admin_unittest@e.com", password="MotDePasse1", role="admin")
        with self.assertRaises(ValueError):
            create_user(first_name="H", last_name="X", email="inconnu_unittest@e.com", password="MotDePasse1", role="boss")

    @override_settings(USERS_ADMIN_BOOTSTRAP_TOKEN="nimporte-mais-existe")
    def test_bootstrap_admin_avec_jeton_valide(self):
        user = create_admin_user(
            first_name="Boo", last_name="Strap", email="bootstrap_unittest@e.com",
            password="MotDePasse1", bootstrap_token="nimporte-mais-existe",
        )
        self.assertEqual(user["role"], "admin")


AUTH = dict(password="MotDePasse1")


class TestAuthentification(SimpleTestCase):
    """3.1 — logique métier : vérification de mot de passe et comptes inactifs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db.users.delete_many({"email": {"$regex": "_unittest@"}})

    def tearDown(self):
        db.users.delete_many({"email": {"$regex": "_unittest@"}})

    def test_authentification_bon_mauvais_mot_de_passe(self):
        _creer("auth_unittest@e.com", **AUTH)
        self.assertIsNotNone(authenticate_user("auth_unittest@e.com", "MotDePasse1"))
        self.assertIsNone(authenticate_user("auth_unittest@e.com", "Fauxmotdep1"))
        self.assertIsNone(authenticate_user("inconnu_unittest@e.com", "MotDePasse1"))

    def test_compte_desactive_rouge_comme_inexistant(self):
        """un compte désactivé ne peut plus s'authentifier (gestion des rôles)."""
        _creer("off_unittest@e.com", **AUTH, actif=False)
        self.assertIsNone(authenticate_user("off_unittest@e.com", "MotDePasse1"))


class TestReinitMotDePasse(SimpleTestCase):
    """3.1 — le flux complet « mot de passe oublié » (tokens à usage unique)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db.users.delete_many({"email": {"$regex": "_unittest@"}})

    def tearDown(self):
        db.users.delete_many({"email": {"$regex": "_unittest@"}})
        db.password_resets.delete_many({})

    def test_compte_inexistant_renvoie_aucun_token(self):
        self.assertIsNone(create_password_reset("ghost_unittest@e.com"))

    def test_consommation_change_le_mot_de_passe_et_invalide(self):
        _creer("reset_unittest@e.com", **AUTH)
        token = create_password_reset("reset_unittest@e.com")
        self.assertTrue(token)
        self.assertTrue(is_password_reset_valid(token))
        self.assertTrue(consume_password_reset(token, "NouveauMotDePasse2"))
        self.assertIsNotNone(authenticate_user("reset_unittest@e.com", "NouveauMotDePasse2"))
        # jeton consommé : usage unique imposé
        self.assertFalse(is_password_reset_valid(token))
        self.assertFalse(consume_password_reset(token, "EncoreUnAutre3x"))

    def test_nouvelle_demande_invalide_les_jetons_precedents(self):
        """chaque nouvelle demande doit rendre les anciens liens inutilisables."""
        _creer("multi_unittest@e.com", **AUTH)
        ancien = create_password_reset("multi_unittest@e.com")
        nouveau = create_password_reset("multi_unittest@e.com")
        self.assertFalse(is_password_reset_valid(ancien))
        self.assertTrue(is_password_reset_valid(nouveau))


class TestActionsAdmin(SimpleTestCase):
    """3.1 + 3.2 — console admin : les filtres et les protections anti-blocage."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db.users.delete_many({"email": {"$regex": "_unittest@"}})
        ensure_users_indexes()

    def tearDown(self):
        db.users.delete_many({"email": {"$regex": "_unittest@"}})
        db.password_resets.delete_many({})

    def test_liste_filtre_par_recherche_et_role(self):
        etudiant = _creer("alice_unittest@e.com", role="student")
        _creer("bob_instructeur_unittest@e.com", role="instructor")
        liste = admin_list_users(search="alice", role="student")
        identités = [str(c["_id"]) for c in liste]
        self.assertIn(str(etudiant["_id"]), identités)
        self.assertFalse(any("bob_instructeur" in c["email"] for c in liste))
        # jamais de mot de passe exposé par la liste admin
        self.assertTrue(all("password" not in c for c in liste))

    def test_admin_ne_peut_pas_se_desactiver_lui_meme(self):
        moi = _creer("moi_unittest@e.com", role="admin")
        with self.assertRaises(AdminActionError):
            admin_set_active(str(moi["_id"]), False, moi)

    def test_admin_ne_peut_pas_perdre_son_propre_role(self):
        moi = _creer("moi2_unittest@e.com", role="admin")
        with self.assertRaises(AdminActionError):
            admin_change_role(str(moi["_id"]), "student", moi)

    def test_change_role_inconnu_rejete(self):
        cible = _creer("cible_unittest@e.com", role="student")
        moi = _creer("boss_unittest@e.com", role="admin")
        with self.assertRaises(AdminActionError):
            admin_change_role(str(cible["_id"]), "superman", moi)

    def test_desactivation_dun_autre_compte_ok(self):
        admin = _creer("admin_actif_unittest@e.com", role="admin")
        cible = _creer("etudiant_actif_unittest@e.com", role="student")
        admin_set_active(str(cible["_id"]), False, admin)  # aucune exception attendue
        self.assertFalse(db.users.find_one({"_id": cible["_id"]})["is_active"])

    def test_creation_compte_admin_email_unique(self):
        admin_create_account(first_name="N", last_name="M", email="new_instructor_unittest@e.com", password="MotDePasse1")
        with self.assertRaises(DuplicateEmailError):
            admin_create_account(first_name="N", last_name="M", email="new_instructor_unittest@e.com", password="MotDePasse1")


class TestPagesWeb(SimpleTestCase):
    """3.1 — tests d'intégration : statuts HTTP et contenus des vues principales."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db.users.delete_many({"email": {"$regex": "_unittest@"}})

    def tearDown(self):
        db.users.delete_many({"email": {"$regex": "_unittest@"}})
        db.password_resets.delete_many({})

    def test_register_page_sert_le_formulaire(self):
        réponse = Client().get(reverse("users:register"))
        self.assertEqual(réponse.status_code, 200)
        self.assertContains(réponse, "Type de compte")

    def test_register_sans_csrf_rejete_403(self):
        """3.2 — sécurité CSRF active : une requête sans jeton est refusée."""
        réponse = Client(enforce_csrf_checks=True).post(reverse("users:register"), {
            "first_name": "No", "last_name": "Csrf", "email": "csrf_unittest@e.com",
            "password": "MotDePasse1", "password_confirm": "MotDePasse1",
        })
        self.assertEqual(réponse.status_code, 403)

    def test_register_valide_redirige_vers_profil(self):
        client = Client()
        page = client.get(reverse("users:register"))
        réponse = client.post(reverse("users:register"), {
            "first_name": "Lina", "last_name": "NMulti", "email": "web_reg_unittest@e.com",
            "role": "instructor", "password": "MotDePasse1", "password_confirm": "MotDePasse1",
        }, follow=False)
        self.assertEqual(réponse.status_code, 302)
        self.assertIn("/users/profile/", réponse["Location"])
        # double soumission : e-mail dupliqué → 200 avec message d'erreur
        second = client.get(reverse("users:register"))  # déjà connecté : redirige profil
        self.assertEqual(second.status_code, 302)

    def test_login_echec_puis_succes(self):
        _creer("loginweb_unittest@e.com", **AUTH)
        client = Client()
        page = client.get(reverse("users:login"))
        données = {"email": "loginweb_unittest@e.com", "password": "FauxTotale9"}
        données["csrfmiddlewaretoken"] = page.context["csrf_token"]
        réponse = client.post(reverse("users:login"), données)
        self.assertEqual(réponse.status_code, 200)
        self.assertContains(réponse, "incorrect")
        données["password"] = "MotDePasse1"
        page2 = client.get(reverse("users:login"))
        données["csrfmiddlewaretoken"] = page2.context["csrf_token"]
        réponse2 = client.post(reverse("users:login"), données)
        self.assertEqual(réponse2.status_code, 302)
        self.assertIn("backoffice", réponse2["Location"])

    def test_anonyme_bloquée_sur_le_backoffice(self):
        """3.2 — contrôle d'accès : invité → redirection vers la connexion."""
        réponse = Client().get(reverse("users:backoffice"))
        self.assertEqual(réponse.status_code, 302)
        self.assertIn("login", réponse["Location"])

    def test_etudiant_bloquee_sur_console_admin(self):
        """3.2 — un étudiant ne pas parvenir sur la console admin via URL directe."""
        _creer("folk_unittest@e.com", **AUTH, role="student")
        client = Client()
        page = client.get(reverse("users:login"))
        client.post(reverse("users:login"), {
            "email": "folk_unittest@e.com", "password": "MotDePasse1",
            "csrfmiddlewaretoken": page.context["csrf_token"],
        })
        réponse = client.get(reverse("users:admin_users"))
        self.assertEqual(réponse.status_code, 403)

    def test_admin_voit_la_console_comptes(self):
        """3.2 — l'admin, lui, accède effectivement à la console : 200."""
        _creer("chief_unittest@e.com", **AUTH, role="admin")
        client = Client()
        page = client.get(reverse("users:login"))
        client.post(reverse("users:login"), {
            "email": "chief_unittest@e.com", "password": "MotDePasse1",
            "csrfmiddlewaretoken": page.context["csrf_token"],
        })
        contenu = client.get(reverse("users:admin_users"))
        self.assertEqual(contenu.status_code, 200)
        self.assertContains(contenu, "Utilisateurs")

    @override_settings(DEBUG=True)
    def test_flux_oublie_complet_depuis_le_web(self):
        """3.1 — parcours bout en bout : demande → lien → nouveau mot de passe → connexion."""
        _creer("flow_unittest@e.com", **AUTH)
        client = Client()
        page = client.get(reverse("users:forgot_password"))
        réponse = client.post(reverse("users:forgot_password"), {
            "email": "flow_unittest@e.com", "csrfmiddlewaretoken": page.context["csrf_token"],
        })
        self.assertEqual(réponse.status_code, 200)
        self.assertContains(réponse, "reset-password/")  # lien direct en mode dév
        lien = réponse.context["dev_reset_link"]
        étape1 = client.get(lien)
        self.assertEqual(étape1.status_code, 200)
        étape1_post = client.post(lien, {
            "password": "RenforceAuth1", "password_confirm": "RenforceAuth1",
            "csrfmiddlewaretoken": étape1.context["csrf_token"],
        })
        self.assertEqual(étape1_post.status_code, 302)
        self.assertIn("login", étape1_post["Location"])
        self.assertIsNotNone(authenticate_user("flow_unittest@e.com", "RenforceAuth1"))
        # jeton à usage unique : le lien ne sert plus → renvoyé vers forget
        étape2 = client.get(lien)
        self.assertEqual(étape2.status_code, 302)
        self.assertIn("forgot", étape2["Location"])

    def test_page_404_personnalisee(self):
        """3.2 — robustesse : page d'erreur 404 personnalisée hors DEBUG."""
        réponse = Client().get("/cette/route/nexiste-pas/")
        self.assertEqual(réponse.status_code, 404)
        self.assertContains(réponse, "404", status_code=404)
