# SkillForge Real-Time Online Classroom — Developer & Operations Guide

## Section A — Feature Overview

The **SkillForge Real-Time Online Classroom** provides an integrated, browser-based virtual mentoring environment inside the existing Django 5.2 + MongoDB (PyMongo) application. When a student books an online mentorship slot and the assigned mentor accepts the request, SkillForge automatically provisions a deterministic, non-PII LiveKit room, sends a transactional email invitation via Brevo SMTP, and unlocks a secure virtual classroom accessible directly inside SkillForge.

### Key Capabilities
- **End-to-End Booking Integration:** Connects directly to `mentorship_bookings` and `mentor_availability` collections without introducing a duplicate booking model or SQL persistence.
- **Role-Scoped LiveKit WebRTC Classroom:** Real-time audio, HD camera video, screen sharing spotlight, active-speaker detection, raised-hand signaling, and live connection state telemetry powered by LiveKit.
- **Pre-Join Hardware & Readiness Lobby:** Allows mentors and students to preview their camera/microphone, switch audio/video input devices, and verify session schedule status before joining the live room.
- **Persistent & Real-Time In-Room Chat:** Synchronizes plain-text messages across participants in real time via LiveKit DataChannels while persisting chat history in MongoDB (`classroom_messages`).
- **Mentor Host & Moderation Controls:** Mentor-only controls to unlock the classroom early, request student microphone mute, lower raised hands, resend email invitations, and mark the session as completed.
- **Attendance & Lifecycle Auditing:** Records verified room join/leave events, reconnects, and cryptographically verified LiveKit webhooks in MongoDB (`classroom_events` and `mentorship_bookings.attendance`).
- **Isolated Groq AI Study Assistant:** Optional in-classroom study assistant (`explain_concept`, `practice_questions`, `summarize_notes`, `follow_up_questions`) with deterministic fallback so AI errors never disrupt the live call.

### Explicit Scope Exclusions
- **No Recording or Transcript Downloads:** Meeting recording, recording download buttons, transcript downloads, and meeting media export options are **intentionally excluded** in this phase. All LiveKit tokens are issued with `room_record=False`.
- **In-Person Sessions:** Bookings with `session_type="in_person"` continue to use physical venue locations and do not generate LiveKit rooms or online classroom invitations.

---

## Section B — Architecture and Data Flow

```mermaid
flowchart TD
    Student["Student Browser"] -->|1. Request Online Slot| BookingSvc["apps/mentors/bookings.py"]
    BookingSvc -->|Persist pending booking| Mongo[("MongoDB: mentorship_bookings")]
    Mentor["Mentor Browser"] -->|2. Accept Request| BookingSvc
    BookingSvc -->|3. Atomic Slot Claim + Stable Room Name| Mongo
    BookingSvc -->|4. Send Classroom Invite Email| Brevo["Brevo SMTP (apps/mentors/email_service.py)"]
    Brevo -->|Email with /users/mentorship/classroom/<id>/| Student
    Student & Mentor -->|5. Open Classroom Page| ClassroomView["apps/mentors/views.py: classroom_view"]
    ClassroomView -->|Authorize & Load Metadata| ClassroomSvc["apps/mentors/classroom.py"]
    Student & Mentor -->|6. Click Join Classroom (POST /token/)| TokenAPI["classroom_token_api"]
    TokenAPI -->|Verify Role, Status & Schedule Window| ClassroomSvc
    ClassroomSvc -->|Sign Short-Lived JWT (livekit-api)| LiveKitCloud["LiveKit Server (WSS)"]
    Student & Mentor <-->|7. WebRTC Audio / Video / Screen / Data| LiveKitCloud
    LiveKitCloud -->|8. Signed Webhook Events| WebhookAPI["livekit_webhook_view"]
    WebhookAPI -->|Verify Signature & Record Attendance| Mongo
```

---

## Section C — Booking-to-Meeting Workflow

1. **Request Creation (`pending`):**
   - A student selects an open online availability slot and submits their learning goal.
   - `create_booking_request()` creates a document in `mentorship_bookings` with `status="pending"`, `session_type="online"`, `classroom_status="requested"`, and `invitation_status="pending"`.
