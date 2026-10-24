from django.urls import path

from . import views

app_name = "mentors"

urlpatterns = [
    # Public & student routes
    path("mentors/", views.mentors, name="mentors"),
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
]
