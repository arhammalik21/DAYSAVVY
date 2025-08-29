# -------------------------
# Imports
# -------------------------
import os
import re
import io
import json
import threading
from datetime import datetime, date, timedelta, time as dt_time
from typing import Optional, Dict, Any, Tuple

# Flask + extensions
from flask import (
    Flask, render_template, redirect, url_for, flash, abort, request,
    jsonify, session, send_from_directory
)
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, DateField, SelectField, TimeField, SubmitField
from wtforms.validators import DataRequired, Optional as WTOptional
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS

# Optional libs (may not be available in your environment)
try:
    import openai
except Exception:
    openai = None

try:
    from gtts import gTTS
    import pygame
except Exception:
    gTTS = None
    pygame = None


app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# Config
app.config['SECRET_KEY'] = ("arham0564")
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL", "sqlite:///DAYSAVVY.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['TEMPLATES_AUTO_RELOAD'] = True

# CSRF protection for forms
csrf = CSRFProtect(app)

# DB init
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Create DB tables on startup if not present (safe in dev)
with app.app_context():
    db.create_all()

# -------------------------
# Constants & Globals
# -------------------------
# STOP words & auto-stop phrases for voice control
STOP_WORDS = {"stop", "cancel", "exit", "quit", "thanks"}
AUTO_STOP_PHRASES = {
    "that's all", "thats all", "all done", "finished", "no more", "i'm done", "im done", "nothing else",
    "that's it", "thats it"
}

# session key for conversation FSM
SESSION_CONV_KEY = 'conversation_state'

# -------------------------
# Models
# -------------------------
class Task(db.Model):
    """
    Persistent Task model.
    We unify fields to avoid mismatch between different versions of the model in your pasted code.
    Fields:
      - id (int PK)
      - name (string): task name
      - due_date (date): optional due date
      - task_time (time): optional time of day
      - category (string): e.g., Work, Personal, Study, Other
      - completed (bool)
      - created_at (datetime)
    """
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(300), nullable=False)
    due_date = db.Column(db.Date, nullable=True)
    task_time = db.Column(db.Time, nullable=True)
    category = db.Column(db.String(100), default='Other')
    completed = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Task id={self.id} name={self.name!r} completed={self.completed}>"

# -------------------------
# Forms
# -------------------------
class TaskForm(FlaskForm):
    """WTForms form used in the web UI to add/update tasks."""
    task = StringField('Task', validators=[DataRequired()])
    due_date = DateField('Due Date', format='%Y-%m-%d', validators=[WTOptional()])
    task_time = TimeField('Time', validators=[WTOptional()])
    category = SelectField('Category', choices=[
        ('Work', 'Work'), ('Personal', 'Personal'), ('Study', 'Study'), ('Other', 'Other')
    ])
    submit = SubmitField('Add Task')

# -------------------------
# Helper utilities
# -------------------------
def task_to_dict(t: Task) -> Dict[str, Any]:
    """Serialize Task model to JSON-serializable dict for APIs and voice responses."""
    return {
        "id": t.id,
        "name": t.name,
        "completed": t.completed,
        "due_date": t.due_date.strftime("%Y-%m-%d") if t.due_date else None,
        "task_time": t.task_time.strftime("%H:%M:%S") if t.task_time else None,
        "category": t.category,
        "created_at": t.created_at.isoformat() if t.created_at else None
    }

