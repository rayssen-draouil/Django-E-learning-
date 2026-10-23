from django.conf import settings
from django.contrib.sessions.backends.base import CreateError, SessionBase, UpdateError
from django.utils import timezone
from pymongo.errors import DuplicateKeyError

from config.mongodb import db


class SessionStore(SessionBase):
    """Django-compatible server-side sessions stored in MongoDB."""

    @property
    def _collection(self):
        return db[settings.MONGODB_SESSIONS_COLLECTION]

    def exists(self, session_key):
        if not self._validate_session_key(session_key):
            return False
        return self._collection.find_one({"_id": session_key}, {"_id": 1}) is not None

    def create(self):
        while True:
            self._session_key = self._get_new_session_key()
            try:
                self.save(must_create=True)
            except CreateError:
                continue
            self.modified = True
            return

    def save(self, must_create=False):
        if self.session_key is None:
            return self.create()
        expiry = self.get_expiry_date()
        document = {
            "_id": self.session_key,
            "session_data": self.encode(self._get_session(no_load=must_create)),
            "expire_date": expiry,
        }
        try:
            if must_create:
                self._collection.insert_one(document)
            else:
                result = self._collection.replace_one(
                    {"_id": self.session_key}, document, upsert=False
                )
                if result.matched_count != 1:
                    raise UpdateError("The session has expired or was deleted")
        except DuplicateKeyError as error:
            raise CreateError from error

    def delete(self, session_key=None):
        key = session_key or self.session_key
        if key:
            self._collection.delete_one({"_id": key})

    def load(self):
        if not self.session_key:
            return {}
        document = self._collection.find_one(
            {"_id": self.session_key, "expire_date": {"$gt": timezone.now()}},
            {"session_data": 1},
        )
        return self.decode(document["session_data"]) if document else {}

    @classmethod
    def clear_expired(cls):
        collection = db[settings.MONGODB_SESSIONS_COLLECTION]
        collection.delete_many({"expire_date": {"$lte": timezone.now()}})

