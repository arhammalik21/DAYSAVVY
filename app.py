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
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect(app)


from flask import jsonify
from flask_wtf.csrf import generate_csrf

@app.route("/csrf-token", methods=["GET"])
def get_csrf_token():
    token = generate_csrf()
    return jsonify({"csrf_token": token})

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

# ---------------- Helper function to safely parse JSON ----------------
def parse_request_json(req):
    """
    Safely parse JSON from the request.
    Prevents Flask from throwing 400 errors if the body is empty or malformed.
    """
    try:
        # Try normal parsing
        data = req.get_json(force=True, silent=True)
        if not data:
            # Fallback: parse raw request body manually
            raw = req.data.decode("utf-8").strip()
            if raw:
                data = json.loads(raw)
        if not data:
            data = {}
    except Exception as e:
        print("JSON parse error:", e)
        data = {}
    return data

# Voice Command Endpoint (with due_date, category, and time support)
# Voice Command Endpoint (due_date, time, category, confirm)
from flask import request, jsonify, session

YES_WORDS = {"yes","yeah","yep","yup","sure","confirm","save","please save","do it","okay save","ok save"}
NO_WORDS  = {"no","nope","nah","cancel","discard","don’t save","dont save","do not save","stop"}

def _get_flow():
    return session.get("voice_flow") or {"mode": None, "step": None, "task": {}}

def _save_flow(flow):
    session["voice_flow"] = flow
    session.modified = True

def _clear_flow():
    session.pop("voice_flow", None)
    session.modified = True

def _title_from_transcript(tl: str) -> str:
    # strip common leading verbs/words when user says "add buy milk"
    s = tl.strip()
    for k in ("add ", "create ", "new task ", "task "):
        if s.startswith(k):
            s = s[len(k):]
            break
    return s.strip()

#-- Voice Command --
# --- Put these near the top of your file (if not already present) ---
from flask import request, jsonify, session

YES_WORDS = {"yes", "yeah", "yep", "yup", "sure", "confirm", "save", "please save", "do it", "okay", "ok"}
NO_WORDS  = {"no", "nope", "nah", "cancel", "discard", "don't save", "dont save", "do not save", "stop"}

def _get_flow():
    return session.get("voice_flow", {"mode": None, "step": None, "task": {}})

def _save_flow(flow):
    session["voice_flow"] = flow
    session.modified = True

def _clear_flow():
    if "voice_flow" in session:
        session.pop("voice_flow", None)
        session.modified = True