def parse_time_from_text(text: str) -> Optional[dt_time]:
    """
    Try to extract a time-of-day from the given text.
    Supports patterns like:
      - "at 5 pm", "at 5pm", "5pm"
      - "at 17:30", "17:30"
      - "at 7:15 a.m.", "7:15 am"
    Returns a datetime.time or None.
    Note: This is a relatively simple parser (regex-based) and not as robust
    as dateutil/parsedatetime; it's designed to cover common voice phrases.
    """
    text = text.lower()
    # Common patterns
    # 1) hh:mm (24h or 12h)
    m = re.search(r'(\b[01]?\d|2[0-3]):([0-5]\d)\b', text)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        return dt_time(hour=hh, minute=mm)

    # 2) hh am/pm (with optional "at")
    m2 = re.search(r'\b(?:at\s*)?([1-9]|1[0-2])(?:[:.]\s*([0-5]\d))?\s*(am|pm|a\.m\.|p\.m\.)?\b', text)
    if m2:
        hour = int(m2.group(1))
        minute = int(m2.group(2)) if m2.group(2) else 0
        ampm = m2.group(3)
        if ampm:
            ampm = ampm.replace('.', '')
            if 'p' in ampm:
                if hour != 12:
                    hour += 12
            else:
                if hour == 12:
                    hour = 0
        return dt_time(hour=hour % 24, minute=minute)

    # 3) words like "noon", "midnight", "morning/evening" heuristics
    if 'noon' in text:
        return dt_time(hour=12, minute=0)
    if 'midnight' in text:
        return dt_time(hour=0, minute=0)
    if 'morning' in text:
        return dt_time(hour=9, minute=0)
    if 'evening' in text or 'night' in text:
        return dt_time(hour=19, minute=0)

    return None

def parse_due_date_from_text(text: str) -> Optional[date]:
    """
    Parse simple date references:
      - "today", "tomorrow"
      - "on YYYY-MM-DD" (explicit)
      - "on July 20" is not robustly parsed here (would require dateparser)
    This keeps the parser small and deterministic.
    """
    text = text.lower()
    if 'today' in text:
        return date.today()
    if 'tomorrow' in text:
        return date.today() + timedelta(days=1)

    # ISO date like 2025-08-28 or 2025/08/28
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if m:
        try:
            y = int(m.group(1)); mo = int(m.group(2)); d = int(m.group(3))
            return date(y, mo, d)
        except Exception:
            return None

    # Friendly fallback: try month-day like "july 20" (limited)
    m2 = re.search(r'([a-zA-Z]+)\s+([0-3]?\d)', text)
    if m2:
        # Map month name -> month number
        try:
            mon_name = m2.group(1)
            day = int(m2.group(2))
            mon = datetime.strptime(mon_name, '%B').month
            year = date.today().year
            # If date already passed this year, assume next year
            dt_candidate = date(year, mon, day)
            if dt_candidate < date.today():
                dt_candidate = date(year+1, mon, day)
            return dt_candidate
        except Exception:
            try:
                mon = datetime.strptime(mon_name, '%b').month
                day = int(m2.group(2))
                year = date.today().year
                dt_candidate = date(year, mon, day)
                if dt_candidate < date.today():
                    dt_candidate = date(year+1, mon, day)
                return dt_candidate
            except Exception:
                return None

    return None

def normalize_task_name(s: str) -> str:
    """Cleanup task name text (strip, collapse spaces)."""
    return re.sub(r'\s+', ' ', s.strip())

# TTS playing helper (non-blocking)
def speak_text_nonblocking(text: str):
    """
    Play text-to-speech without blocking the main thread.
    Uses gTTS + pygame if available. If not available, prints text.
    In production, you probably want client-side TTS instead.
    """
    if not gTTS or not pygame:
        print("[TTS] gTTS/pygame not available. Text:", text)
        return

    def _play():
        try:
            tts = gTTS(text=text, lang='en', slow=False)
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            fp.seek(0)
            pygame.mixer.init()
            pygame.mixer.music.load(fp)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
        except Exception as e:
            print("[TTS] playback error:", e)

    threading.Thread(target=_play, daemon=True).start()

