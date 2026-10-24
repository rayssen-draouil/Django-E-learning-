import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId
from django.conf import settings
from pymongo.errors import PyMongoError

from . import repositories


class TicketNotFound(Exception): pass
class TicketPermissionDenied(Exception): pass
class SupportStorageError(Exception): pass


CATEGORIES = (
    ("courses", "Courses"), ("quizzes", "Quizzes"), ("mentoring", "Mentoring"),
    ("account", "Account"), ("other", "Other"),
)
PRIORITIES = (("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent"))
STATUSES = (("open", "Open"), ("in_progress", "In progress"), ("waiting", "Waiting"), ("resolved", "Resolved"))

CATEGORY_LABELS = dict(CATEGORIES)
PRIORITY_LABELS = dict(PRIORITIES)
STATUS_LABELS = dict(STATUSES)


NOTIFICATION_KINDS = {
    "ticket_created": "Ticket created",
    "reply": "New reply",
    "status_change": "Status changed",
    "assignment": "Assignment",
}

THEME_LABELS = {
    "video": "Video / course playback",
    "quiz": "Quizzes and grading",
    "mentorat": "Mentoring",
    "compte": "Account and sign-in",
    "acces_cours": "Course access",
    "progression": "Progress and certificates",
    "paiement": "Billing and subscription",
    "application": "App behavior",
}

THEME_KEYWORDS = (
    ("video", ("video", "playback", "load", "loading", "streaming", "buffer", "freeze", "audio", "sound")),
    ("quiz", ("quiz", "question", "score", "exam", "correction", "grading", "answers")),
    ("mentorat", ("mentor", "mentoring", "appointment", "meeting", "video call", "session", "support")),
    ("compte", ("account", "login", "sign in", "password", "email", "profile", "registration", "username")),
    ("acces_cours", ("course", "lesson", "chapter", "module", "access", "section", "material")),
    ("progression", ("progress", "advancement", "certificate", "badge", "level")),
    ("paiement", ("payment", "invoice", "card", "subscription", "price", "fee")),
    ("application", ("application", "site", "page", "button", "form", "error", "crash", "bug", "website")),
)

STOPWORDS = {
    "i", "you", "he", "she", "we", "they", "the", "a", "an", "of", "to", "in", "for", "with", "on", "at", "by",
    "from", "up", "about", "into", "over", "after", "and", "or", "but", "as", "if", "when", "than", "because",
    "while", "where", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very", "can", "will", "just", "should", "now",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "but", "at",
    "by", "for", "with", "about", "against", "between", "into", "through", "during", "before", "after", "above",
    "below", "to", "from", "up", "down", "in", "out", "on", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "any", "both", "each", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "issue",
    "unable", "impossible", "cannot", "not", "work", "works", "really", "already", "still", "always", "often",
    "when", "since", "even", "does", "did", "done", "need", "would", "can", "could", "help", "problem", "bug",
}


def now():
    return datetime.now(timezone.utc)


def parse_id(value):
    if isinstance(value, ObjectId): return value
    if isinstance(value, str) and ObjectId.is_valid(value): return ObjectId(value)
    raise TicketNotFound


def category_suggestion(text):
    text = text.casefold()
    terms = {
        "courses": ("course", "lesson", "video", "chapter", "class", "module"),
        "quizzes": ("quiz", "question", "score", "answer", "exam", "test"),
        "mentoring": ("mentor", "mentoring", "appointment", "session", "call", "support"),
        "account": ("account", "login", "password", "email", "profile", "registration"),
    }
    matches = [(sum(term in text for term in words), category) for category, words in terms.items()]
    score, category = max(matches)
    return category if score else "other"


