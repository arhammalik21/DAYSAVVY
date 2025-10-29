# Imports
import os
import re
import io
import json
import threading
import math
from datetime import datetime, date, timedelta, time as dt_time
from typing import Optional, Dict, Any, Tuple
from werkzeug.security import generate_password_hash, check_password_hash
import hashlib
from collections import OrderedDict
from gtts import gTTS
import pygame

# Flask + extensions
from flask import (
    Flask, render_template, redirect, url_for, flash, abort, request,
    jsonify, session, send_from_directory
)
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, DateField, SelectField, TimeField, SubmitField, PasswordField
from wtforms.validators import DataRequired, Optional as WTOptional, Length
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from flask_migrate import Migrate
from flask_cors import CORS
from sqlalchemy.exc import IntegrityError
from flask import (
    Flask, render_template, redirect, url_for, flash, abort, request,
    jsonify, session, send_from_directory, Response  # add Response
)

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

# Voice Components
_groq = None
try:
    # Import groq dynamically to avoid static analyzer errors when the package
    # is not installed in the development environment.
    import importlib
    groq_mod = importlib.import_module("groq")
    Groq = getattr(groq_mod, "Groq", None)
    _groq = Groq(api_key=os.getenv("GROQ_API_KEY")) if (Groq and os.getenv("GROQ_API_KEY")) else None
except Exception:
    _groq = None

def _json_from_text(s: str) -> dict:
    try:
        return json.loads(s or "")
    except Exception:
        if not s:
            return {}
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(s[i:j+1])
            except Exception:
                return {}
        return {}

def nlu_understand(text: str, lang: str = "hinglish") -> dict:
    t = (text or "").strip()
    if not t:
        return {"intent":"unknown","slots":{}}
    # Use Groq if available
    if _groq:
        try:
            sys_prompt = (
                "Extract the user's intent for a task manager.\n"
                "Intents: add_task, complete_task, delete_task, list_tasks, decompose, reschedule, smalltalk, unknown.\n"
                "Slots: task, goal, due, time, category, days.\n"
                "User may speak English, Hindi, or Hinglish. Return ONLY compact JSON."
            )
            resp = _groq.chat.completions.create(
                model=os.getenv("GROQ_MODEL", "moonshotai/kimi-k2-instruct-0905"),
                messages=[{"role":"system","content":sys_prompt},{"role":"user","content":t}],
                temperature=0.2, max_tokens=200,
            )
            data = _json_from_text(resp.choices[0].message.content)
            if isinstance(data, dict) and data.get("intent"):
                return data
        except Exception as e:
            print("[NLU][Groq] error:", e)
    # Heuristic fallback
    tl = t.lower()
    if any(k in tl for k in ("break it down","break this down","break down","decompose","subtask","plan this","plan for","tordo","tod do","toad do","tode do","tod do","toda do","toda do")):
        return {"intent":"decompose","slots":{"goal": t}}
    if any(k in tl for k in ("add","create","new task","add task","jodo","kaam jodo","task jodo","naya kaam")):
        return {"intent":"add_task","slots":{"task": _title_from_transcript(tl) or t}}
    if any(k in tl for k in ("delete","remove","mitao","hatado","hata do","delete kar do")):
        return {"intent":"delete_task","slots":{"task": tl.split("delete",1)[-1].strip() or tl.split("remove",1)[-1].strip()}}
    if any(k in tl for k in ("complete","finish","done","khatam","poora","ho gaya")):
        return {"intent":"complete_task","slots":{"task": tl.replace("complete","").replace("finish","").replace("done","").strip()}}
    if any(k in tl for k in ("list","show","tasks","dikhado","list dikhao","kaam dikhao")):
        return {"intent":"list_tasks","slots":{}}
    if any(k in tl for k in ("reschedule","postpone","move tasks","kal kar do","tomorrow","shift karo")):
        return {"intent":"reschedule","slots":{"days":"1"}}
    if any(k in tl for k in ("stressed","anxious","sad","tired","lonely","down","overwhelmed","bura lag")):
        return {"intent":"smalltalk","slots":{"mood":"low"}}
    return {"intent":"unknown","slots":{}}

def detect_emotion(text: str) -> tuple:
    t = (text or "").lower().strip()
    if not t:
        return ("neutral", 0.0)
    # Use Groq if available
    if _groq:
        try:
            resp = _groq.chat.completions.create(
                model=os.getenv("GROQ_MODEL","moonshotai/kimi-k2-instruct-0905"),
                messages=[{"role":"system","content":"Classify emotion: stressed, sad, tired, positive, neutral. Return JSON {\"emotion\":\"...\",\"score\":0-1}."},
                          {"role":"user","content": t}],
                temperature=0.1, max_tokens=40,
            )
            data = _json_from_text(resp.choices[0].message.content)
            emo = str(data.get("emotion","neutral")).lower()
            score = float(data.get("score", 0.6))
            if emo not in {"stressed","sad","tired","positive","neutral"}:
                emo = "neutral"
            return (emo, max(0.0, min(score, 1.0)))
        except Exception as e:
            print("[Emotion][Groq] error:", e)
    # Lexicon fallback
    NEG_STRESS = {"overwhelmed","stress","stressed","anxious","panic","pressure","burnout","burned out","busy","too much"}
    NEG_SAD    = {"sad","down","upset","depressed","cry","lonely","hurt","bad day"}
    NEG_TIRED  = {"tired","exhausted","fatigued","sleepy","drained","worn out","not well","sick","headache"}
    POSITIVE   = {"great","good","awesome","amazing","excited","happy","fantastic","love"}
    if any(w in t for w in NEG_STRESS): return ("stressed", 0.8)
    if any(w in t for w in NEG_SAD):    return ("sad", 0.8)
    if any(w in t for w in NEG_TIRED):  return ("tired", 0.8)
    if any(w in t for w in POSITIVE):   return ("positive", 0.7)
    return ("neutral", 0.5)

def decompose_goal_text(goal_text: str) -> list:
    text = (goal_text or "").strip()
    if not text:
        return []
    # Use Groq if available
    if _groq:
        try:
            prompt = (
                "Break the user's goal into a small, actionable checklist of 3-7 subtasks.\n"
                "Return ONLY JSON: {\"subtasks\":[{\"name\":\"...\"}, ...]}.\n"
                f"Goal: {text}"
            )
            resp = _groq.chat.completions.create(
                model=os.getenv("GROQ_MODEL","moonshotai/kimi-k2-instruct-0905"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=220,
                temperature=0.4,
            )
            data = _json_from_text(resp.choices[0].message.content)
            subs = data.get("subtasks", []) if isinstance(data, dict) else []
            out = []
            for s in subs:
                name = (s.get("name") or "").strip()
                if name:
                    out.append({"name": name})
            if out:
                return out
        except Exception as e:
            print("[Decompose][Groq] error:", e)
    # Fallback rules
    t = text.lower()
    if any(k in t for k in ("exam","midterm","test")):
        return [
            {"name": "Outline exam topics"},
            {"name": "Make formula/definition sheet"},
            {"name": "Solve last year’s paper"},
            {"name": "Review mistakes and weak areas"},
        ]
    if any(k in t for k in ("presentation","deck","slides")):
        return [
            {"name": "Define key message and outline"},
            {"name": "Draft slides"},
            {"name": "Design visuals and charts"},
            {"name": "Add speaker notes"},
            {"name": "Rehearse twice"},
        ]
    if any(k in t for k in ("trip","travel")):
        return [
            {"name": "Set dates and budget"},
            {"name": "Book flights/trains"},
            {"name": "Reserve hotel"},
            {"name": "Create itinerary"},
            {"name": "Pack essentials checklist"},
        ]
    return [
        {"name": "Clarify the goal and success criteria"},
        {"name": "List required resources"},
        {"name": "Draft a plan with milestones"},
        {"name": "Execute first milestone"},
        {"name": "Review and adjust next steps"},
    ]


# Background task for reminders
import time as time_mod
def reminder_checker():
    while True:
        with app.app_context():
            now = datetime.now()
            tasks = Task.query.filter(
                Task.reminder_time != None,
                Task.reminder_time <= now,
                Task.completed == False
            ).all()
            for t in tasks:
                print(f"[REMINDER] Task '{t.name}' is due now!")
                t.reminder_time = None
                db.session.commit()
        time_mod.sleep(60)

# A talk with Emotion
@app.route("/voice/chat", methods=["POST"])
def voice_chat():
    """
    Accepts: { "message": "user says something" }
    Returns: { "reply": "AI's reply" }
    """
    data = request.get_json(force=True, silent=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"reply": "Say something and I’ll reply!"})

    # Use Groq LLM for generative chat
    if _groq:
        try:
            # You can customize the system prompt for personality
            sys_prompt = (
                "You are DaySavvy, a friendly, helpful, and gentle productivity assistant. "
                "You can chat about anything, help with tasks, listen to feelings, and keep the conversation going naturally. "
                "Reply in a warm, human-like, casual tone. If the user seems stressed or sad, offer encouragement or a reset. "
                "If they mention tasks or goals, you can help break them down or add them."
            )
            resp = _groq.chat.completions.create(
                model=os.getenv("GROQ_MODEL", "moonshotai/kimi-k2-instruct-0905"),
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_msg}
                ],
                temperature=0.7,
                max_tokens=300,
            )
            reply = resp.choices[0].message.content.strip()
            return jsonify({"reply": reply})
        except Exception as e:
            print("[Chat][Groq] error:", e)
            return jsonify({"reply": "Sorry, I had trouble thinking of a reply. Try again?"})

    # Fallback: echo
    return jsonify({"reply": "I'm here to chat! (Groq not configured)"})