# -------------------------
# Web UI Routes (Flask)
# -------------------------
@app.after_request
def add_no_cache_headers(response):
    """
    Prevent caching so the browser always shows latest DB state.
    You had this in your original code; it's preserved.
    """
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route("/", methods=["GET", "POST"])
def index():
    """
    Main web page:
    - Supports adding tasks via form (task name, due_date, time, category)
    - Queries DB for tasks and passes them to template
    - Template should iterate over `incomplete_tasks` and `completed_tasks`
    """
    form = TaskForm()
    if form.validate_on_submit():
        # Create DB-backed task
        t = Task(
            name = normalize_task_name(form.task.data),
            due_date = form.due_date.data,
            task_time = form.task_time.data,
            category = form.category.data
        )
        db.session.add(t)
        db.session.commit()
        flash("Task added!", "success")
        # Redirect to avoid duplicate POST on refresh
        return redirect(url_for("index"))

    # Query parameters for search/status filters (kept from original)
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()

    query = Task.query
    if q:
        query = query.filter(Task.name.contains(q))
    if status == "incomplete":
        query = query.filter(Task.completed == False)
    elif status == "completed":
        query = query.filter(Task.completed == True)
    elif status == "overdue":
        query = query.filter(Task.completed == False, Task.due_date != None, Task.due_date < date.today())

    tasks_filtered = query.order_by(Task.created_at.desc()).all()
    incomplete_tasks = [t for t in tasks_filtered if not t.completed]
    completed_tasks = [t for t in tasks_filtered if t.completed]

    # Debug console prints (preserved)
    print(f"DEBUG: Total tasks: {len(tasks_filtered)}; incomplete: {len(incomplete_tasks)}; completed: {len(completed_tasks)}")
    for t in tasks_filtered:
        print(f"  Task {t.id}: {t.name} | completed={t.completed} | due={t.due_date} time={t.task_time} category={t.category}")

    return render_template("index.html",
                           form=form,
                           tasks=tasks_filtered,
                           incomplete_tasks=incomplete_tasks,
                           completed_tasks=completed_tasks,
                           current_date=date.today())

@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
def edit_task(task_id):
    """
    Edit an existing task via web form; pre-populates fields.
    """
    task = Task.query.get_or_404(task_id)
    form = TaskForm()
    if request.method == "POST" and form.validate_on_submit():
        task.name = normalize_task_name(form.task.data)
        task.due_date = form.due_date.data
        task.task_time = form.task_time.data
        task.category = form.category.data
        db.session.commit()
        flash("Task updated!", "success")
        return redirect(url_for("index"))

    # Pre-fill form on GET
    form.task.data = task.name
    form.due_date.data = task.due_date
    form.task_time.data = task.task_time
    form.category.data = task.category
    return render_template("edit_task.html", form=form, task=task)

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    """
    Delete task by id (form POST)
    """
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    flash("Task deleted!", "warning")
    return redirect(url_for("index"))

@app.route("/complete/<int:task_id>", methods=["POST"])
def complete_task(task_id):
    """
    Mark a task as completed.
    """
    task = Task.query.get_or_404(task_id)
    task.completed = True
    db.session.commit()
    flash("Task marked as completed!", "success")
    return redirect(url_for("index"))

# API endpoints for AJAX or external access
@app.route("/api/tasks", methods=["GET"])
def api_get_tasks():
    """Return JSON list of tasks (newest first)."""
    tasks_q = Task.query.order_by(Task.created_at.desc()).all()
    return jsonify([task_to_dict(t) for t in tasks_q])

@app.route("/api/tasks", methods=["POST"])
def api_add_task():
    """Add a task using JSON body. Returns created task."""
    data = request.get_json() or {}
    name = data.get("name") or data.get("task") or ""
    if not name.strip():
        return jsonify({"error": "Task name is required"}), 400
    due_date = None
    if data.get("due_date"):
        try:
            due_date = datetime.strptime(data.get("due_date"), "%Y-%m-%d").date()
        except Exception:
            due_date = None
    task_time = None
    if data.get("task_time"):
        try:
            task_time = datetime.strptime(data.get("task_time"), "%H:%M:%S").time()
        except Exception:
            try:
                task_time = datetime.strptime(data.get("task_time"), "%H:%M").time()
            except Exception:
                task_time = None
    t = Task(
        name = normalize_task_name(name),
        due_date = due_date,
        task_time = task_time,
        category = data.get("category", "Other")
    )
    db.session.add(t)
    db.session.commit()
    return jsonify(task_to_dict(t)), 201

