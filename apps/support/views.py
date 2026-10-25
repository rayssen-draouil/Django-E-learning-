from pathlib import Path

import json

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponseForbidden, HttpResponseServerError, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from pymongo.errors import PyMongoError

from apps.users.decorators import roles_required, user_required
from . import ai, repositories, services
from .forms import AdminTicketForm, CATEGORIES, FaqForm, NoteForm, PRIORITIES, ReplyForm, STATUSES, TicketFilterForm, TicketForm


def _storage_error(request):
    messages.error(request, "The support service is temporarily unavailable.")
    return HttpResponseServerError("Support service unavailable.")


def _max_count(counts):
    """Largest counter value of a distribution, used to scale the dashboard bars."""
    return max((count for _, count in counts), default=0) or 1


# --------------------------------------------------------------------------
# AI Assistance (Groq). All entry points respond in JSON:
#   - ia/suggestions/  → student: classification + FAQ solutions (pre-filled);
#   - <ticket>/ia/resume/ → admin: ticket summary, stored on the ticket;
#   - <ticket>/ia/reponse/ → admin: reply draft, never sent automatically
#     — the administrator reviews, edits, then submits the normal reply
#     form (human validation mandatory).
# --------------------------------------------------------------------------

def _load_json(request) -> dict:
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _ai_response(request, result: dict) -> JsonResponse:
    return JsonResponse({"ok": True, **result})


def _ai_error(error: str) -> JsonResponse:
    return JsonResponse({"ok": False, "error": error}, status=200)


@user_required
@require_http_methods(["POST"])
def ai_suggestion(request):
    """1)+2) AI analysis of a description entered in the student form."""
    data = _load_json(request)
    title, description = str(data.get("title") or ""), str(data.get("description") or "")
    if not (title or description):
        return _ai_error("Describe your issue, then run the analysis.")
    try:
        result = ai.ai_suggestions(title[:200], description[:2000])
    except Exception:  # noqa: BLE001 — AI must never break the page
        return _ai_error("The AI assistant is temporarily unavailable.")
    return _ai_response(request, result)


@roles_required("admin")
@require_http_methods(["POST"])
def ai_summary(request, ticket_id):
    """3) Automatic ticket summary (main problem + actions already taken)."""
    try:
        ticket = services.get_ticket(ticket_id, request.mongo_user)
        messages = services.messages_for(ticket, request.mongo_user)
        notes = services.notes_for(ticket)
    except services.TicketNotFound:
        raise Http404
    except services.TicketPermissionDenied:
        return HttpResponseForbidden("Access denied.")
    except (services.SupportStorageError, PyMongoError):
        return _storage_error(request)
    try:
        result = ai.ai_summary(ticket, messages, notes)
    except Exception:  # noqa: BLE001
        return _ai_error("The AI assistant is temporarily unavailable.")
    try:
        repositories.update_ticket(ticket["_id"], {
            "ai_summary": result, "ai_summary_at": services.now(),
        })
    except PyMongoError:
        pass  # summary remains displayable even if persistence fails
    return _ai_response(request, result)


@roles_required("admin")
@require_http_methods(["POST"])
def ai_draft(request, ticket_id):
    """4) AI reply draft — human validation mandatory before sending."""
    try:
        ticket = services.get_ticket(ticket_id, request.mongo_user)
        messages = services.messages_for(ticket, request.mongo_user)
    except services.TicketNotFound:
        raise Http404
    except services.TicketPermissionDenied:
        return HttpResponseForbidden("Access denied.")
    except (services.SupportStorageError, PyMongoError):
        return _storage_error(request)
    try:
        result = ai.ai_draft_reply(ticket, messages)
    except Exception:  # noqa: BLE001
        return _ai_error("The AI assistant is temporarily unavailable.")
    try:
        repositories.update_ticket(ticket["_id"], {
            "ai_reply": result, "ai_reply_at": services.now(),
        })
    except PyMongoError:
        pass
    return _ai_response(request, result)


