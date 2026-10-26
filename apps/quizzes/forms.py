import json
import math

from bson import ObjectId
from django import forms


QUESTION_TYPES = {"single_choice", "multiple_choice", "true_false"}


class QuizForm(forms.Form):
    title = forms.CharField(label="Title", max_length=160)
    description = forms.CharField(label="Description", max_length=4000, widget=forms.Textarea(attrs={"rows": 3}))
    status = forms.ChoiceField(label="Status", choices=(("draft", "Draft"), ("published", "Published"), ("archived", "Archived")))
    duration_seconds = forms.IntegerField(label="Duration (seconds)", min_value=1, required=False)
    passing_score = forms.IntegerField(label="Passing score (%)", min_value=0, max_value=100, required=False)
    questions_json = forms.CharField(
        label="Questions (JSON)",
        widget=forms.Textarea(attrs={"rows": 18, "class": "quiz-json", "spellcheck": "false"}),
        help_text='Each question must include type, prompt, points, options [{"_id": "optional ObjectId", "text": "..."}], and correct_option_ids. Answers may reference an index (starting at 0) or an option ID.',
    )

    def __init__(self, *args, **kwargs):
        initial_quiz = kwargs.pop("quiz", None)
        super().__init__(*args, **kwargs)
        if initial_quiz and not self.is_bound and "initial" not in kwargs:
            self.initial.update({
                "title": initial_quiz["title"],
                "description": initial_quiz.get("description", ""),
                "status": initial_quiz.get("status", "draft"),
                "duration_seconds": initial_quiz.get("duration_seconds"),
                "passing_score": initial_quiz.get("passing_score"),
                "questions_json": json.dumps([
                    {"_id": str(q["_id"]), "type": q["type"], "prompt": q["prompt"], "points": q["points"],
                     "options": [{"_id": str(o["_id"]), "text": o["text"]} for o in q["options"]],
                     "correct_option_ids": [str(option_id) for option_id in q["correct_option_ids"]]}
                    for q in initial_quiz.get("questions", [])
                ], ensure_ascii=False, indent=2)
            })

    def clean_questions_json(self):
        raw = self.cleaned_data["questions_json"]
        try:
            entries = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise forms.ValidationError("Enter valid JSON.") from error
        if not isinstance(entries, list) or not entries:
            raise forms.ValidationError("Add at least one question.")
        if len(entries) > 100:
            raise forms.ValidationError("A quiz cannot contain more than 100 questions.")
        questions = []
        for position, item in enumerate(entries, start=1):
            if not isinstance(item, dict):
                raise forms.ValidationError(f"Question {position} must be a JSON object.")
            qtype, prompt = item.get("type"), item.get("prompt")
            if not isinstance(qtype, str) or qtype not in QUESTION_TYPES:
                raise forms.ValidationError(f"Invalid type for question {position}.")
            if not isinstance(prompt, str) or not prompt.strip():
                raise forms.ValidationError(f"Question {position} requires a prompt.")
            if len(prompt.strip()) > 2000:
                raise forms.ValidationError(f"Question {position} prompt exceeds 2,000 characters.")
            raw_question_id = item.get("_id")
            if raw_question_id is not None and (not isinstance(raw_question_id, str) or not ObjectId.is_valid(raw_question_id)):
                raise forms.ValidationError(f"Invalid ID for question {position}.")
            question_id = ObjectId(raw_question_id) if raw_question_id else ObjectId()
            points = item.get("points", 1)
            if isinstance(points, bool) or not isinstance(points, (int, float)) or not 0 < points <= 1000 or not math.isfinite(points):
                raise forms.ValidationError(f"Points for question {position} must be positive.")
            raw_options = item.get("options")
            if not isinstance(raw_options, list) or not 2 <= len(raw_options) <= 20:
                raise forms.ValidationError(f"Question {position} must have between 2 and 20 options.")
            options = []
            for option in raw_options:
                if not isinstance(option, dict) or not isinstance(option.get("text"), str) or not option["text"].strip():
                    raise forms.ValidationError(f"Each option in question {position} must have text.")
                if len(option["text"].strip()) > 500:
                    raise forms.ValidationError(f"An option in question {position} exceeds 500 characters.")
                raw_option_id = option.get("_id", option.get("id"))
                if raw_option_id is not None and (not isinstance(raw_option_id, str) or not ObjectId.is_valid(raw_option_id)):
                    raise forms.ValidationError(f"Invalid option ID in question {position}.")
                option_id = ObjectId(raw_option_id) if raw_option_id is not None else ObjectId()
                if any(existing["_id"] == option_id for existing in options):
                    raise forms.ValidationError(f"Duplicate option ID in question {position}.")
                options.append({"_id": option_id, "text": option["text"].strip()})
            correct = item.get("correct_option_ids")
            if not isinstance(correct, list) or not correct:
                raise forms.ValidationError(f"Choose at least one correct answer for question {position}.")
            correct_ids = []
            for reference in correct:
                if isinstance(reference, bool):
                    raise forms.ValidationError(f"Invalid option reference in question {position}.")
                if isinstance(reference, int):
                    if reference < 0 or reference >= len(options):
                        raise forms.ValidationError(f"Invalid option reference in question {position}.")
                    correct_ids.append(options[reference]["_id"])
                elif isinstance(reference, str) and ObjectId.is_valid(reference):
                    option_id = ObjectId(reference)
                    if option_id not in [option["_id"] for option in options]:
                        raise forms.ValidationError(f"Invalid option reference in question {position}.")
                    correct_ids.append(option_id)
                else:
                    raise forms.ValidationError(f"Invalid option reference in question {position}.")
            if len(set(correct_ids)) != len(correct_ids):
                raise forms.ValidationError(f"A correct answer is repeated in question {position}.")
            if qtype in {"single_choice", "true_false"} and len(correct) != 1:
                raise forms.ValidationError(f"Question {position} requires exactly one correct answer.")
            if qtype == "true_false" and (len(options) != 2 or {o["text"].strip().lower() for o in options} not in ({"vrai", "faux"}, {"true", "false"})):
                raise forms.ValidationError(f"True/false question {position} must provide True and False options.")
            questions.append({"_id": question_id, "position": position, "type": qtype,
                              "prompt": prompt.strip(), "points": points, "options": options,
                              "correct_option_ids": correct_ids})
        return questions

    def to_document(self):
        data = dict(self.cleaned_data)
        data["questions"] = data.pop("questions_json")
        return data