# Voice Command Constants & Globals
STOP_WORDS = {"stop", "cancel", "exit", "quit", "thanks"}
AUTO_STOP_PHRASES = {
    "that's all", "thats all", "all done", "finished", "no more", "i'm done", "im done", "nothing else",
    "that's it", "thats it"
}

# session key for conversation FSM
SESSION_CONV_KEY = 'conversation_state'

# Models
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    name = db.Column(db.String(300), nullable=False)
    due_date = db.Column(db.Date, nullable=True)
    task_time = db.Column(db.Time, nullable=True)
    category = db.Column(db.String(100), default='Other')
    completed = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    priority = db.Column(db.String(20), default='Normal')
    reminder_time = db.Column(db.DateTime, nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=True)
    order_index = db.Column(db.Integer, nullable=True)
    subtasks = db.relationship('Task', backref=db.backref('parent', remote_side=[id]), lazy=True)
    
    def __repr__(self):
        return f"<Task id={self.id} name={self.name!r} completed={self.completed}>"
    
# Account and Authentication
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    tasks = db.relationship('Task', backref='user', lazy=True) 

# TTS Endpoint using gTTS
@app.route("/voice/tts", methods=["POST"])
def voice_tts():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return Response(b"", mimetype="audio/mpeg")
    lang = (data.get("lang") or "en").lower()
    try:
        fp = io.BytesIO()
        gTTS(text=text, lang=("hi" if lang=="hi" else "en"), slow=False).write_to_fp(fp)
        fp.seek(0)
        audio = fp.read()
        return Response(audio, mimetype="audio/mpeg", headers={"Cache-Control":"no-store"})
    except Exception as e:
        print("[TTS][gTTS] error:", e)
        return Response(b"", mimetype="audio/mpeg", status=500)
# Gentle smalltalk without external LLM (works offline/quota-free)
def _gen_empathetic_reply_local(user_text: str, emotion: str, lang: str = "hinglish") -> str:
    emo = (emotion or "neutral").lower()
    if lang == "hi":
        prefix = {
            "stressed": "Lagta hai thoda stress hai.",
            "sad": "Lagta hai mann halka sa udaas hai.",
            "tired": "Lagta hai thakan si hai.",
            "positive": "Energy achchi lag rahi hai!",
            "neutral": "Theek hai."
        }.get(emo, "Theek hai.")
        return f"{prefix} Bolo, main saath hoon. Chaho to 5‑min ka reset bana deta hoon, ya next step plan karte hain. Jo bhi chal raha hai share kar sakte ho."
    if lang == "en":
        prefix = {
            "stressed": "Sounds like there’s some pressure.",
            "sad": "You seem a bit low.",
            "tired": "You sound a little tired.",
            "positive": "Love the energy!",
            "neutral": "Got it."
        }.get(emo, "Got it.")
        return f"{prefix} I’m here with you. Want me to add a quick 5‑minute reset, or help you plan your next step? You can also just talk it out."
    # Hinglish default
    prefix = {
        "stressed": "Lagta hai thoda pressure chal raha hai.",
        "sad": "Lagta hai mood thoda low hai.",
        "tired": "Lagta hai thoda tired feel ho raha hai.",
        "positive": "Vibes achchi hain!",
        "neutral": "Theek hai."
    }.get(emo, "Theek hai.")
    return f"{prefix} Main yahin hoon. 5‑min ka reset bana du ya next step plan karein? Chahe to casually baat bhi kar sakte hain."

# ---- TTS cache ----
_TTS_CACHE = OrderedDict()
_TTS_CACHE_MAX = 256
def _tts_cache_key(provider: str, voice: str, text: str) -> str:
    return hashlib.sha256(f"{provider}|{voice}|{text}".encode("utf-8")).hexdigest()
def _tts_cache_get(key: str):
    if key in _TTS_CACHE:
        _TTS_CACHE.move_to_end(key)
        return _TTS_CACHE[key]
    return None
def _tts_cache_put(key: str, value: bytes):
    _TTS_CACHE[key] = value
    _TTS_CACHE.move_to_end(key)
    while len(_TTS_CACHE) > _TTS_CACHE_MAX:
        _TTS_CACHE.popitem(last=False)

def _azure_voice_shortname(lang: str, gender: str) -> str:
    lg = (lang or "hinglish").lower(); gd = (gender or "female").lower()
    if lg == "hi":
        return "hi-IN-MadhurNeural" if gd == "male" else "hi-IN-SwaraNeural"
    if lg == "en":
        return "en-US-GuyNeural" if gd == "male" else "en-US-JennyNeural"
    return "en-IN-PrabhatNeural" if gd == "male" else "en-IN-NeerjaNeural"

def _openai_voice_name(lang: str, gender: str) -> str:
    return "alloy" if (gender or "").lower() == "male" else "verse"

# NEW: simple emotion log (no schema break to existing tables)
class EmotionEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    emotion = db.Column(db.String(32), nullable=False)  
    score = db.Column(db.Float, default=0.0)            
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@app.route("/register", methods=["GET", "POST"])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        # Case-insensitive duplicate check
        existing = User.query.filter(db.func.lower(User.username) == username.lower()).first()
        if existing:
            flash("Username already taken. Please choose another.", "warning")
            return render_template("register.html", form=form)
        
        hashed_pw = generate_password_hash(form.password.data)
        user = User(username=username, password=hashed_pw)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Username already exists. Please choose another.", "warning")
            return render_template("register.html", form=form)

        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html", form=form)

@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, form.password.data):
            session["user_id"] = user.id
            flash("Logged in successfully!", "success")
            return redirect(url_for("index"))
        else:
            flash("Invalid username or password.", "danger")
    return render_template("login.html", form=form)

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Logged out.", "info")
    return redirect(url_for("login"))   

@app.context_processor
def inject_current_user():
    uid = session.get("user_id")
    user = User.query.get(uid) if uid else None
    return {"current_user": user}

# Forms
class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=8, message="Use 8+ characters")])
    submit = SubmitField('Register')

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class TaskForm(FlaskForm):
    """WTForms form used in the web UI to add/update tasks."""
    task = StringField('Task', validators=[DataRequired()])
    due_date = DateField('Due Date', format='%Y-%m-%d', validators=[WTOptional()])
    task_time = TimeField('Time', validators=[WTOptional()])
    category = SelectField('Category', choices=[
        ('Work', 'Work'), ('Personal', 'Personal'), ('Study', 'Study'), ('Other', 'Other')
    ])
    submit = SubmitField('Add Task')