@user_required
def ticket_list(request):
    try:
        is_admin = request.mongo_user.get("role") == "admin"
        admins = repositories.admins() if is_admin else []
        filter_form = TicketFilterForm(request.GET or None, user=request.mongo_user, admins=admins)
        filters = filter_form.filters()
        queue = services.paginate_tickets(request.mongo_user, filters, page=request.GET.get("page", 1))
        for item in queue["tickets"]: item["id"] = str(item["_id"])
        unread = services.unread_notification_count(request.mongo_user)
        stats = services.dashboard_stats(services.filtered_tickets(request.mongo_user, filters)) if is_admin else None
    except (services.SupportStorageError, PyMongoError):
        return _storage_error(request)

    # Front page: the "New request" form lives directly on this page;
    # a valid POST creates the ticket and leads to its detail page.
    creation_form = TicketForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and creation_form.is_valid():
        try:
            ticket = services.create_ticket(creation_form.cleaned_data, request.mongo_user)
        except (services.SupportStorageError, PyMongoError):
            return _storage_error(request)
        messages.success(request, "Your request was created. The assistant also prepared some helpful suggestions.")
        return redirect("support:detail", ticket_id=ticket["_id"])

    is_front = request.GET.get("view") == "front" or not is_admin
    template_name = "support/list_student.html" if is_front else "support/list_admin.html"
    return render(request, template_name, {"tickets": queue["tickets"], "queue": queue, "filters": filters,
        "filter_form": filter_form, "is_admin": is_admin, "unread_count": unread, "stats": stats,
        "creation_form": creation_form,
        "categories": dict(CATEGORIES), "priorities": dict(PRIORITIES), "statuses": dict(STATUSES)})


@roles_required("admin")
def dashboard(request):
    try:
        tickets = services.list_for_user(request.mongo_user)
        for item in tickets: item["id"] = str(item["_id"])
        stats = services.dashboard_stats(tickets)
        recurring = services.recurring_problems(tickets)
    except (services.SupportStorageError, PyMongoError):
        return _storage_error(request)
    return render(request, "support/dashboard.html", {"stats": stats, "recurring": recurring,
        "categories": dict(CATEGORIES), "priorities": dict(PRIORITIES), "statuses": dict(STATUSES),
        "categories_max": _max_count(stats["categories"]), "priorities_max": _max_count(stats["priorities"]),
        "statuses_max": _max_count(stats["statuses"]), "role_label": "ESPACE ADMINISTRATEUR"})


@user_required
def backoffice_board(request):
    """Support dashboard integrated into the back-office shell: internal
    navigation must stay in the back-end interface (my requests viewable)
    instead of leading to the front page."""
    try:
        tickets = services.list_for_user(request.mongo_user)
        status_labels = dict(STATUSES)
        priority_labels = dict(PRIORITIES)
        counts = {"open": 0, "in_progress": 0, "waiting": 0, "resolved": 0}
        for item in tickets:
            item["id"] = str(item["_id"])
            status = item.get("status", "open")
            item["status_label"] = status_labels.get(status, status)
            item["priority_label"] = priority_labels.get(item.get("priority", "normal"), item.get("priority", ""))
            if status in counts:
                counts[status] += 1
        unread = services.unread_notification_count(request.mongo_user)
    except (services.SupportStorageError, PyMongoError):
        return _storage_error(request)
    return render(request, "support/board.html", {"tickets": tickets, "counts": counts,
        "tickets_count": len(tickets), "unread_count": unread})


@roles_required("admin")
def faq_list(request):
    try:
        entries = repositories.list_faq({})
    except PyMongoError:
        return _storage_error(request)
    for entry in entries: entry["id"] = str(entry["_id"])
    return render(request, "support/faq.html", {"entries": entries, "categories": dict(CATEGORIES),
        "published_count": sum(1 for entry in entries if entry.get("is_published"))})


@roles_required("admin")
@require_http_methods(["GET", "POST"])
def faq_create(request):
    form = FaqForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.create_faq(form.cleaned_data, request.mongo_user)
        except (services.SupportStorageError, PyMongoError):
            return _storage_error(request)
        messages.success(request, "The solution was added to the knowledge base.")
        return redirect("support:faq")
    return render(request, "support/faq_form.html", {"form": form, "heading": "New solution"})


@roles_required("admin")
@require_http_methods(["GET", "POST"])
def faq_edit(request, entry_id):
    try:
        entry = repositories.find_faq(services.parse_id(entry_id))
    except services.TicketNotFound:
        raise Http404
    except PyMongoError:
        return _storage_error(request)
    if not entry:
        raise Http404
    form = FaqForm(request.POST or None, initial={"question": entry.get("question", ""), "answer": entry.get("answer", ""),
                                                  "keywords": ", ".join(entry.get("keywords", [])), "category": entry.get("category", "other"),
                                                  "is_published": entry.get("is_published", False)})
    if request.method == "POST" and form.is_valid():
        try:
            services.update_faq(entry["_id"], form.cleaned_data, request.mongo_user)
        except (services.SupportStorageError, PyMongoError):
            return _storage_error(request)
        messages.success(request, "The solution was updated.")
        return redirect("support:faq")
    return render(request, "support/faq_form.html", {"form": form, "heading": "Edit solution", "entry": entry})


@roles_required("admin")
@require_http_methods(["POST"])
def faq_delete(request, entry_id):
    try:
        services.parse_id(entry_id)
        services.delete_faq(services.parse_id(entry_id))
    except services.TicketNotFound:
        raise Http404
    except PyMongoError:
        return _storage_error(request)
    messages.success(request, "The solution was deleted.")
    return redirect("support:faq")


