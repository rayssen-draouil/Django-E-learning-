from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from django.contrib import messages
from django.http import Http404, HttpResponseForbidden, HttpResponseServerError, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.users.decorators import roles_required, user_required
from config.mongodb import db
from . import bookings, notifications, repositories, reviews
from .bookings import (
    STATUS_ACCEPTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_NO_SHOW,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_RESCHEDULED_PENDING,
    BookingError,
    SlotConflictError,
    get_booking_by_id,
    get_mentor_session_stats,
    list_mentor_bookings,
    list_student_bookings,
)
from .forms import (
    AvailabilityFilterForm,
    AvailabilitySlotForm,
    BookingRequestForm,
    MeetingLinkForm,
    RescheduleProposalForm,
    ReviewSubmissionForm,
)
from .reviews import (
    ReviewDuplicateError,
    ReviewError,
    ReviewValidationError,
    get_mentor_rating_summary,
    get_review_by_booking,
    list_mentor_reviews,
)
from .services import (
    AvailabilityBookedError,
    AvailabilityNotFoundError,
    AvailabilityOverlapError,
    AvailabilityPermissionError,
    AvailabilityStorageError,
    AvailabilityValidationError,
    PREDEFINED_TOPICS,
    create_availability_slot,
    delete_availability_slot,
    format_slot_for_display,
    get_managed_slot,
    get_mentor_availability_stats,
    list_mentor_slots,
    update_availability_slot,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public Mentor Discovery & Profiles (Phases 4 & 5)
# ---------------------------------------------------------------------------

def mentors(request):
    """
    Public mentor directory listing verified mentors from MongoDB with their ratings,
    expertise, and next available coaching slots.
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mentor_users = list(
        db["users"].find(
            {"role": "mentor", "is_active": True},
            {"password": 0},
        ).sort("first_name", 1)
    )

    mentor_cards = []
    for user in mentor_users:
        uid = user["_id"]
        rating_summary = get_mentor_rating_summary(uid)

        # Find active future available slots
        slots = list(
            db["mentor_availability"].find({
                "mentor_id": uid,
                "status": "available",
                "is_booked": False,
                "is_active": True,
                "date": {"$gte": today_str},
            }).sort([("date", 1), ("start_time", 1)]).limit(3)
        )

        next_slot = slots[0] if slots else None
        next_slot_display = (
            f"{next_slot['date']} at {next_slot['start_time']}" if next_slot else None
        )

        mentor_cards.append({
            "id": str(uid),
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "full_name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or "Mentor",
            "initials": (user.get("first_name", "M")[:1] + user.get("last_name", "")[:1]).upper(),
            "bio": user.get("bio") or "Passionate software engineer and mentor dedicated to helping learners master modern tech skills.",
            "topics": user.get("topics", ["Web Development", "Python", "Software Engineering"]),
            "experience": user.get("experience", "Senior Professional"),
            "rating_summary": rating_summary,
            "next_slot_display": next_slot_display,
            "available_slots_count": len(slots),
        })

    return render(
        request,
        "frontoffice/mentors_directory.html",
        {
            "mentors": mentor_cards,
            "total_mentors": len(mentor_cards),
            "page_title": "Mentor Directory",
            "page_intro": "Book 1-on-1 coaching sessions with experienced industry experts.",
        },
    )


def mentor_public_detail(request, mentor_id: str):
    """
    Detailed public mentor profile displaying biography, expertise, verified student reviews,
    and available coaching slots ready for booking.
    """
    try:
        mid = ObjectId(mentor_id)
    except Exception:
        raise Http404("Mentor not found.")

    mentor_user = db["users"].find_one({"_id": mid, "role": "mentor", "is_active": True})
    if not mentor_user:
        raise Http404("Mentor not found.")

    rating_summary = get_mentor_rating_summary(mid)
    reviews_list = list_mentor_reviews(mid, limit=20)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw_slots = list(
        db["mentor_availability"].find({
            "mentor_id": mid,
            "status": "available",
            "is_booked": False,
            "is_active": True,
            "date": {"$gte": today_str},
        }).sort([("date", 1), ("start_time", 1)])
    )

    available_slots = [format_slot_for_display(s) for s in raw_slots]

    return render(
        request,
        "frontoffice/mentor_detail.html",
        {
            "mentor": mentor_user,
            "mentor_id": str(mid),
            "full_name": f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Mentor",
            "initials": (mentor_user.get("first_name", "M")[:1] + mentor_user.get("last_name", "")[:1]).upper(),
            "bio": mentor_user.get("bio") or "Dedicated mentor focused on practical skills, code reviews, and career growth.",
            "topics": mentor_user.get("topics", ["Python", "Django", "Web Development"]),
            "experience": mentor_user.get("experience", "Senior Engineer"),
            "rating_summary": rating_summary,
            "reviews": reviews_list,
            "available_slots": available_slots,
        },
    )


@user_required
@require_http_methods(["GET", "POST"])
def student_book_slot(request, mentor_id: str, availability_id: str):
    """
    Booking request form for students to reserve an available coaching slot.
    """
    try:
        mid = ObjectId(mentor_id)
        aid = ObjectId(availability_id)
    except Exception:
        raise Http404("Invalid booking parameters.")

    mentor_user = db["users"].find_one({"_id": mid, "role": "mentor"})
    slot = repositories.get_availability_slot(aid)
    if not mentor_user or not slot:
        raise Http404("Mentor or availability slot not found.")

    if slot.get("mentor_id") != mid:
        messages.error(request, "This slot does not belong to the selected mentor.")
        return redirect("users:mentor_public_detail", mentor_id=mentor_id)

    if slot.get("status") != "available" or slot.get("is_booked", False):
        messages.error(request, "This slot has already been booked or is no longer available.")
        return redirect("users:mentor_public_detail", mentor_id=mentor_id)

    # Prevent mentors from booking their own slots
    if str(request.mongo_user["_id"]) == str(mid):
        messages.error(request, "You cannot book a mentorship session with yourself.")
        return redirect("users:mentor_public_detail", mentor_id=mentor_id)

    slot_display = format_slot_for_display(slot)
    form = BookingRequestForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            booking_id = bookings.create_booking_request(
                student_user=request.mongo_user,
                mentor_id=mid,
                availability_id=aid,
                learning_objective=form.cleaned_data["learning_objective"],
                problem_description=form.cleaned_data["problem_description"],
            )
            mentor_name = f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Mentor"
            messages.success(
                request,
                f"Your mentorship request for {slot_display['date']} has been sent to {mentor_name}!",
            )
            return redirect("users:profile")
        except SlotConflictError as error:
            messages.error(request, str(error))
            return redirect("users:mentor_public_detail", mentor_id=mentor_id)
        except BookingError as error:
            messages.error(request, str(error))

    return render(
        request,
        "frontoffice/book_slot.html",
        {
            "mentor": mentor_user,
            "mentor_id": str(mid),
            "slot": slot_display,
            "form": form,
        },
    )


@user_required
@require_POST
def student_cancel_booking_view(request, booking_id: str):
    """
    Cancel an active student booking request.
    """
    reason = request.POST.get("cancellation_reason", "").strip()
    try:
        bookings.student_cancel_booking(
            booking_id=booking_id,
            student_user=request.mongo_user,
            cancellation_reason=reason,
        )
        messages.success(request, "Your mentorship booking has been cancelled.")
    except Exception as error:
        messages.error(request, str(error))
    return redirect("users:profile")


@user_required
@require_POST
def student_reschedule_respond_view(request, booking_id: str):
    """
    Student accepts or declines an alternative time proposed by the mentor.
    """
    action = request.POST.get("action", "").lower().strip()
    accept = action == "accept"
    try:
        bookings.student_respond_reschedule(
            booking_id=booking_id,
            student_user=request.mongo_user,
            accept=accept,
        )
        if accept:
            messages.success(request, "You accepted the new session time. Your session is confirmed!")
        else:
            messages.info(request, "You declined the reschedule proposal. The booking was cancelled.")
    except Exception as error:
        messages.error(request, str(error))
    return redirect("users:profile")


@user_required
@require_http_methods(["GET", "POST"])
def student_submit_review_view(request, booking_id: str):
    """
    Submit a verified review for a completed mentorship session.
    """
    booking = get_booking_by_id(booking_id)
    if not booking:
        raise Http404("Booking not found.")

    if str(booking.get("student_id")) != str(request.mongo_user["_id"]):
        return HttpResponseForbidden("You can only review sessions you participated in.")

    if booking.get("status") != STATUS_COMPLETED:
        messages.error(request, "Reviews can only be submitted for completed sessions.")
        return redirect("users:profile")

    existing_review = get_review_by_booking(booking_id)
    if existing_review:
        messages.info(request, "You have already reviewed this coaching session.")
        return redirect("users:profile")

    form = ReviewSubmissionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            reviews.create_review(
                booking=booking,
                student_user=request.mongo_user,
                rating=form.cleaned_data["rating"],
                review_text=form.cleaned_data["review_text"],
            )
            messages.success(request, "Thank you! Your verified review has been published.")
            return redirect("users:profile")
        except (ReviewValidationError, ReviewDuplicateError, ReviewError) as error:
            messages.error(request, str(error))

    return render(
        request,
        "frontoffice/submit_review.html",
        {
            "booking": booking,
            "form": form,
        },
    )


# ---------------------------------------------------------------------------
# Mentor Session Management Dashboard (Phase 6)
# ---------------------------------------------------------------------------

@roles_required("mentor")
def mentor_sessions_list(request):
    """
    Mentor session management dashboard with tabs for pending requests,
    upcoming confirmed sessions, past completed sessions, and cancellations.
    """
    mid = request.mongo_user["_id"]
    stats = get_mentor_session_stats(mid)

    pending_list = (
        list_mentor_bookings(mid, status=STATUS_PENDING)
        + list_mentor_bookings(mid, status=STATUS_RESCHEDULED_PENDING)
    )
    upcoming_list = list_mentor_bookings(mid, status=STATUS_ACCEPTED)
    completed_list = list_mentor_bookings(mid, status=STATUS_COMPLETED)
    cancelled_list = (
        list_mentor_bookings(mid, status=STATUS_CANCELLED)
        + list_mentor_bookings(mid, status=STATUS_REJECTED)
        + list_mentor_bookings(mid, status=STATUS_NO_SHOW)
    )

    summary_stats = [
        ("Pending Requests", str(stats["pending_count"]), "light-orange"),
        ("Upcoming Confirmed", str(stats["upcoming_count"]), "light-green"),
        ("Sessions Completed", str(stats["completed_count"]), "light-blue"),
        ("Cancellations / No-Shows", str(stats["cancellations_count"]), "light-purple"),
    ]

    return render(
        request,
        "backoffice/mentors/sessions_list.html",
        {
            "user": request.mongo_user,
            "role_label": "Mentor",
            "section": "sessions",
            "title": "Mentorship Sessions",
            "subtitle": "Manage incoming booking requests and your coaching sessions schedule.",
            "stats": summary_stats,
            "session_stats": stats,
            "pending_sessions": pending_list,
            "upcoming_sessions": upcoming_list,
            "completed_sessions": completed_list,
            "cancelled_sessions": cancelled_list,
        },
    )


@roles_required("mentor")
@require_POST
def mentor_accept_booking_view(request, booking_id: str):
    """
    Accept an incoming student booking request and atomically claim the slot.
    """
    meeting_link = request.POST.get("meeting_link", "").strip()
    try:
        bookings.mentor_accept_booking(
            booking_id=booking_id,
            mentor_user=request.mongo_user,
            meeting_link=meeting_link,
        )
        messages.success(request, "Session confirmed successfully! The student has been notified.")
    except Exception as error:
        messages.error(request, str(error))
    return redirect("users:mentor_sessions_list")


@roles_required("mentor")
@require_POST
def mentor_reject_booking_view(request, booking_id: str):
    """
    Decline an incoming student booking request with an optional note.
    """
    reason = request.POST.get("rejection_reason", "").strip()
    try:
        bookings.mentor_reject_booking(
            booking_id=booking_id,
            mentor_user=request.mongo_user,
            rejection_reason=reason,
        )
        messages.info(request, "The booking request has been declined.")
    except Exception as error:
        messages.error(request, str(error))
    return redirect("users:mentor_sessions_list")


@roles_required("mentor")
@require_POST
def mentor_propose_reschedule_view(request, booking_id: str):
    """
    Propose a new date and time for a booking request.
    """
    booking = bookings.get_booking_by_id(booking_id)
    min_date = booking.get("date") if booking else None

    form = RescheduleProposalForm(request.POST, min_date=min_date)
    if form.is_valid():
        try:
            bookings.mentor_propose_reschedule(
                booking_id=booking_id,
                mentor_user=request.mongo_user,
                proposed_date=form.cleaned_data["proposed_date"].strftime("%Y-%m-%d"),
                proposed_start_time=form.cleaned_data["proposed_start_time"].strftime("%H:%M"),
                proposed_end_time=form.cleaned_data["proposed_end_time"].strftime("%H:%M"),
                reschedule_note=form.cleaned_data.get("reschedule_note", ""),
            )
            messages.success(request, "Reschedule proposal sent to the student.")
        except Exception as error:
            messages.error(request, str(error))
    else:
        err_msgs: list[str] = []
        for field_errs in form.errors.values():
            err_msgs.extend(field_errs)
        if err_msgs:
            messages.error(request, " ".join(err_msgs))
        else:
            messages.error(request, "Please provide valid date and start/end times for rescheduling.")
    return redirect("users:mentor_sessions_list")


@roles_required("mentor")
@require_POST
def mentor_update_meeting_link_view(request, booking_id: str):
    """
    Update meeting URL for an accepted online coaching session.
    """
    meeting_link = request.POST.get("meeting_link", "").strip()
    try:
        bookings.mentor_update_meeting_link(
            booking_id=booking_id,
            mentor_user=request.mongo_user,
            meeting_link=meeting_link,
        )
        messages.success(request, "Meeting link updated and shared with the student.")
    except Exception as error:
        messages.error(request, str(error))
    return redirect("users:mentor_sessions_list")


@roles_required("mentor")
@require_POST
def mentor_complete_session_view(request, booking_id: str):
    """
    Mark an accepted session as completed, triggering student review invitation.
    """
    try:
        bookings.mentor_complete_session(
            booking_id=booking_id,
            mentor_user=request.mongo_user,
        )
        messages.success(request, "Session marked as completed. The student was invited to review.")
    except Exception as error:
        messages.error(request, str(error))
    return redirect("users:mentor_sessions_list")


@roles_required("mentor")
@require_POST
def mentor_mark_no_show_view(request, booking_id: str):
    """
    Mark an accepted session as a no-show.
    """
    try:
        bookings.mentor_mark_no_show(
            booking_id=booking_id,
            mentor_user=request.mongo_user,
        )
        messages.warning(request, "Session has been recorded as a no-show.")
    except Exception as error:
        messages.error(request, str(error))
    return redirect("users:mentor_sessions_list")


@roles_required("mentor")
@require_POST
def mentor_cancel_booking_view(request, booking_id: str):
    """
    Mentor cancels an accepted session.
    """
    reason = request.POST.get("cancellation_reason", "").strip()
    try:
        bookings.mentor_cancel_booking(
            booking_id=booking_id,
            mentor_user=request.mongo_user,
            cancellation_reason=reason,
        )
        messages.info(request, "The session has been cancelled and the slot released.")
    except Exception as error:
        messages.error(request, str(error))
    return redirect("users:mentor_sessions_list")


# ---------------------------------------------------------------------------
# Notification Center Views (Phase 7)
# ---------------------------------------------------------------------------

@user_required
def notifications_list_view(request):
    """
    Full notification history view for the authenticated user.
    """
    user_notifications = notifications.list_user_notifications(
        request.mongo_user["_id"],
        limit=50,
    )
    return render(
        request,
        "notifications/notifications_list.html",
        {
            "user": request.mongo_user,
            "notifications": user_notifications,
            "unread_count": sum(1 for n in user_notifications if not n.get("is_read")),
        },
    )


@user_required
@require_POST
def notification_mark_read_view(request, notification_id: str):
    """
    Mark a single notification as read.
    """
    notifications.mark_notification_as_read(
        notification_id=notification_id,
        recipient_id=request.mongo_user["_id"],
    )
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/users/notifications/"
    return redirect(next_url)


@user_required
@require_POST
def notification_mark_all_read_view(request):
    """
    Mark all notifications for the authenticated user as read.
    """
    notifications.mark_all_notifications_as_read(request.mongo_user["_id"])
    messages.success(request, "All notifications marked as read.")
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/users/notifications/"
    return redirect(next_url)


@user_required
@require_GET
def notification_unread_count_api(request):
    """
    JSON API for live polling of unread notification count.
    """
    count = notifications.get_unread_count(request.mongo_user["_id"])
    return JsonResponse({"unread_count": count})


# ---------------------------------------------------------------------------
# Legacy & Availability Management (Phases 1 & Existing)
# ---------------------------------------------------------------------------

@roles_required("instructor", "admin")
def backoffice_mentors(request):
    return render(
        request,
        "backoffice/section_dashboard.html",
        {
            "user": request.mongo_user,
            "section": "mentors",
            "title": "Dashboard des mentors",
            "intro": "Trouvez le bon accompagnement pour atteindre vos objectifs.",
            "stats": [
                ("Mentors disponibles", "18", "light-blue"),
                ("Sessions réalisées", "32", "light-orange"),
                ("Mentor préféré", "4.9/5", "light-purple"),
                ("Heures de coaching", "46h", "light-green"),
            ],
            "items": [
                (
                    "Amine Benali",
                    "Python & architecture logicielle",
                    "Disponible",
                    "12 ans d'expérience",
                    "user-1.jpg",
                ),
                (
                    "Sofia Martin",
                    "Design produit & UX",
                    "Disponible",
                    "9 ans d'expérience",
                    "user-2.jpg",
                ),
                (
                    "Lucas Bernard",
                    "Data science & IA",
                    "Demain à 14h",
                    "10 ans d'expérience",
                    "user-3.jpg",
                ),
            ],
        },
    )


def _summary_cards(stats: dict[str, int]) -> list[tuple[str, str, str]]:
    return [
        ("Total availability slots", str(stats["total_slots"]), "light-blue"),
        ("Upcoming available slots", str(stats["upcoming_slots"]), "light-green"),
        ("Online sessions", str(stats["online_slots"]), "light-purple"),
        ("In-person sessions", str(stats["in_person_slots"]), "light-orange"),
    ]


def _managed_slot_or_response(request, slot_id: str):
    try:
        slot = get_managed_slot(slot_id, user=request.mongo_user)
        return slot, None
    except AvailabilityNotFoundError:
        raise Http404
    except AvailabilityPermissionError:
        return None, HttpResponseForbidden("Accès refusé.")
    except AvailabilityStorageError:
        messages.error(
            request,
            "We couldn't save your changes. Please check your connection and try again.",
        )
        return None, redirect("users:mentor_availability_list")


@roles_required("mentor")
def manage_availability_list(request):
    if request.GET.get("reset") == "1":
        messages.info(
            request,
            "All filters cleared. You're viewing your complete schedule again.",
        )

    filter_form = AvailabilityFilterForm(request.GET or None)
    filter_kwargs: dict[str, Any] = {}
    if filter_form.is_valid():
        date_value = filter_form.cleaned_data.get("date")
        if date_value:
            filter_kwargs["date"] = date_value.strftime("%Y-%m-%d")
        topic_value = filter_form.cleaned_data.get("topic")
        if topic_value:
            filter_kwargs["topic"] = topic_value
        session_type_value = filter_form.cleaned_data.get("session_type")
        if session_type_value:
            filter_kwargs["session_type"] = session_type_value

    try:
        stats = get_mentor_availability_stats(request.mongo_user)
        slots = list_mentor_slots(request.mongo_user, **filter_kwargs)
    except AvailabilityStorageError:
        messages.error(
            request,
            "We couldn't save your changes. Please check your connection and try again.",
        )
        stats = {
            "total_slots": 0,
            "upcoming_slots": 0,
            "online_slots": 0,
            "in_person_slots": 0,
            "booked_slots": 0,
        }
        slots = []

    return render(
        request,
        "backoffice/mentors/availability_list.html",
        {
            "user": request.mongo_user,
            "role_label": "Mentor",
            "section": "availability",
            "title": "My Availability",
            "subtitle": "Create and manage your coaching time slots.",
            "stats": _summary_cards(stats),
            "slots": slots,
            "filter_form": filter_form,
            "form": AvailabilitySlotForm(),
            "total_slots": len(slots),
            "topics": PREDEFINED_TOPICS,
        },
    )


@roles_required("mentor")
@require_http_methods(["GET", "POST"])
def manage_availability_create(request):
    form = AvailabilitySlotForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            try:
                create_availability_slot(
                    data=form.to_document(),
                    user=request.mongo_user,
                )
            except AvailabilityOverlapError as error:
                form.add_error(None, str(error))
            except AvailabilityValidationError as error:
                if error.field and error.field in form.fields:
                    form.add_error(error.field, error.message)
                else:
                    form.add_error(None, error.message)
            except AvailabilityStorageError:
                messages.error(
                    request,
                    "We couldn't save your changes. Please check your connection and try again.",
                )
                return redirect("users:mentor_availability_list")
            else:
                messages.success(request, "Availability created successfully.")
                return redirect("users:mentor_availability_list")

    return render(
        request,
        "backoffice/mentors/availability_form.html",
        {
            "user": request.mongo_user,
            "role_label": "Mentor",
            "section": "availability",
            "mode": "create",
            "title": "Create Availability",
            "subtitle": "Set a new time slot when learners can book mentorship sessions with you.",
            "submit_label": "Create Availability",
            "form": form,
            "topics": PREDEFINED_TOPICS,
        },
    )


@roles_required("mentor")
def manage_availability_detail(request, slot_id: str):
    document, response = _managed_slot_or_response(request, slot_id)
    if response:
        return response

    slot = format_slot_for_display(document)
    return render(
        request,
        "backoffice/mentors/availability_detail.html",
        {
            "user": request.mongo_user,
            "role_label": "Mentor",
            "section": "availability",
            "title": "Availability Details",
            "slot": slot,
        },
    )


@roles_required("mentor")
@require_http_methods(["GET", "POST"])
def manage_availability_edit(request, slot_id: str):
    document, response = _managed_slot_or_response(request, slot_id)
    if response:
        return response

    slot = format_slot_for_display(document)
    initial = {
        "date": document["date"],
        "start_time": document["start_time"],
        "end_time": document["end_time"],
        "topic": document["topic"],
        "custom_topic": document.get("custom_topic", ""),
        "session_type": document["session_type"],
        "location": document.get("location", ""),
    }
    form = AvailabilitySlotForm(request.POST or None, initial=initial)
    if request.method == "POST":
        if form.is_valid():
            try:
                update_availability_slot(
                    slot_id,
                    data=form.to_document(),
                    user=request.mongo_user,
                )
            except AvailabilityOverlapError as error:
                form.add_error(None, str(error))
            except AvailabilityValidationError as error:
                if error.field and error.field in form.fields:
                    form.add_error(error.field, error.message)
                else:
                    form.add_error(None, error.message)
            except AvailabilityStorageError:
                messages.error(
                    request,
                    "We couldn't save your changes. Please check your connection and try again.",
                )
                return redirect("users:mentor_availability_list")
            else:
                messages.success(request, "Availability updated successfully.")
                return redirect("users:mentor_availability_list")

    return render(
        request,
        "backoffice/mentors/availability_form.html",
        {
            "user": request.mongo_user,
            "role_label": "Mentor",
            "section": "availability",
            "mode": "edit",
            "title": "Edit Availability",
            "subtitle": "Update your coaching time slot details.",
            "submit_label": "Save Changes",
            "slot": slot,
            "form": form,
            "topics": PREDEFINED_TOPICS,
        },
    )


@roles_required("mentor")
@require_http_methods(["GET", "POST"])
def manage_availability_delete(request, slot_id: str):
    document, response = _managed_slot_or_response(request, slot_id)
    if response:
        return response

    slot = format_slot_for_display(document)
    if request.method == "POST":
        try:
            delete_availability_slot(slot_id, user=request.mongo_user)
        except AvailabilityBookedError as error:
            messages.error(request, str(error))
            return render(
                request,
                "backoffice/mentors/availability_delete.html",
                {
                    "user": request.mongo_user,
                    "role_label": "Mentor",
                    "section": "availability",
                    "title": "Delete Availability",
                    "slot": slot,
                    "booking_error": str(error),
                },
                status=409,
            )
        except AvailabilityNotFoundError:
            raise Http404
        except AvailabilityPermissionError:
            return HttpResponseForbidden("Accès refusé.")
        except AvailabilityStorageError:
            messages.error(
                request,
                "We couldn't save your changes. Please check your connection and try again.",
            )
            return redirect("users:mentor_availability_list")
        else:
            messages.success(request, "Availability removed successfully.")
            return redirect("users:mentor_availability_list")

    return render(
        request,
        "backoffice/mentors/availability_delete.html",
        {
            "user": request.mongo_user,
            "role_label": "Mentor",
            "section": "availability",
            "title": "Delete Availability",
            "slot": slot,
        },
    )
