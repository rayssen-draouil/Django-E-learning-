
from django.conf import settings
from pymongo import MongoClient

client = MongoClient(
    settings.MONGODB_URI,
    serverSelectionTimeoutMS=5000,
)

db = client[settings.MONGODB_DATABASE]


def test_connection():
    try:
        client.admin.command("ping")
        print("Connexion MongoDB réussie !")
        print(f"Base de données : {db.name}")
        return True
    except Exception as error:
        print(f"Erreur MongoDB : {error}")
        return False
