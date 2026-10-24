import re
from typing import Any

from bson import ObjectId

from config.mongodb import db
from apps.users.repositories import users_collection

tickets = db["support_tickets"]
messages = db["support_messages"]
notifications = db["support_notifications"]
notes = db["support_notes"]
faq = db["support_faq"]

PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


def create_indexes():
    tickets.create_index([("student_id", 1), ("updated_at", -1)], name="support_student_updated")
    tickets.create_index([("status", 1), ("priority", 1), ("updated_at", -1)], name="support_queue")
    tickets.create_index([("assignee_id", 1), ("status", 1), ("updated_at", -1)], name="support_assignee_queue")
    tickets.create_index([("category", 1), ("updated_at", -1)], name="support_category_queue")
    messages.create_index([("ticket_id", 1), ("created_at", 1)], name="support_ticket_messages")
    messages.create_index([("recipient_id", 1), ("read_at", 1)], name="support_unread_messages")
    notifications.create_index([("recipient_id", 1), ("created_at", -1)], name="support_notifications_feed")
    notifications.create_index([("recipient_id", 1), ("read_at", 1)], name="support_notifications_unread")
    notes.create_index([("ticket_id", 1), ("created_at", -1)], name="support_ticket_notes")
    faq.create_index([("is_published", 1), ("category", 1)], name="support_faq_published")
    faq.create_index([("question", 1)], name="support_faq_question")


def admins():
    return list(users_collection.find({"role": "admin", "is_active": True}, {"password": 0}).sort("first_name", 1))


def list_tickets(query: dict[str, Any], skip: int = 0, limit: int = 0, sort: str = "recent"):
    """Return the filtered tickets of a queue, with an optional priority ordering."""
    if sort == "priority":
        pipeline = [
            {"$match": query},
            {"$addFields": {"priority_rank": {"$switch": {"branches": [
                {"case": {"$eq": ["$priority", "urgent"]}, "then": 0},
                {"case": {"$eq": ["$priority", "high"]}, "then": 1},
                {"case": {"$eq": ["$priority", "normal"]}, "then": 2},
                {"case": {"$eq": ["$priority", "low"]}, "then": 3},
            ], "default": 4}}}},
            {"$sort": {"priority_rank": 1, "updated_at": -1}},
        ]
        if skip:
            pipeline.append({"$skip": skip})
        if limit:
            pipeline.append({"$limit": limit})
        return list(tickets.aggregate(pipeline))

    cursor = tickets.find(query)
    if sort == "oldest":
        cursor = cursor.sort("created_at", 1)
    else:
        cursor = cursor.sort("updated_at", -1)
    if skip:
        cursor = cursor.skip(skip)
    if limit:
        cursor = cursor.limit(limit)
    return list(cursor)


def count_tickets(query: dict[str, Any]):
    return tickets.count_documents(query)


def find_ticket(ticket_id: ObjectId):
    return tickets.find_one({"_id": ticket_id})


def insert_ticket(document):
    tickets.insert_one(document)


def update_ticket(ticket_id, fields):
    return tickets.update_one({"_id": ticket_id}, {"$set": fields}).matched_count == 1


def remove_ticket(ticket_id):
    tickets.delete_one({"_id": ticket_id})
    messages.delete_many({"ticket_id": ticket_id})
    notifications.delete_many({"ticket_id": ticket_id})
    notes.delete_many({"ticket_id": ticket_id})


def ticket_messages(ticket_id):
    return list(messages.find({"ticket_id": ticket_id}).sort("created_at", 1))


def insert_message(document):
    messages.insert_one(document)


def mark_read(ticket_id, recipient_id, now):
    messages.update_many({"ticket_id": ticket_id, "recipient_id": recipient_id, "read_at": None}, {"$set": {"read_at": now}})


def insert_notification(document):
    notifications.insert_one(document)


def notifications_for(recipient_id, limit: int = 50):
    return list(notifications.find({"recipient_id": recipient_id}).sort("created_at", -1).limit(limit))


def count_unread_notifications(recipient_id):
    return notifications.count_documents({"recipient_id": recipient_id, "read_at": None})


def mark_notifications_read(recipient_id, now, ticket_id=None):
    query: dict[str, Any] = {"recipient_id": recipient_id, "read_at": None}
    if ticket_id is not None:
        query["ticket_id"] = ticket_id
    return notifications.update_many(query, {"$set": {"read_at": now}}).modified_count


def insert_note(document):
    notes.insert_one(document)


def ticket_notes(ticket_id):
    return list(notes.find({"ticket_id": ticket_id}).sort("created_at", -1))


def find_note(note_id, ticket_id):
    return notes.find_one({"_id": note_id, "ticket_id": ticket_id})


def delete_note(note_id):
    return notes.delete_one({"_id": note_id}).deleted_count == 1


def insert_faq(document):
    faq.insert_one(document)


def list_faq(query: dict[str, Any] | None = None):
    return list(faq.find(query or {}).sort("category", 1).sort("question", 1))


def find_faq(entry_id):
    return faq.find_one({"_id": entry_id})


def update_faq(entry_id, fields):
    return faq.update_one({"_id": entry_id}, {"$set": fields}).matched_count == 1


def delete_faq(entry_id):
    return faq.delete_one({"_id": entry_id}).deleted_count == 1


def count_faq(query: dict[str, Any] | None = None):
    return faq.count_documents(query or {})


def escape_regex(value: str):
    return re.escape(value)