@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
def api_update_task(task_id):
    """Update a task via JSON body."""
    t = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    if "name" in data:
        t.name = normalize_task_name(data["name"])
    if "due_date" in data:
        try:
            t.due_date = datetime.strptime(data.get("due_date"), "%Y-%m-%d").date() if data.get("due_date") else None
        except Exception:
            pass
    if "task_time" in data:
        try:
            t.task_time = datetime.strptime(data.get("task_time"), "%H:%M").time() if data.get("task_time") else None
        except Exception:
            pass

    if "category" in data:
        t.category = data.get("category", t.category)
    if "completed" in data:
        t.completed = bool(data.get("completed"))
    db.session.commit()
    return jsonify(task_to_dict(t))

@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def api_delete_task(task_id):
    t = Task.query.get_or_404(task_id)
    db.session.delete(t)
    db.session.commit()
    return jsonify({"message": "Task deleted"})

# -------------------------
# Voice Command Endpoint (with due_date, category, and time support)
# -------------------------
@app.route("/voice/command", methods=["POST"])
def voice_command():
    """
    Main voice endpoint. Expects JSON: { "transcript": "<text>" }
    Conversation flow (FSM stored in session):
      - If user says "add X" -> create DB task with name X, then set conversation state to expect due_date
      - When expecting due_date: parse "today", "tomorrow", explicit date, or "skip"
        -> set due_date then ask for time (or allow time in same phrase)
      - Parse time whenever present in the transcript (e.g., "at 5 pm")
      - After due_date step, next expected is "time" or "category" depending on what you want.
        In this implementation we will ask for due_date -> time(optional) -> category
      - For delete/complete/list we try to match tasks by partial name (case-insensitive)
    Returns JSON:
      - message (string) - user-facing text
      - continue_listening (bool) - whether front-end should keep microphone open
      - reload_page (bool) - whether front-end should reload the tasks UI (True when DB changed)
    """
    try:
        data = request.get_json() or {}
        transcript = (data.get("transcript") or "").strip()
        if not transcript:
            return jsonify({"message": "No speech detected. Please try again.", "continue_listening": True})

        tl = transcript.lower().strip()
        print(f"[VOICE] Raw transcript: {transcript}")

        # Initialize conversation state in session if missing
        conv = session.get(SESSION_CONV_KEY, {"next_expected": None, "pending_task_id": None})
        expecting = conv.get("next_expected")
        pending_id = conv.get("pending_task_id")

        # STOP words + auto stop
        if any(word in tl for word in STOP_WORDS):
            session.pop(SESSION_CONV_KEY, None)
            session.modified = True
            return jsonify({"message": "Voice control stopped. Goodbye!", "continue_listening": False})

        if any(phrase in tl for phrase in AUTO_STOP_PHRASES):
            session.pop(SESSION_CONV_KEY, None)
            session.modified = True
            return jsonify({"message": "All done! Voice control stopped.", "continue_listening": False})

        # If we are in the middle of an "add" conversation, handle follow-ups first
        if expecting and pending_id:
            # Fetch pending task from DB
            pending_task = Task.query.get(pending_id)
            if not pending_task:
                # Task vanished; reset conversation
                session.pop(SESSION_CONV_KEY, None)
                session.modified = True
                return jsonify({"message": "Pending task not found. Let's start again.", "continue_listening": True})

            # 1) If expecting due_date: try to parse due date (or detect time+date in same phrase)
            if expecting == "due_date":
                # Try parsing due date from incoming phrase
                dd = parse_due_date_from_text(tl)
                tt = parse_time_from_text(tl)
                if dd:
                    pending_task.due_date = dd
                # If time included, set it as well
                if tt:
                    pending_task.task_time = tt
                db.session.commit()

                # Move to time (if not provided) else category step
                if not tt:
                    conv["next_expected"] = "time"
                    conv["pending_task_id"] = pending_id
                    session[SESSION_CONV_KEY] = conv
                    session.modified = True
                    return jsonify({
                        "message": "When should this happen? Say a time like 'at 5 pm', or say 'skip' to keep no time.",
                        "continue_listening": True
                    })
                else:
                    # Time included already, next ask for category
                    conv["next_expected"] = "category"
                    session[SESSION_CONV_KEY] = conv
                    session.modified = True
                    return jsonify({
                        "message": f"Due date set to {pending_task.due_date.strftime('%b %d') if pending_task.due_date else 'unspecified'} and time set to {pending_task.task_time.strftime('%-I:%M %p') if pending_task.task_time else 'unspecified'}. What category? Say Work, Personal, Study, Health, or Other.",
                        "continue_listening": True
                    })

            # 2) If expecting time
            if expecting == "time":
                # parse time
                tt = parse_time_from_text(tl)
                if "skip" in tl or "no" in tl:
                    conv["next_expected"] = "category"
                    session[SESSION_CONV_KEY] = conv
                    session.modified = True
                    return jsonify({"message": "No time set. What category? Say Work, Personal, Study, Health, or Other.", "continue_listening": True})
                if tt:
                    pending_task.task_time = tt
                    db.session.commit()
                    conv["next_expected"] = "category"
                    session[SESSION_CONV_KEY] = conv
                    session.modified = True
                    return jsonify({"message": f"Time set to {tt.strftime('%-I:%M %p')}. What category? Say Work, Personal, Study, Health, or Other.", "continue_listening": True})
                else:
                    return jsonify({"message": "Please say a time like 'at 5 pm' or say 'skip'.", "continue_listening": True})

            # 3) If expecting category
            if expecting == "category":
                chosen = "Other"
                if "work" in tl:
                    chosen = "Work"
                elif "personal" in tl:
                    chosen = "Personal"
                elif "study" in tl or "education" in tl:
                    chosen = "Study"
                elif "health" in tl or "fitness" in tl:
                    chosen = "Health"
                # Update and finish conversation
                pending_task.category = chosen
                db.session.commit()
                session.pop(SESSION_CONV_KEY, None)
                session.modified = True
                message = f"Perfect! Task '{pending_task.name}' added with category {chosen}."
                # Optionally voice the reply
                # speak_text_nonblocking(message)
                return jsonify({"message": message, "continue_listening": True, "reload_page": True})

        # If not in a multi-step conversation: parse main commands
        # DELETE command
        if "delete" in tl or "remove" in tl:
            token = "delete" if "delete" in tl else "remove"
            parts = tl.split(token, 1)
            task_name = ""
            if len(parts) > 1:
                task_name = parts[1].replace("task", "").strip()
            if not task_name:
                return jsonify({"message": "What should I delete? Say 'delete shopping' or 'remove gym'.", "continue_listening": True})
            # Attempt partial match (case-insensitive)
            candidate = Task.query.filter(Task.name.ilike(f"%{task_name}%")).first()
            if candidate:
                name = candidate.name
                db.session.delete(candidate)
                db.session.commit()
                return jsonify({"message": f"Deleted '{name}' successfully!", "continue_listening": True, "reload_page": True})
            else:
                # Suggest a few tasks
                some = Task.query.limit(3).all()
                if some:
                    names = ", ".join(t.name for t in some)
                    return jsonify({"message": f"Could not find '{task_name}'. You have: {names}", "continue_listening": True})
                return jsonify({"message": "No tasks to delete.", "continue_listening": True})

        # COMPLETE command
        if "complete" in tl or "finish" in tl or ("mark" in tl and "complete" in tl) or ("i'm done" in tl) or ("im done" in tl):
            # try extract name after keyword
            task_name = ""
            for k in ("complete", "finish", "mark", "done"):
                if k in tl:
                    parts = tl.split(k, 1)
                    if len(parts) > 1:
                        task_name = parts[1].replace("task", "").strip()
                        break
            # If no explicit name, suggest incomplete tasks
            if not task_name:
                incompletes = Task.query.filter(Task.completed == False).limit(3).all()
                if incompletes:
                    names = ", ".join(t.name for t in incompletes)
                    return jsonify({"message": f"Which task should I complete? You have: {names}", "continue_listening": True})
                return jsonify({"message": "You have no incomplete tasks.", "continue_listening": True})
            candidate = Task.query.filter(Task.name.ilike(f"%{task_name}%"), Task.completed == False).first()
            if candidate:
                candidate.completed = True
                db.session.commit()
                return jsonify({"message": f"Excellent! Task '{candidate.name}' marked complete.", "continue_listening": True, "reload_page": True})
            else:
                incompletes = Task.query.filter(Task.completed == False).limit(3).all()
                if incompletes:
                    names = ", ".join(t.name for t in incompletes)
                    return jsonify({"message": f"Could not find '{task_name}'. You have: {names}", "continue_listening": True})
                return jsonify({"message": "No incomplete tasks to complete.", "continue_listening": True})

        # LIST / SHOW tasks
        if "list" in tl or "show" in tl:
            incompletes = Task.query.filter(Task.completed == False).all()
            if not incompletes:
                return jsonify({"message": "You have no tasks yet.", "continue_listening": True})
            task_names = [t.name for t in incompletes[:3]]
            message = f"You have {len(incompletes)} tasks: " + ", ".join(task_names)
            if len(incompletes) > 3:
                message += f" and {len(incompletes) - 3} more."
            return jsonify({"message": message, "continue_listening": True})

        # ADD / CREATE
        if "add" in tl or "create" in tl:
            token = "add" if "add" in tl else "create"
            parts = tl.split(token, 1)
            task_name = ""
            if len(parts) > 1:
                task_name = parts[1].replace("task", "").strip()
            if not task_name:
                return jsonify({"message": "What should I add? Say 'add buy milk'.", "continue_listening": True})
            # Create the DB task immediately with minimal info,
            # then start conversation to collect due_date/time/category
            tname = normalize_task_name(task_name)
            t = Task(name=tname, category="Other")
            # Try to extract due_date and time from the same phrase (e.g., "add meeting tomorrow at 5 pm")
            dd = parse_due_date_from_text(tl)
            tt = parse_time_from_text(tl)
            if dd:
                t.due_date = dd
            if tt:
                t.task_time = tt
            db.session.add(t)
            db.session.commit()

            # Decide next expected: if due_date missing -> ask due_date; else if time missing -> ask time; else ask category
            if not t.due_date:
                next_expected = "due_date"
                prompt = "When is this due? Say 'today', 'tomorrow', a date, or 'skip'."
            elif not t.task_time:
                next_expected = "time"
                prompt = "What time? Say a time like 'at 5 pm' or 'skip'."
            else:
                next_expected = "category"
                prompt = "What category? Say Work, Personal, Study, Health, or Other."
            session[SESSION_CONV_KEY] = {"next_expected": next_expected, "pending_task_id": t.id}
            session.modified = True
            return jsonify({"message": f"Adding '{t.name}'. {prompt}", "continue_listening": True})

        # Skip / fallback when not recognized
        return jsonify({"message": f"I heard: '{transcript}'. Try: 'add [task]', 'delete [task]', 'complete [task]', 'list tasks', or 'stop'.", "continue_listening": True})

    except Exception as e:
        # Log error to console for debugging (preserve original debug style)
        print(f"[ERROR] Voice command exception: {e}")
        return jsonify({"message": "Sorry, I encountered an error. Please try again.", "continue_listening": True}), 500