# Helper utilities
def task_to_dict(t: Task) -> Dict[str, Any]:
    """Serialize Task model to JSON-serializable dict for APIs and voice responses."""
    return {
       "id": t.id,
        "name": t.name,
        "completed": t.completed,
        "due_date": t.due_date.strftime("%Y-%m-%d") if t.due_date else None,
        "task_time": t.task_time.strftime("%H:%M:%S") if t.task_time else None,
        "category": t.category,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "priority": t.priority,
        "reminder_time": t.reminder_time.isoformat() if t.reminder_time else None,
        "user_id": t.user_id,
        "parent_id": t.parent_id,
        "order_index": t.order_index,
        "has_subtasks": bool(t.subtasks),
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
    if not s:
        return ""
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

# Smart Task Decomposition
def _evenly_spaced_dates(final_due: Optional[date], n: int) -> list[Optional[date]]:
    """Return n dates spaced from today to final_due (inclusive). None if no final due."""
    if n <= 0:
        return []
    today = date.today()
    if not final_due:
        return [today + timedelta(days=i) for i in range(n)]
    days = max(0, (final_due - today).days)
    if days == 0:
        return [final_due] * n
    slots = []
    for i in range(n):
        pos = round(i * (days / max(1, n - 1)))
        slots.append(today + timedelta(days=pos))
    return slots

def _stagger_times(base: Optional[dt_time], n: int) -> list[Optional[dt_time]]:
    """
    Return n times. If base is provided, stagger hour +i.
    If no base, cycle through sensible slots (10:00, 14:00, 18:00).
    """
    if n <= 0:
        return []
    if base:
        return [dt_time((base.hour + i) % 24, base.minute) for i in range(n)]
    slots = [dt_time(10, 0), dt_time(14, 0), dt_time(18, 0)]
    return [slots[i % len(slots)] for i in range(n)]

def create_goal_with_subtasks(uid: int, goal_text: str, final_due: Optional[date], default_time: Optional[dt_time],
                              category: str = "Other", parent_id: Optional[int] = None) -> Dict[str, Any]:
    subtasks = decompose_goal_text(goal_text)
    if not subtasks:
        return {"parent_id": None, "count": 0, "children": []}

# Create parent
    parent = None
    if parent_id:
        parent = Task.query.filter_by(id=parent_id, user_id=uid).first()

    if parent is None:
        parent = Task(
            user_id=uid,
            name=normalize_task_name(goal_text),
            category=category,
            due_date=final_due,
            task_time=default_time,
            priority=classify_priority(goal_text),
            reminder_time=(datetime.combine(final_due, default_time) if (final_due and default_time) else None),
        )
        db.session.add(parent)
        db.session.flush()
    else:
        # Optional: update schedule if provided
        if final_due and parent.due_date != final_due:
            parent.due_date = final_due
        if default_time and parent.task_time != default_time:
            parent.task_time = default_time    
    
    dates = _evenly_spaced_dates(final_due, len(subtasks))
    times = _stagger_times(default_time, len(subtasks))
    created = []
    for idx, sub in enumerate(subtasks):
        sd = dates[idx] if idx < len(dates) else None
        st_time = times[idx] if sd else None
        st = Task(
            user_id=uid,
            name=normalize_task_name(sub["name"]),
            category=category,
            due_date=sd,
            task_time=st_time,
            priority=classify_priority(sub["name"]),
            reminder_time=(datetime.combine(sd, st_time) if (sd and st_time) else None),  # use st_time
            parent_id=parent.id,
            order_index=idx,
        )
        db.session.add(st)
        created.append(st)

    db.session.commit()  # move commit here (after loop)
    return {
        "parent_id": parent.id,
        "count": len(created),
        "children": [task_to_dict(c) for c in created],
        "parent": task_to_dict(parent),
    }

@app.route("/api/tasks/decompose", methods=["POST"])
def api_decompose_goal():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    goal_text = (data.get("goal") or "").strip()
    if not goal_text:
        return jsonify({"error": "Goal text is required"}), 400

# Parse optional final due and time
    final_due = None
    if data.get("due_date"):
        try:
            final_due = datetime.strptime(data["due_date"], "%Y-%m-%d").date()
        except Exception:
            final_due = None

    default_time = None
    if data.get("task_time"):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                default_time = datetime.strptime(data["task_time"], fmt).time()
                break
            except Exception:
                pass

    category = (data.get("category") or "Other").strip().title()
    # NEW: accept parent_id
    parent_id = data.get("parent_id")
    try:
        parent_id = int(parent_id) if parent_id is not None else None
    except Exception:
        parent_id = None

    if data.get("create"):
        result = create_goal_with_subtasks(
            uid, goal_text, final_due, default_time, category, parent_id=parent_id  # <- pass it
        )
        return jsonify({
            "created": True,
            "parent": result.get("parent"),
            "subtasks": result.get("children"),
            "count": result.get("count", 0)
        }), 201
    # Preview only
    preview = decompose_goal_text(goal_text)
    # Attach suggested schedule if due provided
    dates = _evenly_spaced_dates(final_due, len(preview)) if final_due else [None]*len(preview)
    out = []
    for idx, sub in enumerate(preview):
        d = dates[idx] if idx < len(dates) else None
        out.append({
            "name": sub["name"],
            "suggested_due_date": d.strftime("%Y-%m-%d") if d else None,
            "category": category,
            "order_index": idx
        })
    return jsonify({"created": False, "goal": goal_text, "subtasks": out})

# Web UI Routes (Flask)
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
    if "user_id" not in session:
        return redirect(url_for("login"))
    user_id = session["user_id"]
    form = TaskForm()
    
    if form.validate_on_submit():
        task_name = form.task.data
        if not task_name:
            flash("Task name is required.", "warning")
            return redirect(url_for("index"))
        
        reminder_dt = None
        if form.due_date.data and form.task_time.data:
            reminder_dt = datetime.combine(form.due_date.data, form.task_time.data)

        t = Task(
            name = normalize_task_name(task_name),
            due_date = form.due_date.data,
            task_time = form.task_time.data,
            category = form.category.data,
            priority = classify_priority(task_name),
            reminder_time = reminder_dt,
            user_id = user_id
        )
        db.session.add(t)
        db.session.commit()
        flash("Task added!", "success")
        return redirect(url_for("index"))

    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    query = Task.query.filter_by(user_id=user_id)
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

 # Build parent -> subtasks mapping for hierarchical UI
    parents = [t for t in tasks_filtered if t.parent_id is None]
    children_map: Dict[int, list[Task]] = {}
    for t in tasks_filtered:
        if t.parent_id:
            children_map.setdefault(t.parent_id, []).append(t)

# Sort subtasks: incomplete first, then order_index, then date/time, then id
    for pid, lst in children_map.items():
        lst.sort(key=lambda x: (
            x.completed,  # False first
            x.order_index if x.order_index is not None else 9999,
            x.due_date or date.max,
            x.task_time or dt_time(23, 59),
            x.id
        ))

    parents_incomplete = [p for p in parents if not p.completed]
    parents_completed  = [p for p in parents if p.completed]

    return render_template(
        "index.html",
        form=form,
        tasks=tasks_filtered,
        incomplete_tasks=incomplete_tasks,
        completed_tasks=completed_tasks,
        parents_incomplete=parents_incomplete,
        parents_completed=parents_completed,
        children_map=children_map,
        current_date=date.today()
    )

@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
def edit_task(task_id):
    if "user_id" not in session: return redirect(url_for("login"))
    task = Task.query.filter_by(id=task_id, user_id=session["user_id"]).first_or_404()
    """
    Edit an existing task via web form; pre-populates fields.
    """
    form = TaskForm()
    if request.method == "POST" and form.validate_on_submit():  
        task.name = normalize_task_name(form.task.data)
        task.due_date = form.due_date.data
        task.task_time = form.task_time.data
        task.category = form.category.data
        task.priority = classify_priority(form.task.data)
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
    if "user_id" not in session:
        return redirect(url_for("login"))
    uid = session["user_id"]

    task = Task.query.filter_by(id=task_id, user_id=uid).first_or_404()

    if task.parent_id is None:
        Task.query.filter_by(user_id=uid, parent_id=task.id).delete(synchronize_session=False)

    db.session.delete(task)
    db.session.commit()
    flash("Task deleted!", "warning")
    return redirect(url_for("index"))

@app.route("/complete/<int:task_id>", methods=["POST"])
def complete_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    uid = session["user_id"]
    task = Task.query.filter_by(id=task_id, user_id=uid).first_or_404()
    # Mark parent and all its subtasks complete
    task.completed = True
    if task.parent_id is None:
        # it's a parent: mark children complete too
        children = Task.query.filter_by(user_id=uid, parent_id=task.id).all()
        for c in children:
            c.completed = True
    db.session.commit()
    flash("Task marked as completed!", "success")
    return redirect(url_for("index"))

# API endpoints for AJAX or external access
@app.route("/api/tasks", methods=["GET"])
def api_get_tasks():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    tasks_q = Task.query.filter_by(user_id=uid).order_by(Task.created_at.desc()).all()
    return jsonify([task_to_dict(t) for t in tasks_q])

@app.route("/api/tasks", methods=["POST"])
def api_add_task():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    name = data.get("name") or data.get("task") or ""
    if not name.strip():
        return jsonify({"error": "Task name is required"}), 400

    due_date = None
    task_time = None
    if data.get("due_date"):
        try:
            due_date = datetime.strptime(data["due_date"], "%Y-%m-%d").date()
        except Exception:
            due_date = None
    if data.get("task_time"):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                task_time = datetime.strptime(data["task_time"], fmt).time()
                break
            except Exception:
                pass

    reminder_dt = datetime.combine(due_date, task_time) if (due_date and task_time) else None

    t = Task(
        name=normalize_task_name(name),
        due_date=due_date,
        task_time=task_time,
        category=data.get("category", "Other"),
        priority=classify_priority(name),
        reminder_time=reminder_dt,
        user_id=uid
    )
    db.session.add(t)
    db.session.commit()
    return jsonify(task_to_dict(t)), 201

@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
def api_update_task(task_id):
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    t = Task.query.filter_by(id=task_id, user_id=uid).first_or_404()
    data = request.get_json() or {}
    if "name" in data:
        t.name = normalize_task_name(data["name"])
        t.priority = classify_priority(t.name)
    if "due_date" in data:
        try:
            t.due_date = datetime.strptime(data["due_date"], "%Y-%m-%d").date() if data.get("due_date") else None
        except Exception:
            pass
    if "task_time" in data:
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                t.task_time = datetime.strptime(data["task_time"], fmt).time() if data.get("task_time") else None
                break
            except Exception:
                continue
    t.reminder_time = datetime.combine(t.due_date, t.task_time) if (t.due_date and t.task_time) else None
    if "category" in data:
        t.category = data.get("category", t.category)
    if "completed" in data:
        t.completed = bool(data.get("completed"))
    db.session.commit()
    return jsonify(task_to_dict(t))

def classify_priority(task_name: str) -> str:
    """Classify priority based on keywords in the task name."""
    name = task_name.lower()
    if any(word in name for word in ["urgent", "asap", "immediately", "now", "today"]):
        return "Urgent"
    if any(word in name for word in ["important", "priority", "soon", "high"]):
        return "High"
    if any(word in name for word in ["later", "someday", "eventually", "low"]):
        return "Low"
    return "Normal"

@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def api_delete_task(task_id):
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    t = Task.query.filter_by(id=task_id, user_id=uid).first_or_404()
    db.session.delete(t)
    db.session.commit()
    return jsonify({"message": "Task deleted"})

# Helper function to safely parse JSON 
def parse_request_json(req):
    """
    Safely parse JSON from the request.
    Prevents Flask from throwing 400 errors if the body is empty or malformed.
    """
    try:
        # Normal parsing
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

# Voice Command 
from flask import request, jsonify, session
from datetime import datetime, date, time, timedelta
import re

#Welcome route
@app.route("/voice/welcome", methods=["GET"])
def voice_welcome():
    return jsonify({
        "message": tr(
            "Hey, I’m here. Talk to me casually—add tasks, break a goal down, reschedule today, or just share what’s on your mind.",
            "Hi, main yahin hoon. Aaram se bolo—kaam jodo, goal tod do, aaj ke kaam shift karo, ya jo mann mein ho share karo.",
            "Hey, main yahin hoon. Bindaas bolo—task add karo, goal tod do, aaj ka shift karo, ya bas jo chal raha hai share karo."
        ),
        "continue_listening": True
    })

@app.route("/voice/command-legacy", methods=["POST"])
def voice_command_legacy():
    try:
        data = request.get_json(force=True, silent=True) or {}
        transcript = (data.get("transcript") or "").strip()
        tl = transcript.lower()
        uid = session.get("user_id")
        prefs = get_voice_prefs()
        lang = prefs.get("lang", "hinglish")

        # Cancel / Empty
        if any(k in tl for k in ("stop","cancel","exit","quit","bas","ruko")):
            _clear_flow()
            return jsonify({"message": tr("Okay, cancelled.","Theek hai, cancel kiya.","Theek hai, cancel kiya."),
                            "continue_listening": False, "task_added": False})
        if not transcript:
            return jsonify({"message": tr("I didn’t catch that. Say it again?",
                                          "Samajh nahi aaya. Dobara bolein?",
                                          "Samajh nahi aaya. Phir bolo?"),
                            "continue_listening": True, "task_added": False})

        # Emotion + proactive reschedule
        emotion, emo_score = detect_emotion(transcript)
        if uid: log_emotion(uid, emotion, emo_score)
        if uid and not _get_flow().get("mode") and emotion in {"stressed","sad","tired"}:
            todays = propose_reschedule_candidates(uid)
            if todays:
                _save_flow({"mode": "reschedule_offer", "step": "confirm", "payload": {"days": 1}})
                return jsonify({"message": tr(
                                    f"{empathetic_prefix(emotion)} Want me to move today’s {len(todays)} task(s) to tomorrow?",
                                    f"{empathetic_prefix(emotion)} Kya aaj ke {len(todays)} kaam kal shift kar du?",
                                    f"{empathetic_prefix(emotion)} Aaj ke {len(todays)} tasks kal shift kar du?"
                                ),
                                "continue_listening": True, "task_added": False})

        # NLU
        parsed = nlu_understand(transcript, lang)
        intent = parsed.get("intent","unknown")
        slots = parsed.get("slots") if isinstance(parsed.get("slots"), dict) else {}

        # Smalltalk
        if intent == "smalltalk":
            msg = _gen_empathetic_reply_local(transcript, emotion, lang)
            # Offer a tiny reset task
            _save_flow({"mode":"add","step":"due","task":{"name": tr("Take a 5‑minute reset",
                                                                     "5‑minute ka reset lo",
                                                                     "5‑minute ka reset lo")}})
            return jsonify({"message": msg, "continue_listening": True, "task_added": False})

        # Intents
        if intent == "add_task":
            name = normalize_task_name(slots.get("task") or "")
            if name:
                _save_flow({"mode":"add","step":"due","task":{"name": name}})
                return jsonify({"message": tr(
                                    f"Adding ‘{name}’. When is it due? Say today, tomorrow, a date, or skip.",
                                    f"‘{name}’ add kar raha hoon. Kab tak due? Aaj, kal, koi date, ya skip.",
                                    f"‘{name}’ add kar raha hoon. Kab tak due? Aaj, kal, koi date, ya skip."
                                ),
                                "continue_listening": True, "task_added": False})

        if intent == "list_tasks" and uid:
            tasks = Task.query.filter_by(user_id=uid).order_by(Task.completed.asc(), Task.id.desc()).all()
            if not tasks:
                return jsonify({"message": tr("You have no tasks.","Aapke paas abhi koi tasks nahi hain.","Koi tasks nahi hain."),
                                "continue_listening": False, "task_added": False})
            preview = ", ".join(f"{t.name} ({'done' if t.completed else 'pending'})" for t in tasks[:5])
            more = f" and {len(tasks)-5} more." if len(tasks) > 5 else ""
            return jsonify({"message": tr(
                                f"You have {len(tasks)} tasks: {preview}{more}",
                                f"Aapke {len(tasks)} tasks hain: {preview}{more}",
                                f"Aapke {len(tasks)} tasks hain: {preview}{more}"
                            ),
                            "continue_listening": False, "task_added": False})

        if intent == "complete_task" and uid and slots.get("task"):
            q = normalize_task_name(slots["task"])
            cand = Task.query.filter(Task.user_id==uid, Task.name.ilike(f"%{q}%"), Task.completed == False).first()
            if cand:
                cand.completed = True; db.session.commit(); _clear_flow()
                return jsonify({"message": tr(f"Marked ‘{cand.name}’ complete.",
                                              f"‘{cand.name}’ complete kar diya.",
                                              f"‘{cand.name}’ complete ho gaya."),
                                "continue_listening": False, "task_added": False, "reload_page": True})

        if intent == "delete_task" and uid and slots.get("task"):
            q = normalize_task_name(slots["task"])
            cand = Task.query.filter(Task.user_id==uid, Task.name.ilike(f"%{q}%")).first()
            if cand:
                title = cand.name
                if cand.parent_id is None:
                    Task.query.filter_by(user_id=uid, parent_id=cand.id).delete(synchronize_session=False)
                db.session.delete(cand); db.session.commit(); _clear_flow()
                return jsonify({"message": tr(f"Deleted ‘{title}’.",
                                              f"‘{title}’ delete kar diya.",
                                              f"‘{title}’ delete ho gaya."),
                                "continue_listening": False, "task_added": False, "reload_page": True})

        if intent == "reschedule" and uid:
            try: days = int(slots.get("days") or 1)
            except Exception: days = 1
            moved = apply_reschedule(uid, days=days)
            return jsonify({"message": tr(
                                f"I moved {moved} task(s) by {days} day(s)." if moved else "No tasks to move.",
                                f"{moved} tasks {days} din ke liye shift kiye." if moved else "Shift karne ko kuch nahi mila.",
                                f"{moved} tasks {days} din ke liye shift kiye." if moved else "Kuch shift karne ko nahi mila."
                            ),
                            "continue_listening": True, "task_added": False, "reload_page": bool(moved)})

        if intent == "decompose" and uid:
            goal = normalize_task_name(slots.get("goal") or "")
            if goal:
                _save_flow({"mode":"decompose","step":"ask_due","payload":{"goal":goal}})
                return jsonify({"message": tr(
                                    f"Okay, breaking down ‘{goal}’. What’s the final due date? Say a date or skip.",
                                    f"Thik hai, ‘{goal}’ todte hain. Final due date kya rakhein? Date bolo ya skip.",
                                    f"Thik hai, ‘{goal}’ todte hain. Final due date kya rakhein? Date bolo ya skip."
                                ),
                                "continue_listening": True, "task_added": False})

        # Flow handlers
        flow = _get_flow(); mode = flow.get("mode"); step = flow.get("step"); task = flow.get("task") or {}

        # RESCHEDULE OFFER confirm
        if mode == "reschedule_offer" and step == "confirm" and uid:
            if any(w in tl for w in ("yes","yeah","yup","sure","ok","okay")):
                days = int(flow.get("payload",{}).get("days",1)); moved = apply_reschedule(uid, days=days); _clear_flow()
                return jsonify({"message": tr(
                                    f"Done. I moved {moved} task(s) to tomorrow. Anything else?" if moved else
                                    "Looks like there are no tasks due today to move.",
                                    f"Ho gaya! {moved} kaam kal ke liye shift kiye. Aur kuch?" if moved else
                                    "Aaj shift karne ke liye koi kaam nahi mila.",
                                    f"Ho gaya! {moved} tasks kal shift kiye. Aur kuch?" if moved else
                                    "Aaj shift karne ko kuch nahi mila."
                                ),
                                "continue_listening": True, "task_added": False, "reload_page": bool(moved)})
            if any(w in tl for w in ("no","nah","nope","cancel")):
                _clear_flow()
                return jsonify({"message": tr("Okay. I’m here if you need anything.",
                                              "Theek hai. Jab zaroorat ho batao.",
                                              "Theek hai. Jab zaroorat ho batao."),
                                "continue_listening": True, "task_added": False})
            return jsonify({"message": tr("Should I move today’s tasks to tomorrow? Say yes or no.",
                                          "Kya aaj ke tasks kal kar du? Haan ya na bolo.",
                                          "Aaj ke tasks kal kar du? Haan ya na bolo."),
                            "continue_listening": True, "task_added": False})

        # ADD FLOW
        if mode == "add":
            if step == "title":
                title_guess = _title_from_transcript(tl) or transcript
                title = (title_guess or "").strip()
                if not title:
                    return jsonify({"message": tr("Say the task name, like ‘buy milk’.",
                                                  "Task ka naam bolo, jaise ‘doodh lena’.",
                                                  "Task ka naam bolo, jaise ‘buy milk’."),
                                    "continue_listening": True, "task_added": False})
                task["name"] = title; flow["step"] = "due"; flow["task"] = task; _save_flow(flow)
                return jsonify({"message": tr(
                                    f"Adding ‘{title}’. When is it due? Say today, tomorrow, a date, or skip.",
                                    f"‘{title}’ add kar raha hoon. Kab tak due? Aaj, kal, koi date, ya skip.",
                                    f"‘{title}’ add kar raha hoon. Kab tak due? Aaj, kal, koi date, ya skip."
                                ),
                                "continue_listening": True, "task_added": False})
            if step == "due":
                task["due_text"] = None if "skip" in tl else transcript
                flow["step"] = "time"; flow["task"] = task; _save_flow(flow)
                return jsonify({"message": tr("What time? Say ‘5 pm’ or say ‘skip’.",
                                              "Kaunsa time? ‘5 baje’ bolo ya ‘skip’.",
                                              "Kaunsa time? ‘5 pm’ bolo ya ‘skip’."),
                                "continue_listening": True, "task_added": False})
            if step == "time":
                task["time_text"] = None if "skip" in tl else transcript
                flow["step"] = "category"; flow["task"] = task; _save_flow(flow)
                return jsonify({"message": tr("Which category? Work, Personal, Study, Health, or say ‘skip’.",
                                              "Kaunsi category? Work, Personal, Study, Health, ya ‘skip’.",
                                              "Kaunsi category? Work, Personal, Study, Health, ya ‘skip’."),
                                "continue_listening": True, "task_added": False})
            if step == "category":
                if "skip" in tl: cat = "Other"
                elif "work" in tl: cat = "Work"
                elif "personal" in tl: cat = "Personal"
                elif "study" in tl or "education" in tl: cat = "Study"
                elif "health" in tl or "fitness" in tl: cat = "Health"
                else: cat = transcript.strip().title() or "Other"
                task["category"] = cat
                parsed_due = parse_due_date(task.get("due_text")) if task.get("due_text") else None
                parsed_time = parse_task_time(task.get("time_text")) if task.get("time_text") else None
                new_task = Task(
                    name=task.get("name"),
                    due_date=parsed_due,
                    task_time=parsed_time,
                    category=task.get("category", "Other"),
                    priority=classify_priority(task.get("name") or ""),
                    reminder_time=(datetime.combine(parsed_due, parsed_time) if (parsed_due and parsed_time) else None),
                    user_id=uid
                )
                db.session.add(new_task); db.session.commit(); _clear_flow()
                return jsonify({"message": tr(f"Task ‘{new_task.name}’ saved.",
                                              f"‘{new_task.name}’ save ho gaya.",
                                              f"‘{new_task.name}’ save ho gaya."),
                                "continue_listening": False, "task_added": True, "reload_page": True})

        # DECOMPOSE FLOW
        if mode == "decompose" and uid:
            if step == "ask_goal":
                goal = transcript.strip()
                if not goal or goal in {"skip","cancel"}:
                    _clear_flow()
                    return jsonify({"message": tr("Okay, cancelled.","Theek hai, cancel.","Theek hai, cancel."),
                                    "continue_listening": False, "task_added": False})
                flow["payload"] = {"goal": goal}; flow["step"] = "ask_due"; _save_flow(flow)
                return jsonify({"message": tr("Got it. What’s the final due date? Say today, tomorrow, a date, or skip.",
                                              "Samajh gaya. Final due date bolo: aaj, kal, koi date, ya skip.",
                                              "Samajh gaya. Final due date bolo: aaj, kal, koi date, ya skip."),
                                "continue_listening": True, "task_added": False})
            if step == "ask_due":
                flow.setdefault("payload", {}); flow["payload"]["due_text"] = None if "skip" in tl else transcript
                flow["step"] = "ask_time"; _save_flow(flow)
                return jsonify({"message": tr("What time should I target? Say a time like ‘5 pm’, or say ‘skip’.",
                                              "Kaunsa time rakhein? ‘5 baje’ bolo ya ‘skip’.",
                                              "Kaunsa time rakhein? ‘5 pm’ bolo ya ‘skip’."),
                                "continue_listening": True, "task_added": False})
            if step == "ask_time":
                payload = flow.get("payload", {}); payload["time_text"] = None if "skip" in tl else transcript
                goal = payload.get("goal", ""); preview = decompose_goal_text(goal)
                names = [s["name"] for s in preview][:5]; more = "" if len(preview) <= 5 else f" and {len(preview)-5} more"
                flow["payload"] = payload; flow["step"] = "confirm"; _save_flow(flow)
                if not names:
                    return jsonify({"message": tr("Couldn’t generate subtasks. Try rephrasing the goal.",
                                                  "Subtasks nahi ban paaye. Thoda aur clear bolo.",
                                                  "Subtasks nahi ban paaye. Thoda aur clear bolo."),
                                    "continue_listening": False, "task_added": False})
                return jsonify({"message": tr(f"I suggest: {', '.join(names)}{more}. Should I create these?",
                                              f"Meri suggestion: {', '.join(names)}{more}. Bana du?",
                                              f"Suggestion: {', '.join(names)}{more}. Bana du?"),
                                "continue_listening": True, "task_added": False})
            if step == "confirm":
                if any(w in tl for w in ("yes","yeah","yup","sure","ok","okay","do it","confirm")):
                    payload = flow.get("payload", {})
                    goal = payload.get("goal", ""); due = parse_due_date(payload.get("due_text") or ""); ttm = parse_task_time(payload.get("time_text") or "")
                    result = create_goal_with_subtasks(uid, goal, due, ttm, category="Other")
                    _clear_flow(); count = result.get("count", 0)
                    if count:
                        return jsonify({"message": tr(f"Done. I created {count} subtasks for ‘{goal}’.",
                                                      f"Ho gaya. ‘{goal}’ ke {count} subtasks bana diye.",
                                                      f"Ho gaya. ‘{goal}’ ke {count} subtasks bana diye."),
                                        "continue_listening": False, "task_added": True, "reload_page": True})
                    return jsonify({"message": tr("I couldn’t create the subtasks.",
                                                  "Subtasks nahi ban paaye.",
                                                  "Subtasks nahi ban paaye."),
                                    "continue_listening": False, "task_added": False})
                if any(w in tl for w in ("no","nah","nope","cancel")):
                    _clear_flow()
                    return jsonify({"message": tr("Cancelled.","Cancel kiya.","Cancel kiya."),
                                    "continue_listening": False, "task_added": False})
                return jsonify({"message": tr("Should I create these subtasks? Say yes or no.",
                                              "Bana du? Haan ya na bolo.",
                                              "Bana du? Haan ya na bolo."),
                                "continue_listening": True, "task_added": False})
        # Unknown → gentle fallback
        if _groq:
            try:
                sys_prompt = (
                    "You are DaySavvy, a friendly, helpful, and gentle productivity assistant. "
                    "You can chat about anything, help with tasks, listen to feelings, and keep the conversation going naturally. "
                    "Reply in a warm, human-like, casual tone. If the user seems stressed or sad, offer encouragement or a reset. "
                    "If they mention tasks or goals, you can help break them down or add them."
                )
                resp = _groq.chat.completions.create(
                    model=os.getenv("GROQ_MODEL", "moonshotai/kimi-k2-instruct-0905"),
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": transcript}
                    ],
                    temperature=0.7,
                    max_tokens=300,
                )
                reply = resp.choices[0].message.content.strip()
                return jsonify({"message": reply, "continue_listening": True, "task_added": False})
            except Exception as e:
                print("[Chat][Groq] error:", e)
        # If Groq not available, fallback to local
        msg = _gen_empathetic_reply_local(transcript, emotion, lang)
        return jsonify({"message": msg, "continue_listening": True, "task_added": False})

    except Exception as e:
        print("[VOICE ERROR]", e)
        return jsonify({"message": tr("Sorry — an error occurred. Try again.",
                                      "Maaf kijiye, kuch gadbad ho gayi. Dobara koshish karein.",
                                      "Sorry, thoda issue aaya. Dubara try karo."),
                        "continue_listening": True, "task_added": False}), 200