def normalize_text(text):
    """Lowercase, strip accents and punctuation so similar tickets can be grouped."""
    cleaned = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").casefold()
    cleaned = re.sub(r"[^a-z0-9\s-]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def detect_theme(text):
    """Return the dominant problem theme of a ticket, or None when no theme matches."""
    normalized = normalize_text(text)
    if not normalized:
        return None
    scores = [(sum(1 for keyword in keywords if keyword in normalized), theme) for theme, keywords in THEME_KEYWORDS]
    score, theme = max(scores)
    return theme if score else None


def problem_signature(title, description=""):
    """Build a stable signature used to detect recurring problems."""
    normalized = normalize_text(f"{title} {description}")
    if not normalized:
        return None
    theme = detect_theme(normalized)
    if theme:
        return f"theme:{theme}"
    tokens = [token for token in normalized.split() if token not in STOPWORDS and len(token) > 2]
    if not tokens:
        return None
    return " ".join(sorted(set(tokens))[:5])


def signature_label(signature, fallback=""):
    if signature and signature.startswith("theme:"):
        theme = signature.split(":", 1)[1]
        return THEME_LABELS.get(theme, theme)
    return fallback or "Other"


DEFAULT_FAQ = (
    {"question": "I can’t sign in to my account",
     "answer": "Reset your password from the sign-in page, then make sure you are using the correct email address.",
     "keywords": ["password", "sign in", "login", "account", "email"],
     "category": "account"},
    {"question": "A course video won’t load",
     "answer": "Refresh the course page and check your internet connection. If the issue continues, include the course title and your browser.",
     "keywords": ["video", "playback", "course", "streaming", "load"],
     "category": "courses"},
    {"question": "My quiz score is missing",
     "answer": "Make sure all your answers were submitted. Include the quiz title and a screenshot of your result.",
     "keywords": ["quiz", "score", "answer", "result", "grade"],
     "category": "quizzes"},
    {"question": "I have an issue with a mentoring session",
     "answer": "Check your mentor’s availability and include the date and time of the session.",
     "keywords": ["mentor", "mentoring", "appointment", "session", "meeting"],
     "category": "mentoring"},
)

TICKET_SORTS = ("recent", "oldest", "priority")


def faq_entries(published_only=True):
    """Return the FAQ managed by the support team, with the built-in entries as a fallback."""
    try:
        entries = repositories.list_faq({"is_published": True} if published_only else {})
    except PyMongoError:
        entries = []
    if entries:
        return entries
    return [dict(entry, _id=None, is_published=True) for entry in DEFAULT_FAQ]


def search_faq(query="", category=None, limit=8):
    """Rank the FAQ entries against a free-text query and an optional category."""
    entries = faq_entries()
    normalized_query = normalize_text(query)
    terms = [term for term in normalized_query.split() if len(term) > 2]
    scored = []
    for entry in entries:
        if category and entry.get("category") != category:
            continue
        keywords = [normalize_text(keyword) for keyword in entry.get("keywords", [])]
        haystack = normalize_text(f'{entry.get("question", "")} {entry.get("answer", "")} {" ".join(keywords)}')
        score = sum(1 for term in terms if term in haystack)
        score += 2 * sum(1 for keyword in keywords if keyword and keyword in normalized_query)
        if terms and score == 0:
            continue
        scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], item[1].get("question", "")))
    return [entry for _, entry in scored[:limit]]


def create_faq(data, user):
    document = {"_id": ObjectId(), "question": data["question"], "answer": data["answer"],
                "keywords": data.get("keywords", []), "category": data.get("category", "other"),
                "is_published": bool(data.get("is_published", True)), "created_by": user["_id"],
                "created_at": now(), "updated_at": now()}
    repositories.insert_faq(document)
    return document


def update_faq(entry_id, data, user):
    fields = {"question": data["question"], "answer": data["answer"], "keywords": data.get("keywords", []),
              "category": data.get("category", "other"), "is_published": bool(data.get("is_published", True)),
              "updated_at": now()}
    return repositories.update_faq(entry_id, fields)


def delete_faq(entry_id):
    return repositories.delete_faq(entry_id)


