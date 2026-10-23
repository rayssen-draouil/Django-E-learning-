from django.contrib.sessions.backends.base import CreateError, SessionBase
from django.utils import timezone


class SessionStore(SessionBase):
    """In-memory sessions used only by HTTP view tests."""

    _sessions = {}

    def exists(self, session_key):
        return session_key in self._sessions

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
        if must_create and self.exists(self.session_key):
            raise CreateError
        self._sessions[self.session_key] = (
            self.encode(self._get_session(no_load=must_create)),
            self.get_expiry_date(),
        )

    def delete(self, session_key=None):
        self._sessions.pop(session_key or self.session_key, None)

    def load(self):
        entry = self._sessions.get(self.session_key)
        if not entry or entry[1] <= timezone.now():
            return {}
        return self.decode(entry[0])

    @classmethod
    def clear_expired(cls):
        return None