# Words used for yes/no checks
YES_WORDS = {"yes", "yeah", "yup", "sure", "correct", "save", "affirmative"}
NO_WORDS = {"no", "nah", "nope", "don't", "dont", "do not", "cancel"}

def _get_flow():
    return session.get("voice_flow", {"mode": None, "step": None, "task": {}})

def _save_flow(flow):
    session["voice_flow"] = flow
    session.modified = True

def _clear_flow():
    session.pop("voice_flow", None)
    session.modified = True

def _title_from_transcript(tl: str):
    """
    Try to extract a short task name from a spoken phrase like:
    "add buy milk tomorrow at 5" -> returns "buy milk"
    If nothing convincing found, return None.
    """
    tl = tl.strip()
    # Find after "add" or "create" or "new task"
    m = re.search(r'\b(?:add|create|new task|i want to add)\b\s*(.+)', tl)
    if not m:
        return None
    candidate = m.group(1).strip()

    # Remove trailing date/time phrases
    candidate = re.sub(r'\b(?:today|tomorrow|tonight|next\s+\w+|in\s+\d+\s+days|on\s+\w+\s*\d{1,2})\b.*$', '', candidate)
    # Remove "at 5 pm" style
    candidate = re.sub(r'\bat\s+\d{1,2}(:\d{2})?\s*(am|pm)?\b', '', candidate)
    candidate = re.sub(r'\b\d{1,2}(:\d{2})\s*(am|pm)?\b', '', candidate)
    candidate = candidate.strip(" ,.")
    return candidate or None