@user_required
@require_http_methods(["GET", "POST"])
def notifications(request):
    if request.method == "POST":
        try:
            services.mark_notifications_read(request.mongo_user)
        except (services.SupportStorageError, PyMongoError):
            return _storage_error(request)
        messages.success(request, "All notifications have been marked as read.")
        return redirect("support:notifications")
    try:
        items = services.notifications_for(request.mongo_user)
    except (services.SupportStorageError, PyMongoError):
        return _storage_error(request)
    unread = sum(1 for item in items if not item.get("read_at"))
    role = str(request.mongo_user.get("role", "student"))
    return render(request, "support/notifications.html", {"notifications": items,
        "kinds": services.NOTIFICATION_KINDS, "categories": dict(CATEGORIES),
        "unread_count": unread,
        "role_label": "ESPACE ADMINISTRATEUR" if role == "admin" else "ESPACE APPRENANT"})


@user_required
@require_http_methods(["GET", "POST"])
def ticket_create(request):
    """Legacy route: the form now lives on the front page
    /support/ (anchor #new-request). GET redirects there directly; POST
    requests from old open forms are processed, then redirected to front."""
    if request.mongo_user.get("role") == "admin":
        return HttpResponseForbidden("Administrators cannot create student tickets.")
    front_url = reverse("support:list") + "#nouvelle-demande"
    if request.method == "POST":
        form = TicketForm(request.POST)
        if form.is_valid():
            try:
                ticket = services.create_ticket(form.cleaned_data, request.mongo_user)
            except (services.SupportStorageError, PyMongoError):
                return _storage_error(request)
            messages.success(request, "Your request was created. The assistant also prepared some helpful suggestions.")
            return redirect("support:detail", ticket_id=ticket["_id"])
        messages.error(request, "The request could not be created. Check the form fields on the Support page.")
        return redirect(front_url)
    return redirect(front_url)


