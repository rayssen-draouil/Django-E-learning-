from django import forms

from .services import CATEGORIES, PRIORITIES, STATUSES


ALLOWED_ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".txt"}
MAX_ATTACHMENT_SIZE = 5 * 1024 * 1024


class TicketForm(forms.Form):
    title = forms.CharField(label="Title", max_length=160)
    category = forms.ChoiceField(label="Category", choices=CATEGORIES)
    priority = forms.ChoiceField(label="Priority", choices=PRIORITIES, initial="normal")
    description = forms.CharField(label="Description", max_length=10000, widget=forms.Textarea(attrs={"rows": 6}))


class ReplyForm(forms.Form):
    body = forms.CharField(label="Your message", max_length=5000, widget=forms.Textarea(attrs={"rows": 4}))
    attachment = forms.FileField(
        label="Attachment (optional)",
        required=False,
        widget=forms.FileInput(attrs={"class": "support-file-input", "accept": ".png,.jpg,.jpeg,.webp,.gif,.pdf,.txt"}),
    )

    def clean_attachment(self):
        upload = self.cleaned_data.get("attachment")
        if not upload:
            return upload
        if upload.size > MAX_ATTACHMENT_SIZE:
            raise forms.ValidationError("The file must not exceed 5 MB.")
        import os
        if os.path.splitext(upload.name)[1].lower() not in ALLOWED_ATTACHMENT_EXTENSIONS:
            raise forms.ValidationError("Allowed formats: PNG, JPG, WEBP, GIF, PDF, and TXT.")
        return upload


class AdminTicketForm(forms.Form):
    status = forms.ChoiceField(label="Status", choices=STATUSES)
    priority = forms.ChoiceField(label="Priority", choices=PRIORITIES)
    assignee = forms.ChoiceField(label="Assigned to", required=False)
    note = forms.CharField(label="Internal note (history)", max_length=300, required=False,
                           widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, admins=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].choices = [("", "Unassigned")] + [(str(admin["_id"]), f'{admin.get("first_name", "")} {admin.get("last_name", "")} ({admin["email"]})'.strip()) for admin in admins]


class TicketFilterForm(forms.Form):
    """Back-office filters used to narrow down a ticket queue."""

    q = forms.CharField(label="Search", max_length=160, required=False,
                        widget=forms.TextInput(attrs={"placeholder": "Title or description"}))
    status = forms.ChoiceField(label="Status", required=False, choices=[("", "All statuses")] + list(STATUSES))
    priority = forms.ChoiceField(label="Priority", required=False, choices=[("", "All priorities")] + list(PRIORITIES))
    category = forms.ChoiceField(label="Category", required=False, choices=[("", "All categories")] + list(CATEGORIES))
    assignee = forms.ChoiceField(label="Assigned to", required=False)
    sort = forms.ChoiceField(label="Sort by", required=False,
                             choices=[("recent", "Recent activity"), ("oldest", "Oldest first"), ("priority", "Priority")])

    def __init__(self, *args, user=None, admins=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.is_admin = bool(user and user.get("role") == "admin")
        if self.is_admin:
            self.fields["assignee"].choices = ([("", "All assignees"), ("me", "Assigned to me"), ("unassigned", "Unassigned")]
                                               + [(str(admin["_id"]), f'{admin.get("first_name", "")} {admin.get("last_name", "")}'.strip()) for admin in admins])
        else:
            for field in ("priority", "assignee", "sort"):
                self.fields.pop(field, None)

    def filters(self):
        """Return the cleaned filters usable to build a ticket query."""
        if not self.is_valid():
            return {}
        data = self.cleaned_data
        result = {"q": data.get("q", "").strip()}
        for field in ("status", "priority", "category", "assignee"):
            if data.get(field):
                result[field] = data[field]
        if data.get("sort"):
            result["sort"] = data["sort"]
        return result


class NoteForm(forms.Form):
    """Internal note, never shown to the student."""

    body = forms.CharField(label="Internal note", max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))


class FaqForm(forms.Form):
    """A solution of the knowledge base managed by the support team."""

    question = forms.CharField(label="Question", max_length=200)
    answer = forms.CharField(label="Answer", max_length=4000, widget=forms.Textarea(attrs={"rows": 6}))
    keywords = forms.CharField(label="Keywords", max_length=300, required=False,
                               help_text="Separate with commas, for example: video, playback, course")
    category = forms.ChoiceField(label="Category", choices=CATEGORIES, initial="other")
    is_published = forms.BooleanField(label="Published for students", required=False, initial=True)

    def clean_keywords(self):
        raw = self.cleaned_data.get("keywords", "") or ""
        return [item.strip() for item in raw.split(",") if item.strip()][:12]
