from __future__ import annotations

from datetime import date
from typing import Any

from django import forms

from .services import (
    MAX_CUSTOM_TOPIC_LENGTH,
    MAX_LOCATION_LENGTH,
    MIN_DURATION_MINUTES,
    PREDEFINED_TOPICS,
    SESSION_TYPE_CHOICES,
    TOPIC_CHOICES,
    calculate_duration_minutes,
    current_local_date,
    current_local_datetime,
    get_max_duration_minutes,
)


class AvailabilitySlotForm(forms.Form):
    date = forms.DateField(
        label="Date",
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={
                "type": "date",
                "class": "availability-input",
                "required": "required",
            },
        ),
        error_messages={
            "required": "Choose a date so students know when you're available.",
            "invalid": "Choose a date so students know when you're available.",
        },
    )
    start_time = forms.TimeField(
        label="Start time",
        input_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.TimeInput(
            format="%H:%M",
            attrs={
                "type": "time",
                "class": "availability-input",
                "required": "required",
            },
        ),
        error_messages={
            "required": "When does your availability begin?",
            "invalid": "When does your availability begin?",
        },
    )
    end_time = forms.TimeField(
        label="End time",
        input_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.TimeInput(
            format="%H:%M",
            attrs={
                "type": "time",
                "class": "availability-input",
                "required": "required",
            },
        ),
        error_messages={
            "required": "Your end time must be later than your start time.",
            "invalid": "Your end time must be later than your start time.",
        },
    )
    topic = forms.ChoiceField(
        label="Mentoring topic",
        choices=(("", "Select a mentoring topic..."), *TOPIC_CHOICES),
        widget=forms.Select(
            attrs={
                "class": "availability-input",
                "required": "required",
            }
        ),
        error_messages={
            "required": "Choose a mentoring topic to help students find the right session.",
            "invalid_choice": "Choose a mentoring topic to help students find the right session.",
        },
    )
    custom_topic = forms.CharField(
        label="Custom topic",
        required=False,
        max_length=MAX_CUSTOM_TOPIC_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "availability-input",
                "placeholder": "e.g., System Design, Cloud Architecture, GraphQL...",
                "maxlength": str(MAX_CUSTOM_TOPIC_LENGTH),
            }
        ),
        error_messages={
            "max_length": f"Custom topic must be {MAX_CUSTOM_TOPIC_LENGTH} characters or fewer.",
        },
    )
    session_type = forms.ChoiceField(
        label="Session format",
        choices=SESSION_TYPE_CHOICES,
        initial="online",
        widget=forms.RadioSelect(attrs={"class": "session-type-radio"}),
        error_messages={
            "required": "Please select whether this session is Online or In-person.",
            "invalid_choice": "Please select whether this session is Online or In-person.",
        },
    )
    location = forms.CharField(
        label="Location / Venue (optional)",
        required=False,
        max_length=MAX_LOCATION_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "availability-input",
                "placeholder": "e.g., Campus Room B204, Coworking Hub, or City Center",
                "maxlength": str(MAX_LOCATION_LENGTH),
            }
        ),
        error_messages={
            "max_length": f"Location must be {MAX_LOCATION_LENGTH} characters or fewer.",
        },
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.initial_slot: dict[str, Any] | None = kwargs.pop("slot", None)
        self.reference_date = kwargs.pop("reference_date", None)
        self.reference_datetime = kwargs.pop("reference_datetime", None)
        super().__init__(*args, **kwargs)

        now_local = self.reference_datetime or current_local_datetime()
        min_date_iso = (self.reference_date or now_local.date()).isoformat()
        self.current_time_hhmm = now_local.strftime("%H:%M")
        self.fields["date"].widget.attrs["min"] = min_date_iso
        self.fields["date"].widget.attrs["data-today"] = min_date_iso
        self.fields["start_time"].widget.attrs["data-current-time"] = self.current_time_hhmm

        if self.initial_slot and not self.is_bound and "initial" not in kwargs:
            self.initial.update(
                {
                    "date": self.initial_slot.get("date", ""),
                    "start_time": self.initial_slot.get("start_time", ""),
                    "end_time": self.initial_slot.get("end_time", ""),
                    "topic": self.initial_slot.get("topic", ""),
                    "custom_topic": self.initial_slot.get("custom_topic", ""),
                    "session_type": self.initial_slot.get("session_type", "online"),
                    "location": self.initial_slot.get("location", ""),
                }
            )

    def clean_date(self):
        slot_date = self.cleaned_data["date"]
        now_local = self.reference_datetime or current_local_datetime()
        today = self.reference_date or now_local.date()
        if slot_date < today:
            raise forms.ValidationError(
                "That date has already passed. Please select today or a future date."
            )
        return slot_date

    def clean_topic(self) -> str:
        topic = (self.cleaned_data.get("topic") or "").strip()
        if topic not in PREDEFINED_TOPICS:
            raise forms.ValidationError(
                "Choose a mentoring topic to help students find the right session."
            )
        return topic

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        slot_date = cleaned_data.get("date")
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")

        now_local = self.reference_datetime or current_local_datetime()
        today = self.reference_date or now_local.date()
        if (
            slot_date
            and start_time
            and slot_date == today
            and (self.reference_date is None or self.reference_datetime is not None)
        ):
            current_hm = now_local.time().replace(second=0, microsecond=0)
            if start_time.replace(second=0, microsecond=0) <= current_hm:
                self.add_error(
                    "start_time",
                    "This start time has already passed for today. Please choose a future start time.",
                )

        if start_time and end_time:
            duration_minutes = calculate_duration_minutes(start_time, end_time)
            if duration_minutes <= 0:
                self.add_error(
                    "end_time",
                    "Your end time must be later than your start time.",
                )
            elif duration_minutes < MIN_DURATION_MINUTES:
                self.add_error(
                    "end_time",
                    f"Choose a session lasting at least {MIN_DURATION_MINUTES} minutes.",
                )
            else:
                max_duration = get_max_duration_minutes()
                if duration_minutes > max_duration:
                    max_hours = max_duration // 60
                    self.add_error(
                        "end_time",
                        f"Sessions cannot exceed {max_hours} hours ({max_duration} minutes).",
                    )
                else:
                    cleaned_data["duration_minutes"] = duration_minutes

        topic = cleaned_data.get("topic")
        custom_topic = (cleaned_data.get("custom_topic") or "").strip()
        if topic == "Other":
            if not custom_topic:
                self.add_error(
                    "custom_topic",
                    "Tell us which topic you want to mentor.",
                )
            elif len(custom_topic) > MAX_CUSTOM_TOPIC_LENGTH:
                self.add_error(
                    "custom_topic",
                    f"Custom topic must be {MAX_CUSTOM_TOPIC_LENGTH} characters or fewer.",
                )
            else:
                cleaned_data["custom_topic"] = custom_topic
        else:
            cleaned_data["custom_topic"] = ""

        session_type = cleaned_data.get("session_type")
        location = (cleaned_data.get("location") or "").strip()
        if session_type == "in_person":
            cleaned_data["location"] = location
        else:
            cleaned_data["location"] = ""

        return cleaned_data

    def to_document(self) -> dict[str, Any]:
        return {
            "date": self.cleaned_data["date"].isoformat(),
            "start_time": self.cleaned_data["start_time"].strftime("%H:%M"),
            "end_time": self.cleaned_data["end_time"].strftime("%H:%M"),
            "duration_minutes": self.cleaned_data["duration_minutes"],
            "topic": self.cleaned_data["topic"],
            "custom_topic": self.cleaned_data.get("custom_topic", ""),
            "session_type": self.cleaned_data["session_type"],
            "location": self.cleaned_data.get("location", ""),
        }


