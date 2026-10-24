from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from django.contrib import messages
from django.http import Http404, HttpResponseForbidden, HttpResponseServerError, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.users.decorators import roles_required, user_required
from config.mongodb import db
from . import bookings, classroom, notifications, recommendations, repositories, reviews
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
    MentorProfileSettingsForm,
    MentorRecommendationForm,
    MentorReviewResponseForm,
    RescheduleProposalForm,
    ReviewReportForm,
    ReviewSubmissionForm,
)
from .reviews import (
    ReviewDuplicateError,
    ReviewError,
    ReviewNotFoundError,
    ReviewPermissionError,
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


@require_http_methods(["GET", "POST"])
def mentor_recommendations_view(request):
    """
    AI-Powered & Hybrid Multi-Criteria Mentor Recommendation engine view.
    Supports both natural-language learning goals (analyzed via Groq openai/gpt-oss-120b)
    and structured preferences, with deterministic fallback and explainable match results.
    """
    source_data = request.POST if request.method == "POST" else request.GET
    has_query = bool(
        request.method == "POST"
        or any(k in request.GET for k in ("learning_goal", "topic", "experience_level", "session_type", "preferred_date"))
    )

    form = MentorRecommendationForm(source_data if has_query else None)
    criteria_kwargs: dict[str, Any] = {}
    if has_query and form.is_valid():
        cleaned = form.cleaned_data
        pref_date = cleaned.get("preferred_date")
        criteria_kwargs = {
            "learning_goal": cleaned.get("learning_goal", ""),
            "topic": cleaned.get("topic", "any") if cleaned.get("topic") != "any" else "",
            "experience_level": cleaned.get("experience_level", "any"),
            "teaching_style": cleaned.get("teaching_style", "any"),
            "preferred_language": cleaned.get("preferred_language", "any"),
            "session_type": cleaned.get("session_type", "any"),
            "preferred_date": pref_date.strftime("%Y-%m-%d") if pref_date else "",
            "preferred_duration": cleaned.get("preferred_duration") or 0,
            "require_available_slot": bool(cleaned.get("require_available_slot", False)),
        }

    current_uid = None
    if hasattr(request, "mongo_user") and isinstance(request.mongo_user, dict):
        current_uid = request.mongo_user.get("_id")
    elif request.session.get("user_id"):
        current_uid = request.session.get("user_id")

    result = recommendations.recommend_mentors(
        exclude_user_id=current_uid,
        use_ai=True,
        **criteria_kwargs,
    )

    if has_query:
        recommendations.log_recommendation_event(
            student_user=getattr(request, "mongo_user", None),
            event_type="search",
            criteria=result["criteria"],
            ai_used=result["ai_used"],
        )

    return render(
        request,
        "frontoffice/mentor_recommendations.html",
        {
            "form": form,
            "result": result,
            "recommendations": result["recommendations"],
            "unavailable_alternatives": result["unavailable_alternatives"],
            "intent": result["intent"],
            "has_searched": has_query,
            "today_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
    )


def mentor_public_detail(request, mentor_id: str):
    """
    Detailed public mentor profile displaying biography, expertise, multi-dimensional
    verified student reviews, rating breakdown, and available coaching slots.
    """
    try:
        mid = ObjectId(mentor_id)
    except Exception:
        raise Http404("Mentor not found.")

    mentor_user = db["users"].find_one({"_id": mid, "role": "mentor", "is_active": True})
    if not mentor_user:
        raise Http404("Mentor not found.")

    rating_summary = get_mentor_rating_summary(mid)

    # Pagination for public reviews
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    per_page = 8
    offset = (page - 1) * per_page

    reviews_list = list_mentor_reviews(mid, limit=per_page + 1, offset=offset)
    has_next_page = len(reviews_list) > per_page
    reviews_page = reviews_list[:per_page]

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

    raw_topics = mentor_user.get("topics", ["Python", "Django", "Web Development"])
    if isinstance(raw_topics, str):
        raw_topics = [t.strip() for t in raw_topics.split(",") if t.strip()]

    raw_langs = mentor_user.get("languages", [])
    if isinstance(raw_langs, str):
        raw_langs = [l.strip() for l in raw_langs.split(",") if l.strip()]

    return render(
        request,
        "frontoffice/mentor_detail.html",
        {
            "mentor": mentor_user,
            "mentor_id": str(mid),
            "full_name": f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Mentor",
            "initials": (mentor_user.get("first_name", "M")[:1] + mentor_user.get("last_name", "")[:1]).upper(),
            "bio": mentor_user.get("bio") or "Dedicated mentor focused on practical skills, code reviews, and career growth.",
            "topics": raw_topics,
            "languages": raw_langs,
            "teaching_style": mentor_user.get("teaching_style", ""),
            "experience": mentor_user.get("experience", "Senior Engineer"),
            "rating_summary": rating_summary,
            "reviews": reviews_page,
            "page": page,
            "has_next_page": has_next_page,
            "has_prev_page": page > 1,
            "next_page": page + 1,
            "prev_page": max(1, page - 1),
            "available_slots": available_slots,
            "report_form": ReviewReportForm(),
        },
    )


@user_required
@require_http_methods(["GET", "POST"])
def student_book_slot(request, mentor_id: str, availability_id: str):
    """
    Booking request form for students to reserve an available coaching slot.
    Supports pre-filled learning goals from the AI Recommendation flow while enforcing
    explicit student confirmation and backend conflict re-validation.
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
        messages.error(
            request,
            "This slot was just reserved or is no longer available. Please choose another open time slot.",
        )
        return redirect("users:mentor_public_detail", mentor_id=mentor_id)

    # Prevent mentors from booking their own slots
    if str(request.mongo_user["_id"]) == str(mid):
        messages.error(request, "You cannot book a mentorship session with yourself.")
        return redirect("users:mentor_public_detail", mentor_id=mentor_id)

    slot_display = format_slot_for_display(slot)
    from_recommend = request.GET.get("from_recommend") == "1" or request.POST.get("from_recommend") == "1"
    prefill_goal = (request.GET.get("goal") or "").strip()
    match_reason = (request.GET.get("reason") or "").strip()

    initial_data: dict[str, str] = {}
    if request.method == "GET" and prefill_goal:
        initial_data["learning_objective"] = prefill_goal[:180]
        if len(prefill_goal) >= 15:
            initial_data["problem_description"] = prefill_goal[:900]

    form = BookingRequestForm(request.POST or None, initial=initial_data)

    if request.method == "POST" and form.is_valid():
        try:
            booking_id = bookings.create_booking_request(
                student_user=request.mongo_user,
                mentor_id=mid,
                availability_id=aid,
                learning_objective=form.cleaned_data["learning_objective"],
                problem_description=form.cleaned_data["problem_description"],
            )
            if from_recommend:
                recommendations.log_recommendation_event(
                    student_user=request.mongo_user,
                    event_type="booking_confirmed",
                    criteria={"topic": slot_display.get("topic_label", "")},
                    mentor_id=mid,
                    availability_id=aid,
                    booking_id=booking_id,
                )
            mentor_name = f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Mentor"
            messages.success(
                request,
                f"Your mentorship request for {slot_display['date']} has been sent to {mentor_name}! Status: Pending Mentor Approval.",
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
            "from_recommend": from_recommend,
            "match_reason": match_reason,
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
    Submit a multi-dimensional verified review for a completed mentorship session.
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
                helpfulness_rating=form.cleaned_data.get("helpfulness_rating") or form.cleaned_data["rating"],
                clarity_rating=form.cleaned_data.get("clarity_rating") or form.cleaned_data["rating"],
                communication_rating=form.cleaned_data.get("communication_rating") or form.cleaned_data["rating"],
                learning_outcome=form.cleaned_data.get("learning_outcome") or "achieved_goal",
                strength_tags=form.cleaned_data.get("strength_tags") or [],
                matched_goal=form.cleaned_data.get("matched_goal", True),
                review_text=form.cleaned_data.get("review_text", ""),
                allow_empty_text=True,
            )
            messages.success(request, "Thank you! Your verified session evaluation has been published.")
            return redirect("users:profile")
        except (ReviewValidationError, ReviewDuplicateError, ReviewPermissionError, ReviewError) as error:
            messages.error(request, str(error))

    return render(
        request,
        "frontoffice/submit_review.html",
        {
            "booking": booking,
            "form": form,
            "is_edit": False,
        },
    )


@user_required
@require_http_methods(["GET", "POST"])
def student_edit_review_view(request, booking_id: str):
    """
    Allow a student to legitimately update their submitted review within the edit window,
    preserving an audit history of changes.
    """
    booking = get_booking_by_id(booking_id)
    if not booking:
        raise Http404("Booking not found.")

    if str(booking.get("student_id")) != str(request.mongo_user["_id"]):
        return HttpResponseForbidden("You can only edit your own session reviews.")

    existing_review = get_review_by_booking(booking_id)
    if not existing_review:
        return redirect("users:student_submit_review", booking_id=booking_id)

    if not existing_review.get("can_edit", False):
        messages.error(request, "This review can no longer be edited.")
        return redirect("users:profile")

    initial = {
        "rating": existing_review.get("rating", 5),
        "helpfulness_rating": existing_review.get("helpfulness_rating", 5),
        "clarity_rating": existing_review.get("clarity_rating", 5),
        "communication_rating": existing_review.get("communication_rating", 5),
        "learning_outcome": existing_review.get("learning_outcome", "achieved_goal"),
        "strength_tags": existing_review.get("strength_tags", []),
        "matched_goal": existing_review.get("matched_goal", True),
        "review_text": existing_review.get("review_text", ""),
    }

    form = ReviewSubmissionForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            reviews.update_review(
                booking_id=booking_id,
                student_user=request.mongo_user,
                rating=form.cleaned_data["rating"],
                helpfulness_rating=form.cleaned_data.get("helpfulness_rating") or form.cleaned_data["rating"],
                clarity_rating=form.cleaned_data.get("clarity_rating") or form.cleaned_data["rating"],
                communication_rating=form.cleaned_data.get("communication_rating") or form.cleaned_data["rating"],
                learning_outcome=form.cleaned_data.get("learning_outcome") or "achieved_goal",
                strength_tags=form.cleaned_data.get("strength_tags") or [],
                matched_goal=form.cleaned_data.get("matched_goal", True),
                review_text=form.cleaned_data.get("review_text", ""),
                allow_empty_text=True,
            )
            messages.success(request, "Your review has been updated.")
            return redirect("users:profile")
        except (ReviewValidationError, ReviewPermissionError, ReviewError) as error:
            messages.error(request, str(error))

    return render(
        request,
        "frontoffice/submit_review.html",
        {
            "booking": booking,
            "form": form,
            "is_edit": True,
            "existing_review": existing_review,
        },
    )


@user_required
@require_POST
def report_review_view(request, review_id: str):
    """
    Allow an authenticated user to flag/report an abusive, spam, or privacy-violating review.
    """
    form = ReviewReportForm(request.POST)
    if form.is_valid():
        try:
            reviews.report_review(
                review_id=review_id,
                reporter_user=request.mongo_user,
                reason=form.cleaned_data["reason"],
                details=form.cleaned_data.get("details", ""),
            )
            messages.info(request, "Thank you. The review has been flagged for moderation.")
        except Exception as error:
            messages.error(request, str(error))
    else:
        messages.error(request, "Please select a valid reason to report the review.")
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/users/mentors/"
    return redirect(next_url)


@roles_required("admin")
@require_POST
def admin_moderate_review_view(request, review_id: str):
    """
    Allow an administrator to hide or restore a review.
    """
    action = request.POST.get("action", "hide").strip()
    try:
        reviews.moderate_review(
            review_id=review_id,
            admin_user=request.mongo_user,
            action=action,
        )
        messages.success(request, f"Review moderation status updated ({action}).")
    except Exception as error:
        messages.error(request, str(error))
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/users/mentors/"
    return redirect(next_url)


@roles_required("mentor")
@require_POST
def mentor_reply_review_view(request, review_id: str):
    """
    Allow a mentor to post a professional response to a student review on their session.
    """
    form = MentorReviewResponseForm(request.POST)
    if form.is_valid():
        try:
            reviews.add_mentor_response(
                review_id=review_id,
                mentor_user=request.mongo_user,
                response_text=form.cleaned_data["response_text"],
            )
            messages.success(request, "Your response has been posted to the review.")
        except Exception as error:
            messages.error(request, str(error))
    else:
        messages.error(request, "Please provide a valid response between 5 and 600 characters.")
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/users/backoffice/mentors/sessions/"
    return redirect(next_url)


@roles_required("mentor")
@require_POST
def mentor_update_profile_settings_view(request):
    """
    Allow a mentor to update their mentoring biography, topics, experience headline,
    teaching languages, and mentoring style in MongoDB.
    """
    form = MentorProfileSettingsForm(request.POST)
    if form.is_valid():
        mid = request.mongo_user["_id"]
        topics_list = [
            t.strip()
            for t in (form.cleaned_data.get("topics") or "").split(",")
            if t.strip()
        ]
        langs_list = [
            l.strip()
            for l in (form.cleaned_data.get("languages") or "").split(",")
            if l.strip()
        ]
        update_doc: dict[str, Any] = {
            "experience": (form.cleaned_data.get("experience") or "").strip(),
            "bio": (form.cleaned_data.get("bio") or "").strip(),
            "teaching_style": form.cleaned_data.get("teaching_style") or "beginner_friendly",
            "updated_at": datetime.now(timezone.utc),
        }
        if topics_list:
            update_doc["topics"] = topics_list[:12]
        if langs_list:
            update_doc["languages"] = langs_list[:6]

        try:
            db["users"].update_one({"_id": mid}, {"$set": update_doc})
            messages.success(request, "Mentoring profile & expertise settings updated.")
        except Exception:
            messages.error(request, "Could not update mentoring profile settings.")
    else:
        messages.error(request, "Please check your profile settings fields.")
    return redirect("users:profile")


# ---------------------------------------------------------------------------
# Mentor Session Management Dashboard (Phase 6)
# ---------------------------------------------------------------------------

@roles_required("mentor")
def mentor_sessions_list(request):
    """
    Mentor session management dashboard with tabs for pending requests,
    upcoming confirmed sessions, past completed sessions, cancellations, and feedback analytics.
    """
    mid = request.mongo_user["_id"]
    stats = get_mentor_session_stats(mid)

    pending_list = (
        list_mentor_bookings(mid, status=STATUS_PENDING)
        + list_mentor_bookings(mid, status=STATUS_RESCHEDULED_PENDING)
    )
    upcoming_list = list_mentor_bookings(mid, status=STATUS_ACCEPTED)
    completed_list = list_mentor_bookings(mid, status=STATUS_COMPLETED)
    for comp in completed_list:
        comp["review"] = get_review_by_booking(comp["_id"])

    cancelled_list = (
        list_mentor_bookings(mid, status=STATUS_CANCELLED)
        + list_mentor_bookings(mid, status=STATUS_REJECTED)
        + list_mentor_bookings(mid, status=STATUS_NO_SHOW)
    )

    rating_summary = get_mentor_rating_summary(mid)
    mentor_reviews = list_mentor_reviews(mid, limit=20)

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
            "subtitle": "Manage incoming booking requests, coaching sessions, and student evaluations.",
            "stats": summary_stats,
            "session_stats": stats,
            "pending_sessions": pending_list,
            "upcoming_sessions": upcoming_list,
            "completed_sessions": completed_list,
            "cancelled_sessions": cancelled_list,
            "rating_summary": rating_summary,
            "mentor_reviews": mentor_reviews,
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
        updated_booking = bookings.get_booking_by_id(booking_id)
        if updated_booking and updated_booking.get("invitation_status") == "failed":
            messages.warning(
                request,
                "Session confirmed and classroom prepared! However, email delivery to the student failed. You can click 'Resend Invitation' on the session card.",
            )
        else:
            messages.success(request, "Session confirmed successfully! The student has been notified.")
    except Exception as error:
        messages.error(request, str(error))
    return redirect("users:mentor_sessions_list")


@roles_required("mentor")
@require_POST
def mentor_resend_invitation_view(request, booking_id: str):
    """
    Resend the online classroom invitation email to the student.
    Supports both standard form POSTs (with redirect) and AJAX/JSON requests from the live classroom.
    """
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
        or (request.content_type or "").startswith("application/json")
    )
    try:
        result = classroom.send_classroom_invitation_email(
            booking_id=booking_id,
            actor_user=request.mongo_user,
            force_resend=True,
        )
        sent_ok = bool(result.get("sent"))
        msg = result.get(
            "message",
            "Classroom invitation email resent successfully."
            if sent_ok
            else "Could not deliver classroom invitation email.",
        )
        if wants_json:
            return JsonResponse(
                {
                    "ok": sent_ok,
                    "sent": sent_ok,
                    "invitation_status": result.get("invitation_status", ""),
                    "message": msg,
                },
                status=200 if sent_ok else 502,
            )
        if sent_ok:
            messages.success(request, msg)
        else:
            messages.warning(request, msg)
    except classroom.ClassroomPermissionError as error:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(error)}, status=403)
        messages.error(request, str(error))
    except classroom.ClassroomNotFoundError as error:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(error)}, status=404)
        messages.error(request, str(error))
    except Exception as error:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(error)}, status=400)
        messages.error(request, str(error))
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/users/backoffice/mentors/sessions/"
    return redirect(next_url)


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



# ---------------------------------------------------------------------------
# Online Classroom & LiveKit Integration Views (Phase 8)
# ---------------------------------------------------------------------------

def _parse_json_body(request) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@user_required
def classroom_view(request, booking_id: str):
    """
    Protected SkillForge Virtual Classroom page for an online mentorship session.
    Requires authentication and participant authorization (mentor or student).
    Never exposes LiveKit API secrets or permanent tokens in the template.
    """
    try:
        meta = classroom.get_classroom_metadata(
            booking_id=booking_id,
            user=request.mongo_user,
        )
    except classroom.ClassroomNotFoundError:
        raise Http404("Mentorship session not found.")
    except classroom.ClassroomPermissionError as error:
        return HttpResponseForbidden(str(error))

    return render(
        request,
        "frontoffice/classroom.html",
        {
            "user": request.mongo_user,
            "classroom_meta": meta,
            "classroom_meta_json": json.dumps(meta),
            "booking_id": str(booking_id),
        },
    )


@user_required
@require_GET
def classroom_metadata_api(request, booking_id: str):
    """
    Return real-time classroom metadata, schedule state, attendance, and chat history.
    """
    try:
        meta = classroom.get_classroom_metadata(
            booking_id=booking_id,
            user=request.mongo_user,
        )
        return JsonResponse({"ok": True, "classroom": meta})
    except classroom.ClassroomNotFoundError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=404)
    except classroom.ClassroomPermissionError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=403)
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)


@user_required
@require_POST
def classroom_token_api(request, booking_id: str):
    """
    Generate a short-lived, role-scoped LiveKit JWT access token for an authorized participant.
    Supports optional mentor-triggered early room unlock (`unlock_early: true`).
    """
    payload = _parse_json_body(request)
    unlock_early = bool(payload.get("unlock_early") or request.POST.get("unlock_early") == "1")

    try:
        if unlock_early:
            classroom.mentor_unlock_classroom_early(
                booking_id=booking_id,
                user=request.mongo_user,
            )
        token_data = classroom.generate_classroom_token(
            booking_id=booking_id,
            user=request.mongo_user,
        )
        return JsonResponse({"ok": True, **token_data})
    except classroom.ClassroomNotFoundError as error:
        return JsonResponse({"ok": False, "error": str(error), "code": "not_found"}, status=404)
    except classroom.ClassroomPermissionError as error:
        return JsonResponse({"ok": False, "error": str(error), "code": "forbidden"}, status=403)
    except classroom.ClassroomConfigError as error:
        return JsonResponse({"ok": False, "error": str(error), "code": "config_error"}, status=503)
    except classroom.ClassroomScheduleError as error:
        return JsonResponse(
            {
                "ok": False,
                "error": error.message,
                "code": error.reason_code,
                **error.extra,
            },
            status=409,
        )
    except classroom.ClassroomStateError as error:
        return JsonResponse({"ok": False, "error": str(error), "code": "invalid_state"}, status=409)
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error), "code": "server_error"}, status=500)


@user_required
@require_POST
def classroom_attendance_api(request, booking_id: str):
    """
    Record verified participant attendance & connection lifecycle events (`join`, `leave`, `reconnect`, `heartbeat`).
    """
    payload = _parse_json_body(request)
    event_type = str(payload.get("event_type") or request.POST.get("event_type") or "").strip().lower()
    reason = str(payload.get("reason") or request.POST.get("reason") or "").strip()

    try:
        summary = classroom.record_attendance_event(
            booking_id=booking_id,
            user=request.mongo_user,
            event_type=event_type,
            source="client_api",
            metadata={"reason": reason} if reason else {},
        )
        return JsonResponse({"ok": True, "attendance": summary})
    except classroom.ClassroomNotFoundError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=404)
    except classroom.ClassroomPermissionError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=403)
    except classroom.ClassroomError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)


@user_required
@require_http_methods(["GET", "POST"])
def classroom_chat_api(request, booking_id: str):
    """
    List or post sanitized plain-text in-session chat messages for a classroom.
    """
    try:
        if request.method == "GET":
            messages_list = classroom.list_chat_messages(
                booking_id=booking_id,
                user=request.mongo_user,
            )
            return JsonResponse({"ok": True, "messages": messages_list})

        payload = _parse_json_body(request)
        text = str(payload.get("message") or request.POST.get("message") or "")
        msg = classroom.post_chat_message(
            booking_id=booking_id,
            user=request.mongo_user,
            message_text=text,
        )
        return JsonResponse({"ok": True, "message": msg})
    except classroom.ClassroomNotFoundError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=404)
    except classroom.ClassroomPermissionError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=403)
    except classroom.ClassroomError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)


@user_required
@require_POST
def classroom_moderate_api(request, booking_id: str):
    """
    Record and authorize mentor moderation actions (`mute_student_request`, `lower_hand`, `unlock_early`, `end_session`).
    """
    payload = _parse_json_body(request)
    action = str(payload.get("action") or request.POST.get("action") or "").strip().lower()
    target_identity = str(payload.get("target_identity") or request.POST.get("target_identity") or "").strip()

    try:
        if action == "unlock_early":
            result = classroom.mentor_unlock_classroom_early(
                booking_id=booking_id,
                user=request.mongo_user,
            )
            return JsonResponse({"ok": True, **result})

        result = classroom.record_moderation_action(
            booking_id=booking_id,
            user=request.mongo_user,
            action=action,
            target_identity=target_identity,
        )
        return JsonResponse({"ok": True, **result})
    except classroom.ClassroomNotFoundError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=404)
    except classroom.ClassroomPermissionError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=403)
    except classroom.ClassroomError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)


@user_required
@require_POST
def classroom_complete_api(request, booking_id: str):
    """
    Allow the assigned mentor to complete and end the session directly from the classroom.
    """
    try:
        bid = classroom.parse_object_id(booking_id)
        booking = bookings.bookings_collection.find_one({"_id": bid})
        if not booking:
            return JsonResponse(
                {"ok": False, "error": "Mentorship session not found."},
                status=404,
            )
        role, _ = classroom.authorize_booking_participant(booking, request.mongo_user)
        if role != "mentor":
            return JsonResponse(
                {"ok": False, "error": "Only the assigned mentor can mark the session as completed."},
                status=403,
            )
        try:
            classroom.record_attendance_event(
                booking_id=booking_id,
                user=request.mongo_user,
                event_type="leave",
                source="session_complete",
            )
        except Exception:
            pass

        bookings.mentor_complete_session(
            booking_id=booking_id,
            mentor_user=request.mongo_user,
        )
        return JsonResponse(
            {
                "ok": True,
                "status": STATUS_COMPLETED,
                "classroom_status": classroom.CLASSROOM_STATUS_COMPLETED,
                "message": "Session completed and closed for both participants.",
                "redirect_url": "/users/backoffice/mentors/sessions/",
                "student_redirect_url": f"/users/mentors/reviews/{booking_id}/add/",
            }
        )
    except (classroom.ClassroomPermissionError, bookings.BookingPermissionError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=403)
    except (classroom.ClassroomNotFoundError, bookings.BookingNotFoundError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=404)
    except (classroom.ClassroomStateError, bookings.BookingValidationError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)


@user_required
@require_POST
def classroom_ai_assist_api(request, booking_id: str):
    """
    Optional Groq-powered in-classroom AI Study Assistant.
    Isolated so any AI failure never impacts the live classroom session.
    """
    payload = _parse_json_body(request)
    prompt_type = str(payload.get("prompt_type") or request.POST.get("prompt_type") or "explain_concept").strip()
    user_input = str(payload.get("user_input") or request.POST.get("user_input") or "").strip()

    try:
        result = classroom.ask_classroom_ai_assistant(
            booking_id=booking_id,
            user=request.mongo_user,
            prompt_type=prompt_type,
            user_input=user_input,
        )
        status_code = 200 if result.get("ok") else 400
        return JsonResponse(result, status=status_code)
    except classroom.ClassroomPermissionError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=403)
    except classroom.ClassroomNotFoundError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=404)
    except Exception:
        return JsonResponse(
            {"ok": False, "error": "AI study assistant is temporarily unavailable."},
            status=200,
        )


@csrf_exempt
@require_POST
def livekit_webhook_view(request):
    """
    Cryptographically verified LiveKit webhook endpoint (`POST /users/api/mentorship/livekit/webhook/`).
    Rejects unsigned or invalid webhook payloads with 401.
    """
    auth_header = (
        request.headers.get("Authorization")
        or request.headers.get("Authorize")
        or request.META.get("HTTP_AUTHORIZATION")
        or ""
    )
    raw_body = request.body.decode("utf-8", errors="replace")
    try:
        result = classroom.process_livekit_webhook(
            raw_body=raw_body,
            auth_header=auth_header,
        )
        return JsonResponse(result, status=200)
    except classroom.ClassroomPermissionError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=401)
    except classroom.ClassroomConfigError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=503)
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