def parse_due_date(text: str):
    """Return a datetime.date or None. Accepts 'today','tomorrow','in N days','next monday','YYYY-MM-DD','DD/MM/YYYY', 'Sep 5' etc."""
    if not text:
        return None
    t = text.lower().strip()

    today = date.today()
    if "today" in t:
        return today
    if "tomorrow" in t:
        return today + timedelta(days=1)

    # in N days
    m = re.search(r'in\s+(\d+)\s+days?', t)
    if m:
        return today + timedelta(days=int(m.group(1)))

    # next <weekday>
    days = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6
    }
    m = re.search(r'next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)', t)
    if m:
        target = days[m.group(1)]
        days_ahead = (target - today.weekday() + 7) % 7
        days_ahead = days_ahead if days_ahead != 0 else 7
        return today + timedelta(days=days_ahead)

    # Try explicit formats (with and without year)
    formats = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%B %d %Y", "%b %d %Y", "%B %d", "%b %d"]
    for fmt in formats:
        try:
            dt = datetime.strptime(t, fmt)
            # if format didn't include year, strptime will fill current year for "%B %d" patterns -> handle:
            if fmt in ("%B %d", "%b %d"):
                return date(date.today().year, dt.month, dt.day)
            return dt.date()
        except Exception:
            continue

    # Last-ditch: search for something like "05/09" -> assume current year, or "5 september"
    m = re.search(r'(\d{1,2})[\/\-\s](\d{1,2})(?:[\/\-\s](\d{2,4}))?', t)
    if m:
        d1 = int(m.group(1)); d2 = int(m.group(2)); y = m.group(3)
        if y:
            y = int(y)
            if y < 100: y += 2000
        else:
            y = today.year
        # guess order: if d1 > 12 treat as day first (d/m), else assume d/m
        if d1 > 12:
            try:
                return date(y, d2, d1)
            except Exception:
                pass
        else:
            try:
                return date(y, d1, d2)
            except Exception:
                pass

    return None