class AvailabilityFilterForm(forms.Form):
    date = forms.DateField(
        label="Date",
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={"type": "date", "class": "filter-input"},
        ),
    )
    topic = forms.ChoiceField(
        label="Topic",
        required=False,
        choices=(("", "All topics"), *TOPIC_CHOICES),
        widget=forms.Select(attrs={"class": "filter-input"}),
    )
    session_type = forms.ChoiceField(
        label="Format",
        required=False,
        choices=(("", "All formats"), *SESSION_TYPE_CHOICES),
        widget=forms.Select(attrs={"class": "filter-input"}),
    )



class BookingRequestForm(forms.Form):
    learning_objective = forms.CharField(
        label="Learning Objective",
        min_length=10,
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "class": "availability-input",
                "rows": 3,
                "placeholder": "What would you like to achieve during this session?",
                "data-char-counter": "objective-counter",
                "maxlength": "500",
            }
        ),
        help_text="Between 10 and 500 characters.",
    )
    problem_description = forms.CharField(
        label="Question or Problem Description",
        min_length=10,
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                "class": "availability-input",
                "rows": 4,
                "placeholder": "Describe the challenge or specific topic you'd like your mentor to help you solve.",
                "data-char-counter": "problem-counter",
                "maxlength": "1000",
            }
        ),
        help_text="Between 10 and 1000 characters.",
    )


