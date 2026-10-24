from django.urls import path

from . import views

app_name = "mentors"

urlpatterns = [
    # Public & student routes
    path("mentors/", views.mentors, name="mentors"),
    path("mentors/recommend/", views.mentor_recommendations_view, name="mentor_recommendations"),
    path("mentors/<str:mentor_id>/", views.mentor_public_detail, name="mentor_public_detail"),
    path(
        "mentors/<str:mentor_id>/book/<str:availability_id>/",
        views.student_book_slot,
        name="mentor_book_slot",
    ),
    path(
        "mentorship/bookings/<str:booking_id>/cancel/",
        views.student_cancel_booking_view,
        name="student_cancel_booking",
    ),
    path(
        "mentorship/bookings/<str:booking_id>/reschedule-respond/",
        views.student_reschedule_respond_view,
        name="student_reschedule_respond",
    ),
    path(
        "mentors/reviews/<str:booking_id>/add/",
        views.student_submit_review_view,
        name="student_submit_review",
    ),
    path(
        "mentors/reviews/<str:booking_id>/edit/",
        views.student_edit_review_view,
        name="student_edit_review",
    ),
    path(
        "mentors/reviews/<str:review_id>/report/",
        views.report_review_view,
        name="report_review",
    ),
    path(
        "mentors/reviews/<str:review_id>/moderate/",
        views.admin_moderate_review_view,
        name="moderate_review",
    ),
    path(
        "backoffice/mentors/reviews/<str:review_id>/reply/",
        views.mentor_reply_review_view,
        name="mentor_reply_review",
    ),
    path(
        "backoffice/mentors/profile-settings/",
        views.mentor_update_profile_settings_view,
        name="mentor_update_profile_settings",
    ),

    # Notifications
    path("notifications/", views.notifications_list_view, name="notifications_list"),
    path(
        "notifications/<str:notification_id>/read/",
        views.notification_mark_read_view,
        name="notification_mark_read",
    ),
    path(
        "notifications/read-all/",
        views.notification_mark_all_read_view,
        name="notification_mark_all_read",
    ),
    path(
        "api/notifications/unread-count/",
        views.notification_unread_count_api,
        name="notification_unread_count_api",
    ),

    # Backoffice legacy student view
    path("backoffice/mentors/", views.backoffice_mentors, name="backoffice_mentors"),

    # Mentor availability management (mentor only)
    path(
        "backoffice/mentors/availability/",
        views.manage_availability_list,
        name="mentor_availability_list",
    ),
    path(
        "backoffice/mentors/availability/create/",
        views.manage_availability_create,
        name="mentor_availability_create",
    ),
    path(
        "backoffice/mentors/availability/<str:slot_id>/",
        views.manage_availability_detail,
        name="mentor_availability_detail",
    ),
    path(
        "backoffice/mentors/availability/<str:slot_id>/edit/",
        views.manage_availability_edit,
        name="mentor_availability_edit",
    ),
    path(
        "backoffice/mentors/availability/<str:slot_id>/delete/",
        views.manage_availability_delete,
        name="mentor_availability_delete",
    ),

    # Mentor session management (mentor only)
    path(
        "backoffice/mentors/sessions/",
        views.mentor_sessions_list,
        name="mentor_sessions_list",
    ),
    path(
        "backoffice/mentors/sessions/<str:booking_id>/accept/",
        views.mentor_accept_booking_view,
        name="mentor_accept_booking",
    ),
    path(
        "backoffice/mentors/sessions/<str:booking_id>/reject/",
        views.mentor_reject_booking_view,
        name="mentor_reject_booking",
    ),
    path(
        "backoffice/mentors/sessions/<str:booking_id>/reschedule/",
        views.mentor_propose_reschedule_view,
        name="mentor_propose_reschedule",
    ),
    path(
        "backoffice/mentors/sessions/<str:booking_id>/meeting-link/",
        views.mentor_update_meeting_link_view,
        name="mentor_update_meeting_link",
    ),
    path(
        "backoffice/mentors/sessions/<str:booking_id>/complete/",
        views.mentor_complete_session_view,
        name="mentor_complete_session",
    ),
    path(
        "backoffice/mentors/sessions/<str:booking_id>/no-show/",
        views.mentor_mark_no_show_view,
        name="mentor_mark_no_show",
    ),
    path(
        "backoffice/mentors/sessions/<str:booking_id>/cancel/",
        views.mentor_cancel_booking_view,
        name="mentor_cancel_booking",
    ),
    path(
        "backoffice/mentors/sessions/<str:booking_id>/resend-invitation/",
        views.mentor_resend_invitation_view,
        name="mentor_resend_invitation",
    ),

    # Online Virtual Classroom & LiveKit Endpoints
    path(
        "mentorship/classroom/<str:booking_id>/",
        views.classroom_view,
        name="mentorship_classroom",
    ),
    path(
        "api/mentorship/classroom/<str:booking_id>/metadata/",
        views.classroom_metadata_api,
        name="classroom_metadata_api",
    ),
    path(
        "api/mentorship/classroom/<str:booking_id>/token/",
        views.classroom_token_api,
        name="classroom_token_api",
    ),
    path(
        "api/mentorship/classroom/<str:booking_id>/attendance/",
        views.classroom_attendance_api,
        name="classroom_attendance_api",
    ),
    path(
        "api/mentorship/classroom/<str:booking_id>/chat/",
        views.classroom_chat_api,
        name="classroom_chat_api",
    ),
    path(
        "api/mentorship/classroom/<str:booking_id>/moderate/",
        views.classroom_moderate_api,
        name="classroom_moderate_api",
    ),
    path(
        "api/mentorship/classroom/<str:booking_id>/complete/",
        views.classroom_complete_api,
        name="classroom_complete_api",
    ),
    path(
        "api/mentorship/classroom/<str:booking_id>/ai-assist/",
        views.classroom_ai_assist_api,
        name="classroom_ai_assist_api",
    ),
    path(
        "api/mentorship/livekit/webhook/",
        views.livekit_webhook_view,
        name="livekit_webhook",
    ),
]
