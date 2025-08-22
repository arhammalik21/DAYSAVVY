# Import necessary modules
from flask import Flask, render_template, redirect, url_for, flash, abort, request, jsonify
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, DateField, SelectField
from wtforms.validators import DataRequired
from datetime import date
from datetime import date, timedelta, datetime
import re
import openai
from gtts import gTTS
import pygame
import io
import json

# ----- Initialize Flask and CSRF protection -----
app = Flask(__name__)
app.config["SECRET_KEY"] = "arham0564"
csrf = CSRFProtect(app)

#TAB icon
from flask import send_from_directory
import os

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static/favicon_io'),
                               'favicon-32x32.png', mimetype='image/png')





# ----- Load Whisper model and AI Assistant -----
print("Loading Whisper model...")

class AIAssistant:
    def __init__(self):
          import os
          OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
          print(OPENAI_API_KEY)
    
    def get_ai_response(self, user_input, current_tasks):
        """Get intelligent response from GPT"""
        tasks_summary = [f"ID {t['id']}: {t['name']}" for t in current_tasks if not t['completed']]
        
        system_prompt = f"""You are a helpful task management assistant. 
        Current pending tasks: {tasks_summary}
        
        User can:
        - Add tasks: respond with {{"action": "add", "task": "task name", "message": "Added task successfully!"}}
        - Complete tasks: respond with {{"action": "complete", "id": task_id, "message": "Task completed!"}}
        - Delete tasks: respond with {{"action": "delete", "id": task_id, "message": "Task deleted!"}}
        - Chat: respond with {{"action": "chat", "message": "your helpful response"}}
        
        Always respond in JSON format. Be conversational and helpful."""
        
        try:
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input}
                ],
                max_tokens=150,
                temperature=0.7
            )
            
            return json.loads(response.choices[0].message.content)
        except:
            return {"action": "chat", "message": "Sorry, I couldn't understand that. Please try again."}
    
    def speak_response(self, text):
        """Convert text to speech and play it"""
        try:
            tts = gTTS(text=text, lang='en', slow=False)
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            fp.seek(0)
            
            pygame.mixer.init()
            pygame.mixer.music.load(fp)
            pygame.mixer.music.play()
            
            # Wait for audio to finish
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
                
        except Exception as e:
            print(f"Text-to-speech error: {e}")

# Create AI assistant instance
ai_assistant = AIAssistant()

def find_task_by_id(task_id):
    return next((t for t in tasks if t["id"] == task_id), None)

# ----- AI Voice Command Route -----
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from datetime import date, timedelta, datetime
import re

app = Flask(__name__)

@app.template_filter('safe_date')
def safe_date(value):
    if not value:
        return ''
    # If it's already a string, return as-is
    if isinstance(value, str):
        return value
    # If it's a datetime object, format it
    try:
        return value.strftime('%b %d, %Y')
    except Exception:
        return str(value)


# Enable CORS - this fixes the connection errors
CORS(app)

# Add secret key for CSRF (newer Flask requirement)  
app.config['SECRET_KEY'] = 'your-secret-key-here'

# Your existing variables
tasks = []
next_id = 1

# Voice assistant globals
conversation_state = {"next_expected": None, "pending_task": None}
STOP_WORDS = {"stop", "done", "cancel", "exit", "quit", "thanks"}

# Your existing routes stay the same...

from flask import session
from flask import request, jsonify, session
from datetime import date, timedelta

# Ensure STOP_WORDS is defined elsewhere in your app
# STOP_WORDS = ["stop", "cancel", "exit", "quit"]