class RescheduleProposalForm(forms.Form):
    proposed_date = forms.DateField(
        label="Proposed Date",
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={"type": "date", "class": "availability-input", "required": "required"},
        ),
        error_messages={
            "required": "Please provide a proposed date.",
            "invalid": "Please select a valid date in YYYY-MM-DD format.",
        },
    )
    proposed_start_time = forms.TimeField(
        label="Proposed Start Time",
        input_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.TimeInput(
            format="%H:%M",
            attrs={"type": "time", "class": "availability-input", "required": "required"},
        ),
        error_messages={
            "required": "Please specify the start time.",
            "invalid": "Please specify a valid start time.",
        },
    )
    proposed_end_time = forms.TimeField(
        label="Proposed End Time",
        input_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.TimeInput(
            format="%H:%M",
            attrs={"type": "time", "class": "availability-input", "required": "required"},
        ),
        error_messages={
            "required": "Please specify the end time.",
            "invalid": "Please specify a valid end time.",
        },
    )
    reschedule_note = forms.CharField(
        label="Message to Student (Optional)",
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "class": "availability-input",
                "rows": 2,
                "placeholder": "Explain why you are proposing a different time...",
            }
        ),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.min_date = kwargs.pop("min_date", None)
        super().__init__(*args, **kwargs)

    def clean_proposed_date(self):
        proposed_date = self.cleaned_data.get("proposed_date")
        if not proposed_date:
            return proposed_date
        today = current_local_date()
        if proposed_date < today:
            raise forms.ValidationError(
                "Proposed date cannot be in the past. Please select today or a future date."
            )
        if self.min_date:
            min_d = None
            try:
                min_d = (
                    self.min_date
                    if isinstance(self.min_date, date)
                    else date.fromisoformat(str(self.min_date))
                )
            except (ValueError, TypeError):
                min_d = None
            if min_d and proposed_date < min_d:
                raise forms.ValidationError(
                    f"Proposed date cannot be before the original scheduled date ({min_d.isoformat()})."
                )
        return proposed_date

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        proposed_date = cleaned_data.get("proposed_date")
        start_time = cleaned_data.get("proposed_start_time")
        end_time = cleaned_data.get("proposed_end_time")

        if proposed_date and start_time:
            now_local = current_local_datetime()
            if proposed_date == now_local.date() and start_time <= now_local.time():
                self.add_error(
                    "proposed_start_time",
                    "Proposed start time has already passed for today. Please select a future time.",
                )

        if start_time and end_time:
            duration_minutes = calculate_duration_minutes(start_time, end_time)
            if duration_minutes <= 0:
                self.add_error(
                    "proposed_end_time",
                    "Proposed end time must be later than proposed start time.",
                )
            elif duration_minutes < MIN_DURATION_MINUTES:
                self.add_error(
                    "proposed_end_time",
                    f"Choose a session lasting at least {MIN_DURATION_MINUTES} minutes.",
                )
            else:
                max_duration = get_max_duration_minutes()
                if duration_minutes > max_duration:
                    max_hours = max_duration // 60
                    self.add_error(
                        "proposed_end_time",
                        f"Sessions cannot exceed {max_hours} hours ({max_duration} minutes).",
                    )
                else:
                    cleaned_data["duration_minutes"] = duration_minutes

        return cleaned_data


class MeetingLinkForm(forms.Form):
    meeting_link = forms.URLField(
        label="Meeting URL",
        widget=forms.URLInput(
            attrs={
                "class": "availability-input",
                "placeholder": "https://meet.google.com/xyz-abcd-efg or Zoom URL",
            }
        ),
        help_text="Private to the mentor and student for online sessions.",
    )


STAR_RATING_CHOICES = [
    (5, "5 Stars — Outstanding guidance"),
    (4, "4 Stars — Very helpful session"),
    (3, "3 Stars — Good coaching"),
    (2, "2 Stars — Needs improvement"),
    (1, "1 Star — Did not meet expectations"),
]