2. **Mentor Acceptance (`accepted` / `scheduled`):**
   - Only the assigned mentor can accept the booking (`mentor_accept_booking()`).
   - The availability slot is atomically claimed in `mentor_availability`.
   - A deterministic, non-PII room identifier (`sf-room-<booking_id>-<hmac12>`) is generated via `classroom.generate_stable_room_name()`.
   - The booking is updated in MongoDB **before** email dispatch with `status="accepted"`, `classroom_status="scheduled"`, `livekit_room_name`, `invitation_id`, and `meeting_link="/users/mentorship/classroom/<booking_id>/"`.
3. **Brevo Email Invitation Dispatch:**
   - SkillForge sends `templates/emails/classroom_invitation_student.html` (and `.txt` fallback) to the student's registered email address.
   - The email contains the internal SkillForge classroom URL (`http://127.0.0.1:8000/users/mentorship/classroom/<booking_id>/`) and never embeds raw LiveKit tokens or secrets.
   - If SMTP delivery fails, the booking remains `accepted`, `invitation_status` is set to `"failed"`, and the mentor can click **Resend Email Invite** from their dashboard.
4. **Rescheduling (`rescheduled_pending` -> `accepted`):**
   - If the mentor proposes a new time, `classroom_status` transitions to `"reschedule_pending"`, temporarily blocking room entry.
   - When the student accepts the new schedule, `classroom_status` returns to `"scheduled"`, `invitation_id` rotates, and an updated classroom invitation email (`is_reschedule=True`) is sent while preserving the stable `livekit_room_name`.
5. **Joining & Schedule Window Enforcement (`ready_to_join` / `in_progress`):**
   - Participants can open the classroom lobby at any time to check session details and test their camera/microphone.
   - LiveKit token issuance (`POST /users/api/mentorship/classroom/<booking_id>/token/`) is allowed starting `CLASSROOM_EARLY_JOIN_MINUTES` (default 15 minutes) before `start_time` until `CLASSROOM_LATE_JOIN_GRACE_MINUTES` (default 180 minutes) after `end_time`.
   - The assigned mentor can optionally unlock the room early from the lobby (**Unlock & Open Classroom Early**).
6. **Completion (`completed`) or Cancellation (`cancelled`):**
   - Disconnecting or leaving the browser tab **never** marks a session as completed; both participants can reconnect freely during the valid window.
   - When the mentor clicks **Complete & End Session**, `status` and `classroom_status` transition to `"completed"`, future LiveKit token issuance is blocked, and the student is directed to the verified post-session review form.
   - If either party cancels before completion, `status` and `classroom_status` become `"cancelled"` and room access is immediately revoked.

---

## Section D — Environment Configuration

Configure the following variables in `.env` at the project root (`D:\5TWIN\Djangro\.env`). Never commit real secrets to version control.

```env
# LiveKit Real-Time Classroom Configuration
LIVEKIT_URL=wss://your-project-livekit-url
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret
LIVEKIT_TOKEN_TTL_SECONDS=900
CLASSROOM_EARLY_JOIN_MINUTES=15
CLASSROOM_LATE_JOIN_GRACE_MINUTES=180

# Public Base URL used for links in transactional emails
SITE_URL=http://127.0.0.1:8000

# Brevo SMTP Email Configuration (reused from existing project setup)
EMAIL_HOST=smtp-relay.brevo.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your_brevo_smtp_login
EMAIL_HOST_PASSWORD=your_brevo_smtp_key
DEFAULT_FROM_EMAIL=SkillForge <no-reply@skillforge.example>

# Optional Groq AI Study Assistant Configuration
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=llama-3.3-70b-versatile
```

---

## Section E — Installation and Local Startup