# -------------------------
# Legacy compatibility route (kept)
# -------------------------
@app.route('/voice/delete-task', methods=['POST'])
def voice_delete_task_legacy():
    """
    Legacy delete endpoint (kept for compatibility with older clients).
    Expects JSON {task_identifier: <id|name>}.
    """
    try:
        data = request.get_json() or {}
        task_identifier = data.get('task_identifier') or data.get('task_name')
        if not task_identifier:
            return jsonify({'message': "No task specified to delete", 'reload_page': False, 'continue_listening': True}), 400
        deleted_task = None
        if str(task_identifier).isdigit():
            tid = int(task_identifier)
            t = Task.query.get(tid)
            if t:
                deleted_task = t
                db.session.delete(t)
                db.session.commit()
                return jsonify({'message': f"Task {tid} deleted", 'reload_page': True, 'continue_listening': True})
            return jsonify({'message': f"Task {tid} not found", 'reload_page': False, 'continue_listening': True}), 404
        else:
            # delete by name partial match
            t = Task.query.filter(Task.name.ilike(f"%{task_identifier}%")).first()
            if t:
                db.session.delete(t)
                db.session.commit()
                return jsonify({'message': f"Deleted '{t.name}'", 'reload_page': True, 'continue_listening': True})
            return jsonify({'message': f"Task '{task_identifier}' not found", 'reload_page': False, 'continue_listening': True}), 404
    except Exception as e:
        print(f"[ERROR] legacy delete: {e}")
        return jsonify({'message': f"Error deleting task: {str(e)}", 'reload_page': False, 'continue_listening': True}), 500