LEARNING_OUTCOME_FORM_CHOICES = [
    ("achieved_goal", "Achieved my learning goal"),
    ("made_progress", "Made solid progress"),
    ("needs_more_help", "Still need follow-up guidance"),
]

STRENGTH_TAG_FORM_CHOICES = [
    ("Clear Explanations", "Clear Explanations"),
    ("Actionable Code Examples", "Actionable Code Examples"),
    ("Patient & Encouraging", "Patient & Encouraging"),
    ("Deep Technical Mastery", "Deep Technical Mastery"),
    ("Great Debugging Partner", "Great Debugging Partner"),
    ("Well-Prepared & Structured", "Well-Prepared & Structured"),
]


class ReviewSubmissionForm(forms.Form):
    rating = forms.TypedChoiceField(
        label="Overall Experience",
        choices=STAR_RATING_CHOICES,
        coerce=int,
        initial=5,
        widget=forms.Select(attrs={"class": "availability-input", "id": "id_rating"}),
    )
    helpfulness_rating = forms.TypedChoiceField(
        label="Helpfulness (Actionable Guidance)",
        choices=STAR_RATING_CHOICES,
        coerce=int,
        required=False,
        initial=5,
        widget=forms.Select(attrs={"class": "availability-input", "id": "id_helpfulness_rating"}),
    )
    clarity_rating = forms.TypedChoiceField(
        label="Clarity of Explanations",
        choices=STAR_RATING_CHOICES,
        coerce=int,
        required=False,
        initial=5,
        widget=forms.Select(attrs={"class": "availability-input", "id": "id_clarity_rating"}),
    )
    communication_rating = forms.TypedChoiceField(
        label="Listening & Communication",
        choices=STAR_RATING_CHOICES,
        coerce=int,
        required=False,
        initial=5,
        widget=forms.Select(attrs={"class": "availability-input", "id": "id_communication_rating"}),
    )
    learning_outcome = forms.ChoiceField(
        label="Learning Outcome",
        choices=LEARNING_OUTCOME_FORM_CHOICES,
        required=False,
        initial="achieved_goal",
        widget=forms.RadioSelect(attrs={"class": "outcome-radio"}),
    )
    strength_tags = forms.MultipleChoiceField(
        label="What stood out? (Optional)",
        choices=STRENGTH_TAG_FORM_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "strength-tag-checkbox"}),
    )
    matched_goal = forms.BooleanField(
        label="This mentor was a good match for my learning goal",
        required=False,
        initial=True,
    )
    review_text = forms.CharField(
        label="Written Review (Optional)",
        required=False,
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                "class": "availability-input",
                "rows": 4,
                "placeholder": "Share what was most helpful about the mentor's guidance (optional)...",
                "data-char-counter": "review-counter",
                "maxlength": "1000",
            }
        ),
        help_text="Optional. If provided, please enter 10 to 1000 characters of constructive feedback.",
    )

    def clean_review_text(self) -> str:
        text = (self.cleaned_data.get("review_text") or "").strip()
        if text and len(text) < 10:
            raise forms.ValidationError(
                "If writing a review comment, please provide at least 10 characters (or leave blank to submit ratings only)."
            )
        return text


class MentorReviewResponseForm(forms.Form):
    response_text = forms.CharField(
        label="Your Professional Response",
        min_length=5,
        max_length=600,
        widget=forms.Textarea(
            attrs={
                "class": "availability-input",
                "rows": 3,
                "placeholder": "Thank the student or add a professional follow-up note...",
                "maxlength": "600",
            }
        ),
    )


class ReviewReportForm(forms.Form):
    reason = forms.ChoiceField(
        label="Reason for Reporting",
        choices=[
            ("spam", "Spam or promotional content"),
            ("offensive", "Offensive or disrespectful language"),
            ("privacy", "Exposes private or personal information"),
            ("inaccurate", "Unrelated to the mentoring session"),
        ],
        widget=forms.Select(attrs={"class": "availability-input"}),
    )
    details = forms.CharField(
        label="Additional Context (Optional)",
        required=False,
        max_length=300,
        widget=forms.Textarea(
            attrs={
                "class": "availability-input",
                "rows": 2,
                "placeholder": "Briefly describe the issue...",
                "maxlength": "300",
            }
        ),
    )