@user_required
@require_http_methods(["GET", "POST"])
def ticket_detail(request, ticket_id):
    try:
        ticket = services.get_ticket(ticket_id, request.mongo_user)
        admins = repositories.admins() if request.mongo_user.get("role") == "admin" else []
    except services.TicketNotFound:
        raise Http404
    except services.TicketPermissionDenied:
        return HttpResponseForbidden("Access denied.")
    except (services.SupportStorageError, PyMongoError):
        return _storage_error(request)

    is_admin = request.mongo_user.get("role") == "admin"
    ticket["id"] = str(ticket["_id"])

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "admin_update" and is_admin:
            admin_form = AdminTicketForm(request.POST, admins=admins)
            if admin_form.is_valid():
                try: services.set_admin_fields(ticket_id, admin_form.cleaned_data, request.mongo_user)
                except services.TicketNotFound: raise Http404
                except services.TicketPermissionDenied: return HttpResponseForbidden("Access denied.")
                except (services.SupportStorageError, PyMongoError): return _storage_error(request)
                messages.success(request, "Ticket updated.")
                return redirect("support:detail", ticket_id=ticket_id)
        elif action == "reply":
            reply_form = ReplyForm(request.POST, request.FILES)
            if reply_form.is_valid():
                try: services.reply(ticket_id, reply_form.cleaned_data["body"], reply_form.cleaned_data["attachment"], request.mongo_user)
                except services.TicketNotFound: raise Http404
                except services.TicketPermissionDenied: return HttpResponseForbidden("Access denied.")
                except (services.SupportStorageError, PyMongoError): return _storage_error(request)
                messages.success(request, "Your reply was sent.")
                return redirect("support:detail", ticket_id=ticket_id)
        elif action == "edit":
            edit_form = TicketForm(request.POST)
            edit_form.fields.pop("priority")
            if edit_form.is_valid():
                try: services.update_ticket_content(ticket_id, edit_form.cleaned_data, request.mongo_user)
                except services.TicketNotFound: raise Http404
                except services.TicketPermissionDenied: return HttpResponseForbidden("This ticket can no longer be edited.")
                except (services.SupportStorageError, PyMongoError): return _storage_error(request)
                messages.success(request, "The request was updated.")
                return redirect("support:detail", ticket_id=ticket_id)
        elif action == "close":
            try: services.close_student_ticket(ticket_id, request.mongo_user)
            except services.TicketNotFound: raise Http404
            except services.TicketPermissionDenied: return HttpResponseForbidden("This ticket cannot be closed.")
            except (services.SupportStorageError, PyMongoError): return _storage_error(request)
            messages.success(request, "Your ticket was marked as resolved.")
            return redirect("support:detail", ticket_id=ticket_id)
        elif action == "note":
            if not is_admin:
                return HttpResponseForbidden("Internal notes are reserved for the support team.")
            note_form = NoteForm(request.POST)
            if note_form.is_valid():
                try: services.add_note(ticket_id, note_form.cleaned_data["body"], request.mongo_user)
                except services.TicketNotFound: raise Http404
                except services.TicketPermissionDenied: return HttpResponseForbidden("Access denied.")
                except (services.SupportStorageError, PyMongoError): return _storage_error(request)
                messages.success(request, "Internal note added.")
                return redirect("support:detail", ticket_id=ticket_id)

    try:
        ticket_messages = services.messages_for(ticket, request.mongo_user)
        for item in ticket_messages: item["id"] = str(item["_id"])
        services.mark_notifications_read(request.mongo_user, ticket_id=ticket["_id"])
    except services.TicketNotFound: raise Http404
    except services.TicketPermissionDenied: return HttpResponseForbidden("Access denied.")
    except (services.SupportStorageError, PyMongoError): return _storage_error(request)

    internal_notes = []
    if is_admin:
        try:
            internal_notes = services.notes_for(ticket)
            for item in internal_notes:
                item["id"] = str(item["_id"])
                item["can_delete"] = item.get("author_id") == request.mongo_user.get("_id")
        except (services.SupportStorageError, PyMongoError):
            return _storage_error(request)

    edit_form = TicketForm(initial={"title": ticket["title"], "description": ticket["description"], "category": ticket["category"]})
    edit_form.fields.pop("priority")
    if request.method == "POST" and request.POST.get("action") == "edit":
        edit_form = TicketForm(request.POST)
        edit_form.fields.pop("priority")
    reply_form = ReplyForm(request.POST if request.method == "POST" and request.POST.get("action") == "reply" else None, request.FILES if request.method == "POST" else None)
    admin_form = AdminTicketForm(initial={
        "status": ticket["status"], "priority": ticket["priority"],
        "assignee": str(ticket["assignee_id"]) if ticket.get("assignee_id") else "",
    }, admins=admins) if is_admin else None
    template = "support/detail_admin.html" if is_admin else "support/detail_student.html"
    return render(request, template, {"ticket": ticket, "ticket_messages": ticket_messages,
        "smart": services.smart_help(ticket), "reply_form": reply_form, "edit_form": edit_form,
        "admin_form": admin_form, "is_admin": is_admin, "can_edit": is_admin or ticket["status"] != "resolved",
        "can_delete": is_admin or (ticket["status"] == "open" and not ticket_messages),
        "can_close": not is_admin and ticket["status"] != "resolved",
        "internal_notes": internal_notes, "note_form": NoteForm() if is_admin else None,
        "categories": dict(CATEGORIES), "priorities": dict(PRIORITIES), "statuses": dict(STATUSES)})


@user_required
@require_http_methods(["POST"])
def note_delete(request, ticket_id, note_id):
    try: services.delete_note(note_id, ticket_id, request.mongo_user)
    except services.TicketNotFound: raise Http404
    except services.TicketPermissionDenied: return HttpResponseForbidden("This internal note cannot be deleted.")
    except (services.SupportStorageError, PyMongoError): return _storage_error(request)
    messages.success(request, "Internal note deleted.")
    return redirect("support:detail", ticket_id=ticket_id)


@user_required
@require_http_methods(["POST"])
def ticket_delete(request, ticket_id):
    try: services.delete_student_ticket(ticket_id, request.mongo_user)
    except services.TicketNotFound: raise Http404
    except services.TicketPermissionDenied: return HttpResponseForbidden("This ticket cannot be deleted.")
    except (services.SupportStorageError, PyMongoError): return _storage_error(request)
    messages.success(request, "Ticket deleted.")
    return redirect("support:list")


@user_required
@require_http_methods(["GET"])
def attachment(request, ticket_id, message_id):
    try:
        ticket = services.get_ticket(ticket_id, request.mongo_user)
        message = repositories.messages.find_one({"_id": services.parse_id(message_id), "ticket_id": ticket["_id"]})
    except services.TicketNotFound: raise Http404
    except services.TicketPermissionDenied: return HttpResponseForbidden("Access denied.")
    except (services.SupportStorageError, PyMongoError): return _storage_error(request)
    if not message or not message.get("attachment"): raise Http404
    root = (Path(settings.MEDIA_ROOT) / "support").resolve()
    path = (Path(settings.MEDIA_ROOT) / message["attachment"]["path"]).resolve()
    if root not in path.parents or not path.is_file(): raise Http404
    return FileResponse(path.open("rb"), as_attachment=True, filename=message["attachment"]["name"])