# Voice helpers for decomposition
def _extract_goal_from_transcript(tl: str) -> Optional[str]:
    """
    Try to extract a goal after phrases like:
    'break this down ...', 'break down ...', 'decompose ...',
    'make subtasks for ...', 'split into tasks ...', 'plan for ...'
    """
    tl = tl.strip()
    patterns = [
        r'\bbreak\s+(?:this|it)?\s*down\s*(.+)$',
        r'\bdecompose\s+(.+)$',
        r'\bmake\s+subtasks\s+(?:for\s+)?(.+)$',
        r'\bsplit\s+(?:this|it)?\s*(?:into\s+tasks\s*)?(.+)$',
        r'\bplan\s+(?:for\s+)?(.+)$',
        r'\bprepare\s+plan\s+(?:for\s+)?(.+)$',
    ]
    for p in patterns:
        m = re.search(p, tl)
        if m:
            goal = m.group(1).strip(" .")
            if goal:
                return goal
    return None

def _recent_goal_candidate(uid: int) -> Optional[Task]:
    # Prefer most recent top-level task (no parent), incomplete
    return Task.query.filter(
        Task.user_id == uid,
        Task.parent_id.is_(None)
    ).order_by(Task.created_at.desc()).first()

def parse_task_time(text: str):
    """Return datetime.time or None. Accepts '5 pm', '17:30', '7:00 a.m.', 'noon'."""
    if not text:
        return None
    t = text.lower().strip().replace(".", "")
    if "noon" in t:
        return time(12, 0)
    if "midnight" in t:
        return time(0, 0)

    # Find hh:mm am/pm or hh am/pm or hh:mm (24h)
    m = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', t)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)

    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and hour != 12:
            hour = hour + 12
        if ampm == "am" and hour == 12:
            hour = 0
    else:
        # no am/pm -> treat as 24h if reasonable
        if hour <= 23:
            pass
        else:
            # fallback: invalid hour
            return None

    try:
        return time(hour % 24, minute)
    except Exception:
        return None