1. **Activate the virtual environment and install dependencies:**
   ```powershell
   cd D:\5TWIN\Djangro
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
   *(Installs `livekit-api==1.2.2`, `livekit-protocol==1.1.28`, `PyJWT==2.15.1`, and existing project packages.)*

2. **Create/verify MongoDB indexes (including classroom indexes):**
   ```powershell
   .\.venv\Scripts\python.exe manage.py create_user_indexes
   ```

3. **Run the development server:**
   ```powershell
   .\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
   ```

4. **Verify end-to-end in the browser:**
   - Log in as a **Student**, browse mentors or AI recommendations, and request an **Online** mentorship slot.
   - Log in as the **Mentor**, navigate to **Mentorship Sessions** (`/users/backoffice/mentors/sessions/`), and click **Accept Request**.
   - Click **Launch Classroom** (as Mentor) and **Join Virtual Classroom** (as Student from `/users/profile/`).

---

## Section F — File and Module Guide

| File Path | Responsibility |
| :--- | :--- |
| `apps/mentors/classroom.py` | Core classroom service: LiveKit config validation, deterministic room naming, schedule window & participant authorization, short-lived JWT generation (`livekit.api.AccessToken`), Brevo invitation sender/retry, attendance tracking, webhook verification (`livekit.api.WebhookReceiver`), in-room chat persistence, mentor moderation, and isolated Groq AI study assistant. |
| `apps/mentors/bookings.py` | Booking lifecycle integration: provisions `livekit_room_name` and `classroom_status` on acceptance, triggers `classroom_invitation_student` email, handles reschedule invitation updates, revokes classroom access on cancellation, and enforces idempotent mentor completion. |
| `apps/mentors/views.py` | HTTP views and JSON API endpoints for the virtual classroom page, metadata, token issuance, attendance, chat, moderation, completion, AI assistant, invitation retry, and LiveKit webhooks. |
| `apps/mentors/urls.py` | URL routing under the `users:` namespace for all classroom views and APIs. |
| `templates/frontoffice/classroom.html` | Responsive virtual classroom UI featuring the Pre-Join Lobby (camera/mic preview & device selectors) and Live Stage (video grid, screen share spotlight, active speaker border, toolbar controls, collapsible Chat / People / AI Study drawer). |
| `static/mentors/js/livekit-client.umd.min.js` | Locally vendored official `livekit-client` browser SDK (`v2.11.4`). |
| `templates/emails/classroom_invitation_student.html` / `.txt` | Branded HTML and plain-text email templates for initial and rescheduled online classroom invitations. |
| `templates/backoffice/mentors/sessions_list.html` | Mentor session dashboard with **Launch Classroom** button, invitation status badge, and **Resend Email Invite** action. |
| `apps/users/templates/users/profile.html` | Student profile dashboard with **Join Virtual Classroom** button for confirmed online sessions. |
| `apps/mentors/tests.py` | Unit and integration test suite (`OnlineClassroomLifecycleAndSecurityTests` + existing mentor tests). |

---

## Section G — Security Model

1. **Server-Side Secret Isolation:**
   - `LIVEKIT_API_SECRET` and `LIVEKIT_API_KEY` are read exclusively on the server in `apps/mentors/classroom.py`. They are never rendered in templates, returned in JSON responses, logged, or sent in emails.
2. **Short-Lived, Role-Scoped JWTs:**
   - Tokens expire after `LIVEKIT_TOKEN_TTL_SECONDS` (default 900 seconds / 15 minutes) and are scoped to a single `livekit_room_name`.
   - **Mentor Identity:** `mentor:<user_id>` with `room_admin=True`, `room_create=True`, `room_record=False`.
   - **Student Identity:** `student:<user_id>` with `room_admin=False`, `room_create=False`, `room_record=False`.
3. **Strict Participant & State Authorization:**
   - Every view and API endpoint verifies that `request.mongo_user` is authenticated and matches either `booking.mentor_id` or `booking.student_id`.
   - Unauthenticated users are redirected to login; authenticated non-participants receive `403 Forbidden`.
   - Tokens are refused (`409 Conflict`) if the booking is `pending`, `rescheduled_pending`, `rejected`, `cancelled`, `completed`, `no_show`, `in_person`, `too_early`, or `expired`.
4. **No Raw Token Links in Emails:**
   - Invitation emails link only to `/users/mentorship/classroom/<booking_id>/`. Clicking the link requires authenticating into SkillForge before a LiveKit token can be requested via `POST`.
5. **Cryptographic Webhook Verification:**
   - `POST /users/api/mentorship/livekit/webhook/` validates the `Authorization` header against the raw request body using `livekit.api.TokenVerifier` and `livekit.api.WebhookReceiver`. Forged or unsigned requests are rejected with `401 Unauthorized`.

---

## Section H — API and URL Reference

All routes are mounted under `/users/` (URL namespace `users:`):

| Method | Path | URL Name | Access | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/users/mentorship/classroom/<booking_id>/` | `users:mentorship_classroom` | Assigned Mentor or Student | Renders the Virtual Classroom Pre-Join Lobby & Live Stage. |
| `GET` | `/users/api/mentorship/classroom/<booking_id>/metadata/` | `users:classroom_metadata_api` | Assigned Mentor or Student | Returns session schedule, access state, attendance, and chat history. |
| `POST` | `/users/api/mentorship/classroom/<booking_id>/token/` | `users:classroom_token_api` | Assigned Mentor or Student | Validates schedule window & state and issues a short-lived LiveKit JWT. Supports `{"unlock_early": true}` for the mentor. |
| `POST` | `/users/api/mentorship/classroom/<booking_id>/attendance/` | `users:classroom_attendance_api` | Assigned Mentor or Student | Records `join`, `leave`, `reconnect`, or `heartbeat` events. |
| `GET, POST` | `/users/api/mentorship/classroom/<booking_id>/chat/` | `users:classroom_chat_api` | Assigned Mentor or Student | Lists or posts sanitized plain-text in-room chat messages. |
| `POST` | `/users/api/mentorship/classroom/<booking_id>/moderate/` | `users:classroom_moderate_api` | Assigned Mentor only | Records host moderation actions (`mute_student_request`, `lower_hand`, `unlock_early`). |
| `POST` | `/users/api/mentorship/classroom/<booking_id>/complete/` | `users:classroom_complete_api` | Assigned Mentor only | Marks the session as `completed`, closes the classroom, and notifies the student. |
| `POST` | `/users/api/mentorship/classroom/<booking_id>/ai-assist/` | `users:classroom_ai_assist_api` | Assigned Mentor or Student | Queries the isolated Groq AI classroom study assistant with deterministic fallback. |
| `POST` | `/users/backoffice/mentors/sessions/<booking_id>/resend-invitation/` | `users:mentor_resend_invitation` | Assigned Mentor only | Retries or resends the Brevo classroom invitation email to the student. |
| `POST` | `/users/api/mentorship/livekit/webhook/` | `users:livekit_webhook` | LiveKit Server (Signed JWT) | Processes verified LiveKit room/participant lifecycle webhooks. |