def _title_from_transcript(tl: str) -> str:
    s = tl.strip()
    for prefix in ("add ", "create ", "new task ", "task "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
            break
    return s

# --- Replace your existing voice_command route with this one ---
@app.route("/voice/command", methods=["POST"])
def voice_command():
    data = request.get_json(force=True, silent=True) or {}
    transcript = (data.get("transcript") or "").strip()
    tl = transcript.lower().strip()

    if not transcript:
        return jsonify({
            "message": "I didn't catch that. Say: add, delete, complete, or list.",
            "continue_listening": True,
            "task_added": False
        })

    # quick cancel / stop
    if any(w in tl for w in ("stop", "cancel", "exit", "quit")):
        _clear_flow()
        return jsonify({
            "message": "Voice flow cancelled.",
            "continue_listening": False,
            "task_added": False
        })

    # load flow
    flow = _get_flow()
    mode = flow["mode"]
    step = flow["step"]
    task = flow["task"]

    # ----------------- If currently in ADD flow -----------------
    if mode == "add":
        # 1) title
        if step == "title":
            title = _title_from_transcript(tl) or transcript
            if not title:
                return jsonify({
                    "message": "Say the task title, like 'buy milk'.",
                    "continue_listening": True,
                    "task_added": False
                })
            task["title"] = title
            flow["step"] = "due"
            _save_flow(flow)
            return jsonify({
                "message": f"Adding '{title}'. When is it due? Say 'today', 'tomorrow', a date, or 'skip'.",
                "continue_listening": True,
                "task_added": False
            })

        # 2) due
        if step == "due":
            task["due"] = None if "skip" in tl else transcript
            flow["step"] = "time"
            _save_flow(flow)
            return jsonify({
                "message": "What time? Say a time like 'at 5 pm' or say 'skip'.",
                "continue_listening": True,
                "task_added": False
            })

        # 3) time
        if step == "time":
            task["time"] = None if "skip" in tl else transcript
            flow["step"] = "category"
            _save_flow(flow)
            return jsonify({
                "message": "Which category? Say Work, Personal, Study, Health, or say 'skip'.",
                "continue_listening": True,
                "task_added": False
            })

        # 4) category
        if step == "category":
            if "skip" in tl:
                cat = "Other"
            elif "work" in tl:
                cat = "Work"
            elif "personal" in tl:
                cat = "Personal"
            elif "study" in tl or "education" in tl:
                cat = "Study"
            elif "health" in tl or "fitness" in tl:
                cat = "Health"
            else:
                cat = transcript.strip().title() or "Other"
            task["category"] = cat
            flow["step"] = "confirm"
            _save_flow(flow)
            return jsonify({
                "message": (
                    f"Confirm: '{task['title']}' "
                    f"(Due: {task.get('due') or 'none'}, "
                    f"Time: {task.get('time') or 'none'}, "
                    f"Category: {task.get('category')}). Should I save? Say 'yes' or 'no'."
                ),
                "continue_listening": True,
                "task_added": False
            })

        # 5) confirm
        if step == "confirm":
            # YES path: create `new_task` only here (avoids UnboundLocalError)
            if any(w in tl for w in YES_WORDS):
                title_to_save = task.get("title", "").strip()
                # try to use normalize_task_name if available, else fallback
                try:
                    saved_title = normalize_task_name(title_to_save)
                except Exception:
                    saved_title = title_to_save.title()
                new_task = Task(
                    name=task.get["task"],
                    due=task.get("due"),
                    time=task.get("time"),
                    category=task.get("category", "Other")
                )
                db.session.add(new_task)
                db.session.commit()
                new_task_data = {
                    "id": new_task.id,
                    "title": new_task.title,
                    "due": new_task.due,
                    "time": new_task.time,
                    "category": new_task.category,
                    "completed": new_task.completed,
                }
                _clear_flow()
                return jsonify({
                    "message": f"Task '{new_task.title}' saved.",
                    "continue_listening": False,
                    "task_added": True,
                    "reload_page": True,
                    "new_task": new_task_data
                })

            # NO path
            if any(w in tl for w in NO_WORDS):
                _clear_flow()
                return jsonify({
                    "message": "Okay, discarded.",
                    "continue_listening": False,
                    "task_added": False
                })

            # Neither: retry once, then give up
            retries = task.get("_retries", 0) + 1
            task["_retries"] = retries
            flow["task"] = task
            _save_flow(flow)

            if retries >= 2:
                _clear_flow()
                return jsonify({
                    "message": "Didn't get a clear yes or no. Discarding the task.",
                    "continue_listening": False,
                    "task_added": False
                })

            return jsonify({
                "message": "Please say 'yes' to save or 'no' to discard.",
                "continue_listening": True,
                "task_added": False
            })

    # ----------------- If not in add-flow, handle single-shot commands -----------------
    # DELETE / REMOVE
    if "delete" in tl or "remove" in tl:
        part = "delete" if "delete" in tl else "remove"
        name = tl.split(part, 1)[1].strip() if part in tl else ""
        if not name:
            return jsonify({
                "message": "Which task should I delete?",
                "continue_listening": True,
                "task_added": False
            })
        candidate = Task.query.filter(Task.title.ilike(f"%{name}%")).first()
        if not candidate:
            return jsonify({
                "message": f"I couldn't find '{name}'.",
                "continue_listening": True,
                "task_added": False
            })
        title = candidate.title
        db.session.delete(candidate)
        db.session.commit()
        _clear_flow()
        return jsonify({
            "message": f"Deleted '{title}'.",
            "continue_listening": False,
            "task_added": False,
            "reload_page": True
        })

    # COMPLETE
    if "complete" in tl or "finish" in tl or "done" in tl:
        name = ""
        for k in ("complete", "finish", "done"):
            if k in tl:
                parts = tl.split(k, 1)
                if len(parts) > 1:
                    name = parts[1].strip()
                break
        if not name:
            incompletes = Task.query.filter(Task.completed == False).limit(3).all()
            if not incompletes:
                return jsonify({
                    "message": "You have no incomplete tasks.",
                    "continue_listening": False,
                    "task_added": False
                })
            names = ", ".join(t.title for t in incompletes)
            return jsonify({
                "message": f"Which task should I complete? You have: {names}.",
                "continue_listening": True,
                "task_added": False
            })

        cand = Task.query.filter(Task.title.ilike(f"%{name}%"), Task.completed == False).first()
        if not cand:
            return jsonify({
                "message": f"I couldn't find '{name}'.",
                "continue_listening": True,
                "task_added": False
            })
        cand.completed = True
        db.session.commit()
        _clear_flow()
        return jsonify({
            "message": f"Marked '{cand.title}' complete.",
            "continue_listening": False,
            "task_added": False,
            "reload_page": True
        })

    # LIST
    if "list" in tl or "show" in tl:
        tasks = Task.query.order_by(Task.completed.asc(), Task.id.desc()).all()
        if not tasks:
            return jsonify({
                "message": "You have no tasks.",
                "continue_listening": False,
                "task_added": False
            })
        preview = ", ".join(f"{t.title} ({'done' if t.completed else 'pending'})" for t in tasks[:5])
        more = f" and {len(tasks)-5} more." if len(tasks) > 5 else ""
        return jsonify({
            "message": f"You have {len(tasks)} tasks: {preview}{more}",
            "continue_listening": False,
            "task_added": False
        })

    # START ADD FLOW
    if "add" in tl or "create" in tl or "new task" in tl:
        title = _title_from_transcript(tl)
        if title:
            flow = {"mode": "add", "step": "due", "task": {"title": title}}
            _save_flow(flow)
            return jsonify({
                "message": f"Adding '{title}'. When is it due? Say 'today', 'tomorrow', a date, or 'skip'.",
                "continue_listening": True,
                "task_added": False
            })
        else:
            flow = {"mode": "add", "step": "title", "task": {}}
            _save_flow(flow)
            return jsonify({
                "message": "Sure. What task do you want to add?",
                "continue_listening": True,
                "task_added": False
            })

    # FALLBACK
    return jsonify({
        "message": "Try: 'add buy milk', 'delete <task>', 'complete <task>', or 'list tasks'.",
        "continue_listening": True,
        "task_added": False
    })


# OpenAI Assistant skeleton
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