def _fmt_date_for_user(d):
    if d is None:
        return "none"
    if isinstance(d, date):
        return d.strftime("%b %d")
    return str(d)

def _fmt_time_for_user(t):
    if t is None:
        return "none"
    if isinstance(t, time):
        return t.strftime("%I:%M %p").lstrip("0") if hasattr(t, 'strftime') else str(t)
    return str(t)

# voice_command route
@app.route("/voice/command", methods=["POST"])
def voice_command():
    try:
        # Parse
        data = request.get_json(force=True, silent=True) or {}
        transcript = (data.get("transcript") or "").strip()
        tl = transcript.lower()
        uid = session.get("user_id")
        prefs = get_voice_prefs()
        lang = prefs.get("lang", "hinglish")

        # Defaults
        emotion = "neutral"
        emo_score = 0.5

        # Cancel / empty
        if any(k in tl for k in ("stop","cancel","exit","quit","bas","ruko")):
            _clear_flow()
            return jsonify({"message": tr("Okay, cancelled.","Theek hai, cancel kiya.","Theek hai, cancel kiya."),
                            "continue_listening": False, "task_added": False})
        if not transcript:
            return jsonify({"message": tr("I didn’t catch that. Say it again?",
                                          "Samajh nahi aaya. Dobara bolein?",
                                          "Samajh nahi aaya. Phir bolo?"),
                            "continue_listening": True, "task_added": False})

        # Emotion (never crash)
        try:
            emotion, emo_score = detect_emotion(transcript)
            if uid:
                log_emotion(uid, emotion, emo_score)
            if uid and not _get_flow().get("mode") and emotion in {"stressed","sad","tired"}:
                todays = propose_reschedule_candidates(uid)
                if todays:
                    _save_flow({"mode":"reschedule_offer","step":"confirm","payload":{"days":1}})
                    return jsonify({"message": tr(
                                        f"{empathetic_prefix(emotion)} Want me to move today’s {len(todays)} task(s) to tomorrow?",
                                        f"{empathetic_prefix(emotion)} Kya aaj ke {len(todays)} kaam kal shift kar du?",
                                        f"{empathetic_prefix(emotion)} Aaj ke {len(todays)} tasks kal shift kar du?"
                                    ),
                                    "continue_listening": True, "task_added": False})
        except Exception as e:
            print("[VOICE emotion]", e)

        # Flow
        flow = _get_flow()
        mode = flow.get("mode")
        step = flow.get("step")
        task = flow.get("task") or {}

        if mode == "reschedule_offer" and step == "confirm" and uid:
            if any(w in tl for w in ("yes","yeah","yup","sure","ok","okay")):
                days = int(flow.get("payload",{}).get("days",1))
                moved = apply_reschedule(uid, days=days)
                _clear_flow()
                return jsonify({"message": tr(
                                    f"Done. I moved {moved} task(s) to tomorrow. Anything else?" if moved else
                                    "Looks like there are no tasks due today to move.",
                                    f"Ho gaya! {moved} kaam kal ke liye shift kiye. Aur kuch?" if moved else
                                    "Aaj shift karne ke liye koi kaam nahi mila.",
                                    f"Ho gaya! {moved} tasks kal shift kiye. Aur kuch?" if moved else
                                    "Aaj shift karne ko kuch nahi mila."
                                ),
                                "continue_listening": True, "task_added": False, "reload_page": bool(moved)})
            if any(w in tl for w in ("no","nah","nope","cancel")):
                _clear_flow()
                return jsonify({"message": tr("Okay. I’m here if you need anything.",
                                              "Theek hai. Jab zaroorat ho batao.",
                                              "Theek hai. Jab zaroorat ho batao."),
                                "continue_listening": True, "task_added": False})
            return jsonify({"message": tr("Should I move today’s tasks to tomorrow? Say yes or no.",
                                          "Kya aaj ke tasks kal kar du? Haan ya na bolo.",
                                          "Aaj ke tasks kal kar du? Haan ya na bolo."),
                            "continue_listening": True, "task_added": False})

        if mode == "add":
            if step == "title":
                title_guess = _title_from_transcript(tl) or transcript
                title = (title_guess or "").strip()
                if not title:
                    return jsonify({"message": tr("Say the task name, like ‘buy milk’.",
                                                  "Task ka naam bolo, jaise ‘doodh lena’.",
                                                  "Task ka naam bolo, jaise ‘buy milk’."),
                                    "continue_listening": True, "task_added": False})
                task["name"] = title
                flow["step"] = "due"
                flow["task"] = task
                _save_flow(flow)
                return jsonify({"message": tr(
                                    f"Adding ‘{title}’. When is it due? Say today, tomorrow, a date, or skip.",
                                    f"‘{title}’ add kar raha hoon. Kab tak due? Aaj, kal, koi date, ya skip.",
                                    f"‘{title}’ add kar raha hoon. Kab tak due? Aaj, kal, koi date, ya skip."
                                ),
                                "continue_listening": True, "task_added": False})
            if step == "due":
                task["due_text"] = None if "skip" in tl else transcript
                flow["step"] = "time"
                flow["task"] = task
                _save_flow(flow)
                return jsonify({"message": tr("What time? Say ‘5 pm’ or say ‘skip’.",
                                              "Kaunsa time? ‘5 baje’ bolo ya ‘skip’.",
                                              "Kaunsa time? ‘5 pm’ bolo ya ‘skip’."),
                                "continue_listening": True, "task_added": False})
            if step == "time":
                task["time_text"] = None if "skip" in tl else transcript
                flow["step"] = "category"
                flow["task"] = task
                _save_flow(flow)
                return jsonify({"message": tr("Which category? Work, Personal, Study, Health, or say ‘skip’.",
                                              "Kaunsi category? Work, Personal, Study, Health, ya ‘skip’.",
                                              "Kaunsi category? Work, Personal, Study, Health, ya ‘skip’."),
                                "continue_listening": True, "task_added": False})
            if step == "category":
                if "skip" in tl: cat = "Other"
                elif "work" in tl: cat = "Work"
                elif "personal" in tl: cat = "Personal"
                elif "study" in tl or "education" in tl: cat = "Study"
                elif "health" in tl or "fitness" in tl: cat = "Health"
                else: cat = transcript.strip().title() or "Other"
                task["category"] = cat
                parsed_due = parse_due_date(task.get("due_text")) if task.get("due_text") else None
                parsed_time = parse_task_time(task.get("time_text")) if task.get("time_text") else None
                new_task = Task(
                    name=task.get("name"),
                    due_date=parsed_due,
                    task_time=parsed_time,
                    category=task.get("category", "Other"),
                    priority=classify_priority(task.get("name") or ""),
                    reminder_time=(datetime.combine(parsed_due, parsed_time) if (parsed_due and parsed_time) else None),
                    user_id=uid
                )
                db.session.add(new_task)
                db.session.commit()
                _clear_flow()
                return jsonify({"message": tr(f"Task ‘{new_task.name}’ saved.",
                                              f"‘{new_task.name}’ save ho gaya.",
                                              f"‘{new_task.name}’ save ho gaya."),
                                "continue_listening": False, "task_added": True, "reload_page": True})

        if mode == "decompose" and uid:
            if step == "ask_goal":
                goal = transcript.strip()
                if not goal or goal in {"skip","cancel"}:
                    _clear_flow()
                    return jsonify({"message": tr("Okay, cancelled.","Theek hai, cancel.","Theek hai, cancel."),
                                    "continue_listening": False, "task_added": False})
                flow["payload"] = {"goal": goal}
                flow["step"] = "ask_due"
                _save_flow(flow)
                return jsonify({"message": tr("Got it. What’s the final due date? Say today, tomorrow, a date, or skip.",
                                              "Samajh gaya. Final due date bolo: aaj, kal, koi date, ya skip.",
                                              "Samajh gaya. Final due date bolo: aaj, kal, koi date, ya skip."),
                                "continue_listening": True, "task_added": False})
            if step == "ask_due":
                flow.setdefault("payload", {})
                flow["payload"]["due_text"] = None if "skip" in tl else transcript
                flow["step"] = "ask_time"
                _save_flow(flow)
                return jsonify({"message": tr("What time should I target? Say a time like ‘5 pm’, or say ‘skip’.",
                                              "Kaunsa time rakhein? ‘5 baje’ bolo ya ‘skip’.",
                                              "Kaunsa time rakhein? ‘5 pm’ bolo ya ‘skip’."),
                                "continue_listening": True, "task_added": False})
            if step == "ask_time":
                payload = flow.get("payload", {})
                payload["time_text"] = None if "skip" in tl else transcript
                goal = payload.get("goal", "")
                preview = decompose_goal_text(goal)
                names = [s["name"] for s in preview][:5]
                more = "" if len(preview) <= 5 else f" and {len(preview)-5} more"
                flow["payload"] = payload
                flow["step"] = "confirm"
                _save_flow(flow)
                if not names:
                    return jsonify({"message": tr("Couldn’t generate subtasks. Try rephrasing the goal.",
                                                  "Subtasks nahi ban paaye. Thoda aur clear bolo.",
                                                  "Subtasks nahi ban paaye. Thoda aur clear bolo."),
                                    "continue_listening": False, "task_added": False})
                return jsonify({"message": tr(f"I suggest: {', '.join(names)}{more}. Should I create these?",
                                              f"Meri suggestion: {', '.join(names)}{more}. Bana du?",
                                              f"Suggestion: {', '.join(names)}{more}. Bana du?"),
                                "continue_listening": True, "task_added": False})
            if step == "confirm":
                if any(w in tl for w in ("yes","yeah","yup","sure","ok","okay","do it","confirm")):
                    payload = flow.get("payload", {})
                    goal = payload.get("goal", "")
                    due = parse_due_date(payload.get("due_text") or "")
                    ttm = parse_task_time(payload.get("time_text") or "")
                    result = create_goal_with_subtasks(uid, goal, due, ttm, category="Other")
                    _clear_flow()
                    count = result.get("count", 0)
                    if count:
                        return jsonify({"message": tr(f"Done. I created {count} subtasks for ‘{goal}’.",
                                                      f"Ho gaya. ‘{goal}’ ke {count} subtasks bana diye.",
                                                      f"Ho gaya. ‘{goal}’ ke {count} subtasks bana diye."),
                                        "continue_listening": False, "task_added": True, "reload_page": True})
                    return jsonify({"message": tr("I couldn’t create the subtasks.",
                                                  "Subtasks nahi ban paaye.",
                                                  "Subtasks nahi ban paaye."),
                                    "continue_listening": False, "task_added": False})
                if any(w in tl for w in ("no","nah","nope","cancel")):
                    _clear_flow()
                    return jsonify({"message": tr("Cancelled.","Cancel kiya.","Cancel kiya."),
                                    "continue_listening": False, "task_added": False})
                return jsonify({"message": tr("Should I create these subtasks? Say yes or no.",
                                              "Bana du? Haan ya na bolo.",
                                              "Bana du? Haan ya na bolo."),
                                "continue_listening": True, "task_added": False})

