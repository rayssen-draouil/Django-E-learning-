from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import PyMongoError

from . import repositories


def support_notifications(request):
    """Expose the unread support notification count to every rendered page."""
    user_id = request.session.get("user_id")
    if not user_id:
        return {"support_unread_notifications": 0}
    try:
        count = repositories.count_unread_notifications(ObjectId(user_id))
    except (PyMongoError, InvalidId, TypeError):
        count = 0
    return {"support_unread_notifications": count}