class QuizAIGenerationForm(forms.Form):
    topic = forms.CharField(label="Quiz topic", max_length=160, strip=True)
    description = forms.CharField(label="Description", max_length=1500, strip=True)
    objectives = forms.CharField(label="Learning objectives", max_length=1500, strip=True)
    question_count = forms.TypedChoiceField(
        label="Number of questions",
        choices=((5, "5 questions"), (10, "10 questions"), (15, "15 questions"), (20, "20 questions")),
        coerce=int,
    )
    duration_minutes = forms.IntegerField(
        label="Duration (minutes)",
        min_value=0,
        max_value=300,
        required=False,
        initial=15,
    )
    difficulty = forms.ChoiceField(
        label="Difficulty",
        choices=(("beginner", "Beginner"), ("intermediate", "Intermediate"), ("advanced", "Advanced")),
    )
    question_type = forms.ChoiceField(
        label="Question types",
        choices=(("single_choice", "Single choice"), ("multiple_choice", "Multiple choice"),
                 ("true_false", "True / False"), ("mixed", "Mixed")),
    )
    language = forms.ChoiceField(label="Language", choices=(("en", "English"), ("fr", "French")), initial="en")


class QuizSubmissionForm(forms.Form):
    """Build answer fields from the current published quiz document."""

    def __init__(self, *args, quiz, **kwargs):
        super().__init__(*args, **kwargs)
        self.quiz = quiz
        self.question_fields = {}
        for question in quiz.get("questions", []):
            field_name = f"question_{question['_id']}"
            choices = [(str(option["_id"]), option["text"]) for option in question.get("options", [])]
            if question["type"] == "multiple_choice":
                self.fields[field_name] = forms.MultipleChoiceField(
                    label=question["prompt"], choices=choices,
                    widget=forms.CheckboxSelectMultiple,
                    required=False,
                )
            else:
                self.fields[field_name] = forms.ChoiceField(
                    label=question["prompt"], choices=choices,
                    widget=forms.RadioSelect,
                    required=False,
                )
            self.question_fields[field_name] = question

    def selected_answers(self):
        answers = {}
        for field_name, question in self.question_fields.items():
            values = self.cleaned_data.get(field_name, [])
            answers[question["_id"]] = values if isinstance(values, list) else ([values] if values else [])
        return answers


def _normalize_extended_json(value):
    if isinstance(value, dict):
        if set(value) == {"$oid"} and isinstance(value["$oid"], str):
            return value["$oid"]
        return {key: _normalize_extended_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_extended_json(item) for item in value]
    return value


class QuizImportForm(forms.Form):
    quiz_file = forms.FileField(label="Quiz JSON file")

    def clean_quiz_file(self):
        uploaded = self.cleaned_data["quiz_file"]
        if uploaded.size > 1_000_000:
            raise forms.ValidationError("The file must not exceed 1 MB.")
        try:
            data = json.loads(uploaded.read().decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise forms.ValidationError("The file must contain valid UTF-8 JSON.") from error
        data = _normalize_extended_json(data)
        if not isinstance(data, dict):
            raise forms.ValidationError("The file must contain a quiz JSON object.")
        if not isinstance(data.get("questions"), list):
            raise forms.ValidationError("The file must contain a `questions` list.")
        if len(data["questions"]) > 100:
            raise forms.ValidationError("A quiz cannot contain more than 100 questions.")
        return data