# Final fallback (always)
        if _groq:
            try:
                sys_prompt = (
                    "You are DaySavvy, a friendly, helpful, and gentle productivity assistant. "
                    "You can chat about anything, help with tasks, listen to feelings, and keep the conversation going naturally. "
                    "Reply in a warm, human-like, casual tone. If the user seems stressed or sad, offer encouragement or a reset. "
                    "If they mention tasks or goals, you can help break them down or add them."
                )
                resp = _groq.chat.completions.create(
                    model=os.getenv("GROQ_MODEL", "moonshotai/kimi-k2-instruct-0905"),
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": transcript}
                    ],
                    temperature=0.7,
                    max_tokens=300,
                )
                reply = resp.choices[0].message.content.strip()
                return jsonify({"message": reply, "continue_listening": True, "task_added": False})
            except Exception as e:
                print("[Chat][Groq] error:", e)
        # If Groq not available, fallback to local
        msg = _gen_empathetic_reply_local(transcript, emotion, lang)
        return jsonify({"message": msg, "continue_listening": True, "task_added": False})

    except Exception as e:
        print("[VOICE ERROR]", e)
        return jsonify({"message": tr("Sorry — an error occurred. Try again.",
                                      "Maaf kijiye, kuch gadbad ho gayi. Dobara koshish karein.",
                                      "Sorry, thoda issue aaya. Dubara try karo."),
                        "continue_listening": True, "task_added": False}), 200

# ---- Voice prefs (lang + gender) in session ----
def get_voice_prefs():
    return {
        "lang": session.get("voice_lang", "hinglish"),   # 'en' | 'hi' | 'hinglish'
        "gender": session.get("voice_gender", "female")  # 'male' | 'female'
    }

@app.route("/voice/prefs", methods=["GET", "POST"])
def voice_prefs():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        lang = (data.get("lang") or session.get("voice_lang","hinglish")).lower()
        gender = (data.get("gender") or session.get("voice_gender","female")).lower()
        if lang in ("en","hi","hinglish"): session["voice_lang"] = lang
        if gender in ("male","female"): session["voice_gender"] = gender
        session.modified = True
        return jsonify({"ok": True, **get_voice_prefs()})
    return jsonify(get_voice_prefs())

# Lightweight translator for a couple of strings (optional)
def tr(en: str, hi: str = None, hi_en: str = None) -> str:
    lang = session.get("voice_lang","hinglish")
    if lang == "hi" and hi: return hi
    if lang == "hinglish" and hi_en: return hi_en
    return en

def log_emotion(user_id: Optional[int], emotion: str, score: float) -> None:
    try:
        ev = EmotionEvent(user_id=user_id, emotion=emotion, score=score)
        db.session.add(ev)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print("[EmotionLog] failed:", e)

def propose_reschedule_candidates(uid: int):
    """Return list of today's due, incomplete tasks for this user."""
    today = date.today()
    return Task.query.filter(
        Task.user_id == uid,
        Task.completed == False,
        Task.due_date == today
   ).order_by(
        (Task.task_time.is_(None)).asc(),  # non-null first (SQLite-safe)
        Task.task_time.asc(),
        Task.id.asc()
    ).all()
def apply_reschedule(uid: int, days: int = 1) -> int:
    """Shift today's due, incomplete tasks by N days. Recompute reminder_time."""
    candidates = propose_reschedule_candidates(uid)
    moved = 0
    for t in candidates:
        try:
            new_due = t.due_date + timedelta(days=days) if t.due_date else None
            t.due_date = new_due
            if new_due and t.task_time:
                t.reminder_time = datetime.combine(new_due, t.task_time)
            else:
                t.reminder_time = None
            moved += 1
        except Exception:
            continue
    if moved:
        db.session.commit()
    return moved

def empathetic_prefix(emotion: str) -> str:
    return {
        "stressed": "You sound a bit stressed.",
        "sad": "I’m picking up that you’re feeling low.",
        "tired": "You seem tired.",
        "positive": "Love the energy!",
        "neutral": ""
    }.get(emotion, "")

# Favicon / Tab icon
@app.route('/favicon.ico')
def favicon():
    """
    Serves favicon if present under static/favicon_ico/favicon-32x32.png
    This was in your original code; keep the same path or update as needed.
    """
    fp = os.path.join(app.root_path, 'static', 'favicon_ico')
    return send_from_directory(fp, 'favicon-32x32.png', mimetype='image/png')

# Run server
if __name__ == "__main__":
    print("DaySavvy consolidated app starting up...")
    print("Voice commands available at: POST /voice/command (JSON: {'transcript': '...'})")
    print("Main interface at: http://127.0.0.1:5000/")
    # Ensure DB created
    with app.app_context():
        db.create_all()
        threading.Thread(target=reminder_checker, daemon=True).start()
    app.run(debug=True, host='0.0.0.0', port=5000)