def seed_default_faq():
    """Insert the built-in FAQ entries once, when the collection is still empty."""
    if repositories.count_faq():
        return 0
    for entry in DEFAULT_FAQ:
        repositories.insert_faq({"_id": ObjectId(), "question": entry["question"], "answer": entry["answer"],
                                 "keywords": entry["keywords"], "category": entry["category"],
                                 "is_published": True, "created_at": now(), "updated_at": now()})
    return len(DEFAULT_FAQ)


def smart_help(ticket):
    content = f'{ticket.get("title", "")} {ticket.get("description", "")}'.casefold()
    matches = search_faq(content)
    suggestions = [entry.get("answer", "") for entry in matches if entry.get("answer")]
    return {
        "category": category_suggestion(content),
        "faq": suggestions[0] if suggestions else "Include the page name, the steps to reproduce the issue, and your browser. The support team will reply here.",
        "summary": " ".join(ticket.get("description", "").split())[:280],
        "reply": (suggestions[0] if suggestions else "Hello, thanks for reporting this. We’re looking into the issue and will get back to you as soon as possible."),
        "matches": matches,
    }


def _store_upload(upload, ticket_id):
    folder = Path(settings.MEDIA_ROOT) / "support" / str(ticket_id)
    folder.mkdir(parents=True, exist_ok=True)
    extension = Path(upload.name).suffix.lower()
    name = f"{uuid4().hex}{extension}"
    path = folder / name
    with path.open("wb") as target:
        for chunk in upload.chunks(): target.write(chunk)
    return f"support/{ticket_id}/{name}", Path(upload.name).name


def list_for_user(user):
    query = {} if user.get("role") == "admin" else {"student_id": user["_id"]}
    return repositories.list_tickets(query)


def build_ticket_query(user, filters=None):
    """Build the MongoDB query of a ticket queue from the back-office filters."""
    filters = filters or {}
    query: dict[str, Any] = {}
    if user.get("role") != "admin":
        query["student_id"] = user["_id"]
        return query
    for field in ("status", "priority", "category"):
        value = filters.get(field)
        if value:
            query[field] = value
    assignee = filters.get("assignee")
    if assignee == "unassigned":
        query["assignee_id"] = None
    elif assignee == "me":
        query["assignee_id"] = user["_id"]
    elif assignee:
        try:
            query["assignee_id"] = ObjectId(assignee)
        except (InvalidId, TypeError):
            pass
    search = (filters.get("q") or "").strip()
    if search:
        pattern = {"$regex": repositories.escape_regex(search), "$options": "i"}
        query["$or"] = [{"title": pattern}, {"description": pattern}]
    return query


def filtered_tickets(user, filters=None):
    """Return every ticket of a queue, without pagination."""
    query = build_ticket_query(user, filters)
    try:
        return repositories.list_tickets(query)
    except PyMongoError as error: raise SupportStorageError from error


def paginate_tickets(user, filters=None, page=1, per_page=20):
    """Return one page of the ticket queue with its pagination metadata."""
    filters = filters or {}
    query = build_ticket_query(user, filters)
    try:
        total = repositories.count_tickets(query)
        per_page = max(1, min(int(per_page or 20), 100))
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(int(page or 1), pages))
        items = repositories.list_tickets(query, skip=(page - 1) * per_page, limit=per_page,
                                          sort=filters.get("sort") or "recent")
    except PyMongoError as error:
        raise SupportStorageError from error
    return {"tickets": items, "total": total, "page": page, "pages": pages, "per_page": per_page}


def add_note(ticket_id, body, user):
    """Add an internal note, visible to the support team only."""
    ticket = get_ticket(ticket_id, user)
    if user.get("role") != "admin":
        raise TicketPermissionDenied
    document = {"_id": ObjectId(), "ticket_id": ticket["_id"], "author_id": user["_id"],
                "author_name": f'{user.get("first_name", "")} {user.get("last_name", "")}'.strip() or user.get("email", ""),
                "body": body, "created_at": now()}
    try:
        repositories.insert_note(document)
    except PyMongoError as error: raise SupportStorageError from error
    return document


