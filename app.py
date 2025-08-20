# Import necessary modules
from flask import Flask, render_template, redirect, url_for, flash, abort, request, jsonify
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, DateField, SelectField
from wtforms.validators import DataRequired
from datetime import date
import openai
from gtts import gTTS
import pygame
import io
import json

# ----- Initialize Flask and CSRF protection -----
app = Flask(__name__)
app.config["SECRET_KEY"] = "arham0564"
csrf = CSRFProtect(app)

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
# Add this in your app.py top-level (simple in-memory session)
conversation_state = {"next_expected": None, "pending_task": None}

from datetime import date, timedelta, datetime
import re
from flask import jsonify, request

@csrf.exempt
@app.route("/voice/command", methods=["POST"])
def voice_command():
    data = request.get_json() or {}
    transcript = (data.get("transcript") or "").strip()
    tl = transcript.lower()

    global next_id, conversation_state

    def respond(message, success=False, reload=False, next_expected=None):
        return jsonify({
            "message": message,
            "success": success,
            "reload": reload,
            "next_expected": next_expected
        })

    # Follow-up flow
    pending = conversation_state.get("pending_task")
    expecting = conversation_state.get("next_expected")

    # 1) Expecting due date
    if expecting == "due_date" and pending:
        parsed_due = None
        try:
            if "today" in tl:
                parsed_due = date.today()
            elif "tomorrow" in tl:
                parsed_due = date.today() + timedelta(days=1)
            elif "in " in tl and " day" in tl:
                m = re.search(r"in\s+(\d+)\s+day", tl)
                if m:
                    n = int(m.group(1))
                    parsed_due = date.today() + timedelta(days=n)
            else:
                # Try YYYY-MM-DD
                parsed_due = datetime.strptime(transcript, "%Y-%m-%d").date()
        except Exception:
            parsed_due = None

        if parsed_due:
            pending["due_date"] = parsed_due
            conversation_state["next_expected"] = "category"
            return respond(
                f"Due date set to {parsed_due.isoformat()}. What category should I use? For example: Work, Personal, Study, Other.",
                success=False,
                reload=True,
                next_expected="category"
            )
        else:
            return respond(
                "I couldn't parse the date. Say 'tomorrow', 'in 3 days', or a date like 2025-08-25.",
                success=False,
                reload=False,
                next_expected="due_date"
            )

    # 2) Expecting category
    if expecting == "category" and pending:
        cat = None
        for c in ["work", "personal", "study", "other", "health", "shopping", "finance", "school"]:
            if c in tl:
                cat = c.capitalize()
                break
        if not cat:
            return respond(
                "I didn't catch a category. Try: Work, Personal, Study, Other, Health, Shopping, Finance, or School.",
                success=False,
                reload=False,
                next_expected="category"
            )

        pending["category"] = cat
        conversation_state = {"next_expected": None, "pending_task": None}
        return respond(
            f"Category set to {cat}. All set!",
            success=False,
            reload=True,
            next_expected=None
        )

    # New commands
    # ADD
    if any(w in tl for w in ["add ", "create ", "new "]):
        base = None
        for w in ["add", "create", "new"]:
            if w in tl:
                parts = tl.split(w, 1)
                if len(parts) > 1:
                    base = parts[1].strip()
                    break
        if not base:
            return respond("I heard add, but not the task name. Say: add buy milk.", False, False, None)

        clean = (
            base.replace("task", "")
                .replace("to my list", "")
                .replace("in my list", "")
                .strip(" .")
        )
        if not clean:
            return respond("What should I add? Please say the task name.", False, False, None)

        new_task = {
            "id": next_id,
            "name": clean,
            "completed": False,
            "due_date": None,
            "category": "Other"
        }
        tasks.append(new_task)
        next_id += 1

        conversation_state = {"next_expected": "due_date", "pending_task": new_task}
        return respond(
            f"Got it! I added '{clean}'. Do you want to set a due date? You can say 'tomorrow', 'in 3 days', or 2025-08-25.",
            success=True,
            reload=True,
            next_expected="due_date"
        )

    # DELETE
    if any(w in tl for w in ["delete", "remove"]):
        target = None
        for w in ["delete", "remove"]:
            if w in tl:
                parts = tl.split(w, 1)
                if len(parts) > 1:
                    target = parts[1].strip()
                    break
        if not target:
            return respond("What should I delete? Say: delete gym workout.", False, False, None)

        target = (
            target.replace("task", "")
                  .replace("from my list", "")
                  .replace("in my task", "")
                  .strip(" .")
        )
        for i, t in enumerate(tasks):
            if target.lower() in t["name"].lower():
                removed = tasks.pop(i)
                # Clear pending flow if it referenced removed task
                if conversation_state.get("pending_task") and conversation_state["pending_task"]["id"] == removed["id"]:
                    conversation_state = {"next_expected": None, "pending_task": None}
                return respond(f"Deleted '{removed['name']}'.", True, True, None)

        return respond(f"I couldn't find a task containing '{target}'.", False, False, None)

    # COMPLETE
    if any(w in tl for w in ["complete", "done", "finish", "mark as done"]):
        m = re.search(r"task\s*(\d+)", tl)
        if m:
            tid = int(m.group(1))
            t = next((x for x in tasks if x["id"] == tid), None)
            if t:
                t["completed"] = True
                return respond(f"Marked task {tid} as complete: '{t['name']}'.", True, True, None)
            return respond(f"I couldn't find task {tid}.", False, False, None)

        for t in tasks:
            if not t["completed"]:
                t["completed"] = True
                return respond(f"Completed '{t['name']}'.", True, True, None)

        return respond("Looks like everything is already complete.", False, False, None)

    # STATUS
    if any(w in tl for w in ["what's on my list", "what is on my list", "read my tasks", "show tasks", "what do i need to do"]):
        inc = [t for t in tasks if not t["completed"]]
        if not tasks:
            return respond("Your list is empty. Want me to add something?", False, False, None)
        if not inc:
            return respond("All tasks are complete. Nice work!", False, False, None)

        titles = ", ".join([f"{t['id']}: {t['name']}" for t in inc[:5]])
        more = "" if len(inc) <= 5 else f", and {len(inc)-5} more"
        return respond(f"You have {len(inc)} tasks: {titles}{more}.", False, False, None)

    # DEFAULT
    return respond(f"I heard '{transcript}'. Try: add buy milk; delete gym; complete task 1.", False, False, None)


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