@app.route("/voice/command", methods=["POST"])
def voice_command():
    global tasks, next_id

    try:
        data = request.get_json() or {}
        transcript = data.get("transcript", "").strip()

        if not transcript:
            return jsonify({"message": "No speech detected.", "continue_listening": True})

        tl = transcript.lower().strip()

        # Stop command
        if any(word in tl for word in STOP_WORDS):
            session.pop('conversation_state', None)
            session.modified = True
            return jsonify({"message": "Voice control stopped.", "continue_listening": False})

        # Get conversation state
        conversation_state = session.get('conversation_state', {"next_expected": None, "pending_task": None})
        pending = conversation_state.get("pending_task")
        expecting = conversation_state.get("next_expected")

        # Due date follow-up
        if expecting == "due_date" and pending:
            due_date = None
            if "today" in tl:
                due_date = date.today()
            elif "tomorrow" in tl:
                due_date = date.today() + timedelta(days=1)
            elif "skip" in tl:
                conversation_state["next_expected"] = "category"
                session['conversation_state'] = conversation_state
                session.modified = True
                return jsonify({
                    "message": "No due date set. What category? Say Work, Personal, Study, Health, or Other.",
                    "continue_listening": True
                })

            if due_date:
                # Update task - store as string to avoid Jinja2 error
                for task in tasks:
                    if task["id"] == pending["id"]:
                        task["due_date"] = due_date.strftime('%Y-%m-%d')
                        break

                conversation_state["next_expected"] = "category"
                session['conversation_state'] = conversation_state
                session.modified = True

                return jsonify({
                    "message": f"Due date set to {due_date.strftime('%B %d')}. What category? Say Work, Personal, Study, Health, or Other.",
                    "continue_listening": True,
                    "reload_page": True
                })
            else:
                return jsonify({
                    "message": "Say 'today', 'tomorrow', or 'skip' for due date.",
                    "continue_listening": True
                })

        # Category follow-up
        if expecting == "category" and pending:
            category = "Other"
            if "work" in tl:
                category = "Work"
            elif "personal" in tl:
                category = "Personal"
            elif "study" in tl:
                category = "Study"
            elif "health" in tl:
                category = "Health"

            # Update task
            for task in tasks:
                if task["id"] == pending["id"]:
                    task["category"] = category
                    break

            # Clear session
            session.pop('conversation_state', None)
            session.modified = True

            return jsonify({
                "message": f"Task '{pending['name']}' added with category {category}. What's next?",
                "continue_listening": True,
                "reload_page": True
            })

        # Add task
        if "add" in tl:
            parts = transcript.split("add", 1)
            if len(parts) > 1:
                task_name = parts[1].replace("task", "").strip()
                if task_name:
                    new_task = {
                        "id": next_id,
                        "name": task_name,
                        "completed": False,
                        "due_date": None,
                        "category": "Other"
                    }
                    tasks.append(new_task)
                    next_id += 1

                    session['conversation_state'] = {
                        "next_expected": "due_date",
                        "pending_task": new_task
                    }
                    session.modified = True

                    return jsonify({
                        "message": f"Adding '{task_name}'. Set due date? Say 'today', 'tomorrow', or 'skip'.",
                        "continue_listening": True,
                        "reload_page": True
                    })
            
            return jsonify({
                "message": "What should I add? Try saying 'add buy groceries'.",
                "continue_listening": True
            })

        # Skip follow-up
        if "skip" in tl and expecting:
            if expecting == "due_date":
                conversation_state["next_expected"] = "category"
                session['conversation_state'] = conversation_state
                session.modified = True
                return jsonify({
                    "message": "No due date. What category? Say Work, Personal, Study, Health, or Other.",
                    "continue_listening": True
                })
            elif expecting == "category":
                session.pop('conversation_state', None)
                session.modified = True
                return jsonify({
                    "message": "Category kept as Other. What's next?",
                    "continue_listening": True,
                    "reload_page": True
                })

        return jsonify({
            "message": f"I heard: '{transcript}'. Try: add task, complete task, list tasks, or stop.",
            "continue_listening": True
        })

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return jsonify({"message": "Server error occurred.", "continue_listening": True}), 500




# ----- WTForms: Task form definition -----
class TaskForm(FlaskForm):
    task = StringField('Task', validators=[DataRequired()])
    due_date = DateField('Due Date', format='%Y-%m-%d', validators=[], default=None)
    category = SelectField('Category', choices=[
        ('Work', 'Work'),
        ('Personal', 'Personal'),
        ('Study', 'Study'),
        ('Other', 'Other')
    ])

# ----- In-memory store -----
tasks = []
next_id = 1

# ----- Home: add task (POST), search/filter (GET), and render list -----
@app.route("/", methods=["GET", "POST"])
def index():
    form = TaskForm()
    global next_id

    # Handle task creation on POST
    if form.validate_on_submit():
        tasks.append({
            "id": next_id,
            "name": form.task.data.strip(),
            "completed": False,
            "due_date": form.due_date.data,
            "category": form.category.data
        })
        next_id += 1
        flash("Task added!", "success")
        return redirect(url_for("index"))

    # Read search and filter parameters on GET
    q = request.args.get("q", "").lower()
    status = request.args.get("status", "")

    # Build filtered list
    filtered = []
    for t in tasks:
        if q and q not in t["name"].lower():
            continue

        if status == "incomplete" and t["completed"]:
            continue

        if status == "completed" and not t["completed"]:
            continue

        if status == "overdue":
            if t["completed"] or not t["due_date"] or t["due_date"] >= date.today():
                continue

        filtered.append(t)

    return render_template("index.html", tasks=filtered, form=form, current_date=date.today())

# ----- Edit task -----
@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
def edit_task(task_id):
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        abort(404)

    form = TaskForm(obj=task)
    if request.method == "POST" and form.validate_on_submit():
        task["name"] = form.task.data.strip()
        task["due_date"] = form.due_date.data
        task["category"] = form.category.data
        flash("Task updated!", "info")
        return redirect(url_for("index"))

    return render_template("edit.html", form=form, task=task)

# ----- Mark task as complete -----
@app.route("/complete/<int:task_id>")
def complete_task(task_id):
    for task in tasks:
        if task["id"] == task_id:
            task["completed"] = True
            flash("Task marked as complete!", "info")
            break
    else:
        abort(404)

    return redirect(url_for("index"))

# ----- Delete task -----
@app.route("/delete/<int:task_id>")
def delete_task(task_id):
    global tasks
    updated = [t for t in tasks if t["id"] != task_id]
    if len(updated) == len(tasks):
        abort(404)

    tasks = updated
    flash("Task deleted.", "warning")
    return redirect(url_for("index"))

# ----- Entry point -----
if __name__ == "__main__":
    print("AI Task Manager starting up...")
    app.run(debug=True)