class MentorRecommendationForm(forms.Form):
    learning_goal = forms.CharField(
        label="What do you want to learn or solve?",
        required=False,
        max_length=800,
        widget=forms.Textarea(
            attrs={
                "class": "availability-input",
                "rows": 3,
                "placeholder": "e.g., I am a beginner in React and want help understanding hooks and building my first project...",
                "maxlength": "800",
            }
        ),
    )
    topic = forms.ChoiceField(
        label="Topic / Technology",
        required=False,
        choices=(("any", "Any topic (Auto-detect from goal)"), *TOPIC_CHOICES),
        initial="any",
        widget=forms.Select(attrs={"class": "availability-input"}),
    )
    experience_level = forms.ChoiceField(
        label="Your Current Level",
        required=False,
        choices=[
            ("any", "Any level"),
            ("beginner", "Beginner — New to the topic"),
            ("intermediate", "Intermediate — Building projects"),
            ("advanced", "Advanced — Architecture & optimization"),
        ],
        initial="any",
        widget=forms.Select(attrs={"class": "availability-input"}),
    )
    teaching_style = forms.ChoiceField(
        label="Preferred Mentoring Style",
        required=False,
        choices=[
            ("any", "Any teaching style"),
            ("beginner_friendly", "Patient & step-by-step (Beginner-friendly)"),
            ("project_focused", "Hands-on & practical (Project-focused)"),
            ("deep_technical", "Deep-dive architecture & code review"),
            ("career_coaching", "Interview prep & career guidance"),
        ],
        initial="any",
        widget=forms.Select(attrs={"class": "availability-input"}),
    )
    preferred_language = forms.ChoiceField(
        label="Preferred Language",
        required=False,
        choices=[
            ("any", "Any language"),
            ("English", "English"),
            ("French", "French (Français)"),
            ("Arabic", "Arabic (العربية)"),
        ],
        initial="any",
        widget=forms.Select(attrs={"class": "availability-input"}),
    )
    session_type = forms.ChoiceField(
        label="Session Format",
        required=False,
        choices=[
            ("any", "Online or In-person"),
            ("online", "Online (Video Call)"),
            ("in_person", "In-person"),
        ],
        initial="any",
        widget=forms.Select(attrs={"class": "availability-input"}),
    )
    preferred_date = forms.DateField(
        label="Preferred Date (Optional)",
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={"type": "date", "class": "availability-input"},
        ),
    )
    preferred_duration = forms.TypedChoiceField(
        label="Minimum Duration",
        required=False,
        coerce=int,
        empty_value=0,
        choices=[
            (0, "Any duration"),
            (30, "At least 30 minutes"),
            (45, "At least 45 minutes"),
            (60, "At least 60 minutes"),
        ],
        initial=0,
        widget=forms.Select(attrs={"class": "availability-input"}),
    )
    require_available_slot = forms.BooleanField(
        label="Only show mentors with currently open availability slots",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "rec-checkbox"}),
    )


class MentorProfileSettingsForm(forms.Form):
    experience = forms.CharField(
        label="Professional Headline / Experience",
        required=False,
        max_length=120,
        widget=forms.TextInput(
            attrs={
                "class": "availability-input",
                "placeholder": "e.g., Senior Full-Stack Engineer (8 yrs experience)",
            }
        ),
    )
    bio = forms.CharField(
        label="Mentoring Biography & Approach",
        required=False,
        max_length=800,
        widget=forms.Textarea(
            attrs={
                "class": "availability-input",
                "rows": 3,
                "placeholder": "Describe your technical background, projects, and how you help students succeed...",
            }
        ),
    )
    topics = forms.CharField(
        label="Verified Expertise & Skills (comma-separated)",
        required=False,
        max_length=300,
        widget=forms.TextInput(
            attrs={
                "class": "availability-input",
                "placeholder": "e.g., Python, Django, Web Development, React, Databases",
            }
        ),
    )
    languages = forms.CharField(
        label="Teaching Languages (comma-separated)",
        required=False,
        max_length=120,
        widget=forms.TextInput(
            attrs={
                "class": "availability-input",
                "placeholder": "e.g., English, French",
            }
        ),
    )
    teaching_style = forms.ChoiceField(
        label="Primary Mentoring Style",
        required=False,
        choices=[
            ("beginner_friendly", "Patient & step-by-step (Beginner-friendly)"),
            ("project_focused", "Hands-on & practical (Project-focused)"),
            ("deep_technical", "Deep-dive architecture & code review"),
            ("career_coaching", "Interview prep & career guidance"),
        ],
        widget=forms.Select(attrs={"class": "availability-input"}),
    )