# -------------------------
# OpenAI Assistant skeleton (preserved but safe)
# -------------------------
class AIAssistant:
    """
    Light wrapper for OpenAI ChatCompletion (kept as a skeleton).
    Reads OPENAI_API_KEY from environment. If openai package not available or key not set,
    assistant methods will return safe fallback messages.
    """
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        if self.api_key and openai:
            openai.api_key = self.api_key
        else:
            if not self.api_key:
                print("[AIAssistant] OPENAI_API_KEY not set in environment.")
            if not openai:
                print("[AIAssistant] openai package not available.")

    def get_ai_response(self, user_input: str, current_tasks: list):
        """
        Simple assistant that instructs the model to return JSON with a small action.
        Kept extremely defensive: if OpenAI is not set up, return a safe fallback.
        """
        if not openai or not self.api_key:
            return {"action": "chat", "message": "AI assistant not configured."}

        try:
            tasks_summary = [f"ID {t.id}: {t.name}" for t in current_tasks if not t.completed]
            system_prompt = f"You are a helpful task assistant. Current pending tasks: {tasks_summary}. Respond in JSON actions."
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role":"system", "content": system_prompt},
                    {"role":"user", "content": user_input}
                ],
                max_tokens=150,
                temperature=0.7
            )
            text = response.choices[0].message.content
            try:
                return json.loads(text)
            except Exception:
                return {"action": "chat", "message": text}
        except Exception as e:
            print("[AIAssistant] OpenAI error:", e)
            return {"action": "chat", "message": "Sorry, I couldn't get a response from AI."}

    def speak_response(self, text: str):
        """Optionally speak response using TTS (non-blocking)."""
        speak_text_nonblocking(text)

ai_assistant = AIAssistant()

# -------------------------
# Favicon / Static helper
# -------------------------
@app.route('/favicon.ico')
def favicon():
    """
    Serves favicon if present under static/favicon_ico/favicon-32x32.png
    This was in your original code; keep the same path or update as needed.
    """
    fp = os.path.join(app.root_path, 'static', 'favicon_ico')
    return send_from_directory(fp, 'favicon-32x32.png', mimetype='image/png')

# -------------------------
# Run server
# -------------------------
if __name__ == "__main__":
    print("🚀 DaySavvy consolidated app starting up...")
    print("Voice commands available at: POST /voice/command (JSON: {'transcript': '...'})")
    print("Main interface at: http://127.0.0.1:5000/")
    # Ensure DB created
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)