---

## Section I — Database Structure

### 1. `mentorship_bookings` (Extended Fields)
- `livekit_room_name` (`str`): Deterministic room identifier (`sf-room-<booking_id>-<hmac12>`), empty for `in_person`.
- `classroom_url` (`str`): Internal path `/users/mentorship/classroom/<booking_id>/`.
- `classroom_status` (`str`): One of `requested`, `scheduled`, `ready_to_join`, `in_progress`, `completed`, `cancelled`, `rejected`, `reschedule_pending`, `no_show`, `expired`, `not_applicable`.
- `mentor_opened_early` (`bool`): True when the mentor unlocks the room prior to the standard early-join window.
- `invitation_id` (`str`): Unique identifier for the current schedule's email invitation.
- `invitation_status` (`str`): `pending`, `sent`, `failed`, or `not_required`.
- `invitation_sent_at` (`datetime | None`): UTC timestamp of last successful email dispatch.
- `invitation_last_error` (`str`): Diagnostic code if SMTP delivery failed.
- `invitation_retry_count` (`int`): Number of manual invitation retries.
- `meeting_started_at` (`datetime | None`): UTC timestamp when the first participant connected to the LiveKit room.
- `completed_at` (`datetime | None`): UTC timestamp when the mentor completed the session.
- `attendance` (`dict`):
  - `mentor_joined` (`bool`), `mentor_first_joined_at`, `mentor_last_joined_at`, `mentor_last_left_at`, `mentor_join_count` (`int`)
  - `student_joined` (`bool`), `student_first_joined_at`, `student_last_joined_at`, `student_last_left_at`, `student_join_count` (`int`)
  - `both_attended` (`bool`)

