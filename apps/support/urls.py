from django.urls import path
from . import views

app_name = "support"
urlpatterns = [
    path("", views.ticket_list, name="list"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("board/", views.backoffice_board, name="board"),
    path("notifications/", views.notifications, name="notifications"),
    path("faq/", views.faq_list, name="faq"),
    path("faq/new/", views.faq_create, name="faq_create"),
    path("faq/<str:entry_id>/edit/", views.faq_edit, name="faq_edit"),
    path("faq/<str:entry_id>/delete/", views.faq_delete, name="faq_delete"),
    path("new/", views.ticket_create, name="create"),
    path("ia/suggestions/", views.ai_suggestion, name="ai_suggestion"),
    path("<str:ticket_id>/ia/resume/", views.ai_summary, name="ai_summary"),
    path("<str:ticket_id>/ia/reponse/", views.ai_draft, name="ai_draft"),
    path("<str:ticket_id>/delete/", views.ticket_delete, name="delete"),
    path("<str:ticket_id>/notes/<str:note_id>/delete/", views.note_delete, name="note_delete"),
    path("<str:ticket_id>/files/<str:message_id>/", views.attachment, name="attachment"),
    path("<str:ticket_id>/", views.ticket_detail, name="detail"),
]