def notes_for(ticket):
    try:
        return repositories.ticket_notes(ticket["_id"])
    except PyMongoError as error: raise SupportStorageError from error


def delete_note(note_id, ticket_id, user):
    """Only the author of an internal note can remove it."""
    if user.get("role") != "admin":
        raise TicketPermissionDenied
    try:
        note = repositories.find_note(parse_id(note_id), parse_id(ticket_id))
    except PyMongoError as error: raise SupportStorageError from error
    if not note:
        raise TicketNotFound
    if note.get("author_id") != user.get("_id"):
        raise TicketPermissionDenied
    repositories.delete_note(note["_id"])


def get_ticket(ticket_id, user):
    try: ticket = repositories.find_ticket(parse_id(ticket_id))
    except PyMongoError as error: raise SupportStorageError from error
    if not ticket: raise TicketNotFound
    if user.get("role") != "admin" and ticket.get("student_id") != user.get("_id"):
        raise TicketPermissionDenied
    return ticket


def notify(recipient_id, kind, ticket, title, body):
    """Record a notification for one recipient; failures never break the caller."""
    if not recipient_id:
        return None
    document = {"_id": ObjectId(), "recipient_id": recipient_id, "kind": kind, "ticket_id": ticket["_id"],
                "title": title, "body": body, "created_at": now(), "read_at": None}
    try:
        repositories.insert_notification(document)
    except PyMongoError:
        return None
    return document


def _support_recipients(ticket):
    """Admins that should be warned about a new student activity on a ticket."""
    if ticket.get("assignee_id"):
        return [ticket["assignee_id"]]
    try:
        return [admin["_id"] for admin in repositories.admins()]
    except PyMongoError:
        return []


def create_ticket(data, user):
    timestamp, ticket_id = now(), ObjectId()
    student_name = f'{user.get("first_name", "")} {user.get("last_name", "")}'.strip()
    doc = {"_id": ticket_id, "student_id": user["_id"], "student_name": student_name,
           "student_email": user["email"], "title": data["title"], "description": data["description"],
           "category": data["category"], "priority": data["priority"], "status": "open", "assignee_id": None,
           "assignee_name": "", "created_at": timestamp, "updated_at": timestamp,
           "history": [{"status": "open", "previous_status": None, "by": user.get("email"), "at": timestamp, "note": "Ticket created"}], "resolved_at": None}
    try:
        repositories.insert_ticket(doc)
    except PyMongoError as error: raise SupportStorageError from error
    try:
        for admin in repositories.admins():
            notify(admin["_id"], "ticket_created", doc, f"New ticket: {doc['title']}",
                   f"{student_name or doc['student_email']} reported an issue. Priority: {PRIORITY_LABELS.get(data['priority'], data['priority']).lower()}.")
    except PyMongoError:
        pass
    return doc


def update_ticket_content(ticket_id, data, user):
    """Update title, description or category (student owner, or any support admin)."""
    ticket = get_ticket(ticket_id, user)
    if user.get("role") != "admin" and ticket["status"] == "resolved":
        raise TicketPermissionDenied
    try:
        repositories.update_ticket(ticket["_id"], {"title": data["title"], "description": data["description"], "category": data["category"], "updated_at": now()})
    except PyMongoError as error: raise SupportStorageError from error


def close_student_ticket(ticket_id, user):
    ticket = get_ticket(ticket_id, user)
    if user.get("role") == "admin" or ticket.get("status") == "resolved":
        raise TicketPermissionDenied
    timestamp = now()
    event = {"status": "resolved", "previous_status": ticket.get("status"), "by": user["email"], "at": timestamp, "note": "Ticket closed by the student"}
    try:
        repositories.update_ticket(ticket["_id"], {"status": "resolved", "resolved_at": timestamp,
            "updated_at": timestamp, "history": ticket.get("history", []) + [event]})
    except PyMongoError as error: raise SupportStorageError from error
    try:
        for admin in repositories.admins():
            notify(admin["_id"], "status_change", ticket, f"Ticket resolved: {ticket['title']}",
                   f"{user['email']} marked their request as resolved.")
    except PyMongoError:
        pass


