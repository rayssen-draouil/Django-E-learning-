from __future__ import annotations

import io
import os
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.files.storage import FileSystemStorage
from django.templatetags.static import static
from django.utils._os import safe_join
from PIL import Image, UnidentifiedImageError

COURSE_IMAGE_DIRECTORY = "courses"
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
ALLOWED_FORMATS = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def media_root() -> Path:
    val = getattr(settings, "MEDIA_ROOT", None)
    if val:
        return Path(val)
    return Path(os.environ.get("DJANGO_MEDIA_ROOT", str(getattr(settings, "BASE_DIR", Path(".")) / "media")))


def media_url() -> str:
    url = getattr(settings, "MEDIA_URL", "")
    if not url or url == "/":
        return "/media/"
    return url


def max_image_bytes() -> int:
    return int(getattr(settings, "COURSE_IMAGE_MAX_BYTES", os.environ.get("COURSE_IMAGE_MAX_BYTES", str(DEFAULT_MAX_BYTES))))


def allowed_formats() -> set[str]:
    configured = getattr(settings, "COURSE_IMAGE_ALLOWED_FORMATS", tuple(ALLOWED_FORMATS))
    return {image_format for image_format in configured if image_format in ALLOWED_FORMATS}


def _storage() -> FileSystemStorage:
    return FileSystemStorage(location=str(media_root()), base_url=media_url())


def inspect_image(upload: Any) -> str:
    """Validate an uploaded file and return its real Pillow format."""
    if not upload:
        raise ValidationError("Choisissez une image.")

    size = getattr(upload, "size", 0) or 0
    if size <= 0:
        raise ValidationError("Le fichier image est vide.")
    if size > max_image_bytes():
        raise ValidationError("L'image ne doit pas dépasser 2 Mo.")

    extension = Path(getattr(upload, "name", "") or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError("Formats acceptés : JPG, PNG ou WebP.")

    try:
        upload.seek(0)
        payload = upload.read()
    except (AttributeError, OSError, ValueError) as error:
        raise ValidationError("Impossible de lire le fichier image.") from error
    finally:
        try:
            upload.seek(0)
        except (AttributeError, OSError):
            pass

    if not payload:
        raise ValidationError("Le fichier image est vide.")

    try:
        with Image.open(io.BytesIO(payload)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(payload)) as verified:
            image_format = (verified.format or "").upper()
            verified.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError) as error:
        raise ValidationError(
            "Le fichier n'est pas une image valide ou il est corrompu."
        ) from error

    if image_format not in allowed_formats():
        raise ValidationError("Formats acceptés : JPG, PNG ou WebP.")
    return image_format


def save_course_image(upload: Any) -> str:
    """Persist an uploaded image under MEDIA_ROOT/courses and return its relative path."""
    image_format = inspect_image(upload)
    extension = ALLOWED_FORMATS[image_format][0]
    relative_path = f"{COURSE_IMAGE_DIRECTORY}/{uuid.uuid4().hex}{extension}"
    upload.seek(0)
    saved_name = _storage().save(relative_path, upload)
    return str(saved_name).replace("\\", "/")


def delete_course_image(relative_path: Any) -> None:
    """Delete a previously stored course image; ignore missing or unsafe paths."""
    if not relative_path or not isinstance(relative_path, str):
        return
    normalized = relative_path.replace("\\", "/").lstrip("/")
    if not normalized.startswith(f"{COURSE_IMAGE_DIRECTORY}/"):
        return
    try:
        safe_path = safe_join(str(media_root()), normalized)
    except (SuspiciousFileOperation, ValueError):
        return
    try:
        Path(safe_path).unlink()
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return


def course_image_url(value: Any) -> str:
    """Build a public URL for a stored image, falling back to the static placeholder."""
    raw = str(value or "").strip()
    if raw.startswith(("http://", "https://", "//")):
        return raw
    if raw.startswith("/"):
        return raw
    if raw.startswith(f"{COURSE_IMAGE_DIRECTORY}/"):
        return f"{media_url().rstrip('/')}/{raw.lstrip('/')}"
    placeholder = getattr(settings, "COURSE_IMAGE_PLACEHOLDER", "images/course-placeholder.svg")
    return static(placeholder)


def course_image_alt(course: dict[str, Any]) -> str:
    alt = str(course.get("image_alt") or "").strip()
    title = str(course.get("title") or "").strip()
    return alt or title or "Illustration du cours"
