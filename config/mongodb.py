import logging

from django.conf import settings
from pymongo import MongoClient
from pymongo.errors import PyMongoError

client = MongoClient(
    settings.MONGODB_URI,
    serverSelectionTimeoutMS=10000,
)

db = client[settings.MONGODB_DATABASE]
logger = logging.getLogger(__name__)


def test_connection():
    try:
        client.admin.command("ping")
        print("Connexion MongoDB réussie !")
        print(f"Base de données : {db.name}")
        return True
    except PyMongoError:
        logger.warning("MongoDB health check failed.")
        return False