def delete_student_ticket(ticket_id, user):
    ticket = get_ticket(ticket_id, user)
    if user.get("role") != "admin" and (ticket["status"] != "open" or repositories.messages.count_documents({"ticket_id": ticket["_id"]}) > 0):
        raise TicketPermissionDenied
    try: repositories.remove_ticket(ticket["_id"])
    except PyMongoError as error: raise SupportStorageError from error


def set_admin_fields(ticket_id, data, user):
    ticket = get_ticket(ticket_id, user)
    try:
        admins = repositories.admins()
    except PyMongoError as error:
        raise SupportStorageError from error
    assignee_id = None
    if data.get("assignee"):
        try:
            assignee_id = ObjectId(data["assignee"])
        except (InvalidId, TypeError):
            raise TicketPermissionDenied
    assignee = next((admin for admin in admins if admin["_id"] == assignee_id), None)
    timestamp = now()
    fields = {"status": data["status"], "priority": data["priority"], "assignee_id": assignee_id,
              "assignee_name": f'{assignee.get("first_name", "")} {assignee.get("last_name", "")}'.strip() if assignee else "", "updated_at": timestamp}
    try:
        if ticket["status"] != data["status"]:
            note = data.get("note") or "Status updated"
            fields["history"] = ticket.get("history", []) + [{"status": data["status"], "previous_status": ticket["status"],
                                                              "by": user["email"], "at": timestamp, "note": note}]
        fields["resolved_at"] = timestamp if data["status"] == "resolved" else None
        repositories.update_ticket(ticket["_id"], fields)
    except PyMongoError as error: raise SupportStorageError from error
    if ticket["status"] != data["status"]:
        notify(ticket["student_id"], "status_change", ticket,
               f"Status updated: {STATUS_LABELS.get(data['status'], data['status'])}",
               f"Your request ‘{ticket['title']}’ is now {STATUS_LABELS.get(data['status'], data['status']).lower()}.")
    if assignee_id and assignee_id != ticket.get("assignee_id"):
        notify(assignee_id, "assignment", ticket, f"Ticket assigned: {ticket['title']}",
               f"{user['email']} assigned you this ticket (priority {PRIORITY_LABELS.get(data['priority'], data['priority']).lower()}).")


def reply(ticket_id, body, upload, user):
    ticket = get_ticket(ticket_id, user)
    timestamp = now()
    attachment = None
    try:
        if upload:
            stored_path, original_name = _store_upload(upload, ticket["_id"])
            attachment = {"path": stored_path, "name": original_name}
        is_admin = user.get("role") == "admin"
        recipient = ticket["student_id"] if is_admin else (ticket.get("assignee_id") or next((a["_id"] for a in repositories.admins()), None))
        repositories.insert_message({"_id": ObjectId(), "ticket_id": ticket["_id"], "sender_id": user["_id"],
            "sender_name": f'{user.get("first_name", "")} {user.get("last_name", "")}'.strip() or user["email"],
            "sender_role": user.get("role"), "recipient_id": recipient, "body": body,
            "attachment": attachment, "created_at": timestamp, "read_at": None})
        fields = {"updated_at": timestamp}
        if is_admin and ticket["status"] == "open": fields["status"] = "in_progress"
        if not is_admin and ticket["status"] in {"resolved", "waiting"}:
            fields["status"] = "open"
            fields["resolved_at"] = None
        if fields.get("status") and fields["status"] != ticket["status"]:
            fields["history"] = ticket.get("history", []) + [{"status": fields["status"], "previous_status": ticket["status"], "by": user["email"], "at": timestamp, "note": "Updated after reply"}]
        repositories.update_ticket(ticket["_id"], fields)
    except PyMongoError as error: raise SupportStorageError from error
    if is_admin:
        notify(ticket["student_id"], "reply", ticket, f"Support reply: {ticket['title']}",
               f"{user.get('first_name', '') or user['email']} replied to your request.")
    else:
        for recipient_id in _support_recipients(ticket):
            notify(recipient_id, "reply", ticket, f"New reply: {ticket['title']}",
                   f"{user.get('first_name', '') or user['email']} replied to the ticket.")