### 2. `classroom_events` Collection
Stores auditable session lifecycle events (`token_issued`, `participant_joined`, `participant_left`, `lobby_visited`, `invitation_sent`, `invitation_failed`, `mentor_opened_early`, `moderation_*`, `session_completed`).
- Indexes:
  - `[("booking_id", 1), ("created_at", -1)]` (`classroom_events_booking_recent`)
  - `[("external_event_id", 1)]` unique sparse (`classroom_events_external_id_unique`)

### 3. `classroom_messages` Collection
Stores sanitized plain-text in-session chat messages (`booking_id`, `sender_id`, `sender_name`, `sender_role`, `message`, `created_at`).
- Index:
  - `[("booking_id", 1), ("created_at", 1)]` (`classroom_messages_booking_chronological`)

---

## Section J — Testing and Troubleshooting

### Running Automated Tests
Run the complete mentor and online classroom test suite (70 tests):
```powershell
D:\5TWIN\Djangro\.venv\Scripts\python.exe D:\5TWIN\Djangro\manage.py test apps.mentors.tests -v 2
```

Run only the Online Classroom lifecycle and security tests:
```powershell
D:\5TWIN\Djangro\.venv\Scripts\python.exe D:\5TWIN\Djangro\manage.py test apps.mentors.tests.OnlineClassroomLifecycleAndSecurityTests -v 2
```

### Common Troubleshooting
- **"LiveKit virtual classroom is not configured properly" (HTTP 503):**
  - Check that `LIVEKIT_URL` starts with `wss://` or `ws://` and that `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` in `.env` are real credentials rather than placeholder values (`your_livekit_api_key`).
- **"The virtual classroom opens 15 minutes before the scheduled start time" (HTTP 409):**
  - The booking is confirmed (`accepted`), but the current time is earlier than `start_time - 15 minutes`. If you are logged in as the assigned **Mentor**, click **Unlock & Open Classroom Early (Host)** in the lobby to open the room immediately.
- **Camera or Microphone Permission Denied in Browser:**
  - The lobby gracefully falls back to audio-only or listen/chat-only mode and displays an informational banner. Allow camera/microphone access in the browser address bar to enable publishing tracks.
- **Invitation Email Not Received by Student:**
  - Check the session card in the Mentor Sessions Dashboard (`/users/backoffice/mentors/sessions/`). If the badge shows `Email Invite Failed`, verify Brevo SMTP settings (`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`) in `.env` and click **Resend Email Invite**. Note that the student can also join directly from `/users/profile/` under **Upcoming Confirmed** sessions even without the email.

---

## Section K — Operational Notes

- **Timezones:** All session schedule comparisons (`get_booking_schedule_bounds`) use the Django project's configured `TIME_ZONE` (`settings.TIME_ZONE`) and convert to UTC for window evaluation and persistence.
- **Browser Compatibility:** Requires a modern WebRTC-capable browser (Chrome, Edge, Firefox, Safari). Screen sharing (`getDisplayMedia`) is supported on desktop browsers; mobile browsers that restrict `getDisplayMedia` will keep the session connected if screen share is cancelled or unsupported.
- **Webhook Deployment Note:** LiveKit webhooks (`POST /users/api/mentorship/livekit/webhook/`) are optional in local development because the classroom client also reports verified attendance transitions via `/users/api/mentorship/classroom/<booking_id>/attendance/`. In staging/production, configure your LiveKit Cloud project webhook URL to point to `https://<your-domain>/users/api/mentorship/livekit/webhook/`.