def messages_for(ticket, user):
    try:
        result = repositories.ticket_messages(ticket["_id"])
        repositories.mark_read(ticket["_id"], user["_id"], now())
        return result
    except PyMongoError as error: raise SupportStorageError from error


def notifications_for(user, limit: int = 50):
    try:
        items = repositories.notifications_for(user["_id"], limit=limit)
    except PyMongoError as error: raise SupportStorageError from error
    for item in items: item["id"] = str(item["_id"])
    return items


def unread_notification_count(user):
    try:
        return repositories.count_unread_notifications(user["_id"])
    except PyMongoError:
        return 0


def mark_notifications_read(user, ticket_id=None):
    try:
        return repositories.mark_notifications_read(user["_id"], now(), ticket_id=ticket_id)
    except PyMongoError as error: raise SupportStorageError from error


def _naive_utc(value):
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _hours_between(start, end):
    return (_naive_utc(end) - _naive_utc(start)).total_seconds() / 3600


def recurring_problems(tickets, minimum: int = 2):
    """Group tickets by problem signature to expose recurring issues."""
    groups: dict[str, dict] = {}
    for ticket in tickets:
        signature = problem_signature(ticket.get("title", ""), ticket.get("description", ""))
        if not signature:
            continue
        group = groups.setdefault(signature, {"signature": signature, "label": signature_label(signature, ticket.get("title", "")),
                                              "count": 0, "category": ticket.get("category", "other"), "sample_title": ticket.get("title", ""),
                                              "student_ids": set(), "ticket_ids": []})
        group["count"] += 1
        group["student_ids"].add(str(ticket.get("student_id")))
        group["ticket_ids"].append(str(ticket.get("_id")))
    result = []
    for group in groups.values():
        if group["count"] < minimum:
            continue
        result.append({"signature": group["signature"], "label": group["label"], "count": group["count"],
                       "category": group["category"], "sample_title": group["sample_title"],
                       "student_count": len(group["student_ids"]), "ticket_ids": group["ticket_ids"]})
    result.sort(key=lambda item: (-item["count"], item["label"]))
    return result


def dashboard_stats(tickets):
    resolved = [t for t in tickets if t.get("resolved_at") and t.get("created_at")]
    times = sorted(_hours_between(t["created_at"], t["resolved_at"]) for t in resolved)
    statuses = Counter(t.get("status", "open") for t in tickets)
    recurring = recurring_problems(tickets)
    recurring_tickets = sum(item["count"] for item in recurring)
    return {
        "total_count": len(tickets),
        "open_count": sum(1 for t in tickets if t.get("status") != "resolved"),
        "resolved_count": len(resolved),
        "in_progress_count": statuses.get("in_progress", 0),
        "waiting_count": statuses.get("waiting", 0),
        "unassigned_count": sum(1 for t in tickets if not t.get("assignee_id") and t.get("status") != "resolved"),
        "avg_resolution_hours": round(sum(times) / len(times), 1) if times else None,
        "median_resolution_hours": round(times[len(times) // 2], 1) if times else None,
        "categories": list(Counter(t.get("category", "other") for t in tickets).items()),
        "priorities": list(Counter(t.get("priority", "normal") for t in tickets).items()),
        "statuses": list(statuses.items()),
        "recurring": recurring,
        "recurring_count": len(recurring),
        "recurring_ticket_count": recurring_tickets,
        "recurring_rate": round(recurring_tickets * 100 / len(tickets), 1) if tickets else 0.0,
    }
