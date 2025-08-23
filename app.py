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

# AI Voice command
from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_cors import CORS
from flask_wtf import FlaskForm
from wtforms import StringField, DateField, SelectField, SubmitField
from wtforms.validators import DataRequired
from datetime import date, timedelta, datetime

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'arham0564'

from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_cors import CORS
from flask_wtf import FlaskForm
from wtforms import StringField, DateField, SelectField, SubmitField
from wtforms.validators import DataRequired
from datetime import date, timedelta, datetime

# Initialize Flask app
app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'daysavvy-secret-key-2025-change-in-production'

# Global variables
tasks = []
next_id = 1
STOP_WORDS = {"stop", "done", "cancel", "exit", "quit", "thanks"}

class TaskForm(FlaskForm):
    task = StringField('Task Name', validators=[DataRequired()])
    due_date = DateField('Due Date')
    category = SelectField('Category', choices=[
        ('Other', 'Other'),
        ('Work', 'Work'),
        ('Personal', 'Personal'),
        ('Study', 'Study'),
        ('Health', 'Health')
    ])
    submit = SubmitField('Add Task')

def get_formatted_tasks():
    """Helper function to format tasks for template"""
    filtered = []
    for task in tasks:
        task_copy = task.copy()
        if task_copy.get('due_date') and isinstance(task_copy['due_date'], str):
            try:
                # Convert to datetime for template
                task_copy['due_date'] = datetime.strptime(task_copy['due_date'], '%Y-%m-%d')
            except ValueError:
                pass
        filtered.append(task_copy)
    return filtered

@app.route("/", methods=["GET", "POST"])
def index():
    """Main route that displays the task list"""
    form = TaskForm()
    
    # Handle web form submission
    if form.validate_on_submit():
        global next_id
        new_task = {
            "id": next_id,
            "name": form.task.data,
            "completed": False,
            "due_date": form.due_date.data.strftime('%Y-%m-%d') if form.due_date.data else None,
            "category": form.category.data
        }
        tasks.append(new_task)
        next_id += 1
        return redirect(url_for('index'))
    
    return render_template("index.html", 
                         tasks=get_formatted_tasks(), 
                         form=form,
                         current_date=datetime.combine(date.today(), datetime.min.time()))

# Web Interface Routes
@app.route("/complete/<int:task_id>", methods=["POST"])
def complete_task(task_id):
    """Complete a task via web interface"""
    for task in tasks:
        if task["id"] == task_id:
            task["completed"] = True
            break
    return redirect(url_for('index'))

@app.route("/delete/<int:task_id>", methods=["POST"])  
def delete_task(task_id):
    """Delete a task via web interface"""
    global tasks
    tasks = [task for task in tasks if task["id"] != task_id]
    return redirect(url_for('index'))

@app.route("/toggle/<int:task_id>", methods=["POST"])
def toggle_task(task_id):
    """Toggle task completion status"""
    for task in tasks:
        if task["id"] == task_id:
            task["completed"] = not task["completed"]
            break
    return redirect(url_for('index'))

@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
def edit_task(task_id):
    """Edit a task"""
    task_to_edit = None
    for task in tasks:
        if task["id"] == task_id:
            task_to_edit = task
            break
    
    if not task_to_edit:
        return redirect(url_for('index'))
    
    form = TaskForm()
    
    if form.validate_on_submit():
        task_to_edit["name"] = form.task.data
        task_to_edit["due_date"] = form.due_date.data.strftime('%Y-%m-%d') if form.due_date.data else None
        task_to_edit["category"] = form.category.data
        return redirect(url_for('index'))
    
    # Pre-populate form with current task data
    form.task.data = task_to_edit["name"]
    if task_to_edit.get("due_date"):
        try:
            form.due_date.data = datetime.strptime(task_to_edit["due_date"], '%Y-%m-%d').date()
        except ValueError:
            pass
    form.category.data = task_to_edit.get("category", "Other")
    
    return render_template("edit_task.html", form=form, task=task_to_edit)

# Voice Command Route - FIXED VERSION
@app.route("/voice/command", methods=["POST"])
def voice_command():
    """Handle voice commands for task management with continuous conversation"""
    global tasks, next_id
    
    try:
        data = request.get_json() or {}
        transcript = data.get("transcript", "").strip()
        
        if not transcript:
            return jsonify({
                "message": "No speech detected. Please try again.", 
                "continue_listening": True
            })
        
        tl = transcript.lower().strip()
        print(f"[VOICE] Received: '{transcript}'")
        
        # Stop command
        if any(word in tl for word in STOP_WORDS):
            session.pop('conversation_state', None)
            return jsonify({
                "message": "Voice control stopped. Goodbye!", 
                "continue_listening": False
            })
        
        # Get conversation state
        conversation_state = session.get('conversation_state', {
            "next_expected": None, 
            "pending_task": None
        })
        pending = conversation_state.get("pending_task")
        expecting = conversation_state.get("next_expected")
        
        print(f"[DEBUG] Expecting: {expecting}, Pending: {bool(pending)}")
        
        # Handle due date follow-up
        if expecting == "due_date" and pending:
            due_date = None
            
            if "today" in tl:
                due_date = date.today()
            elif "tomorrow" in tl:
                due_date = date.today() + timedelta(days=1)
            elif "skip" in tl or "no" in tl:
                conversation_state["next_expected"] = "category"
                session['conversation_state'] = conversation_state
                session.modified = True
                return jsonify({
                    "message": "No due date set. What category? Say Work, Personal, Study, Health, or Other.",
                    "continue_listening": True
                })
            
            if due_date:
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
                    "message": "Please say 'today', 'tomorrow', or 'skip' for due date.",
                    "continue_listening": True
                })
        
        # Handle category follow-up
        if expecting == "category" and pending:
            category = "Other"
            
            if "work" in tl:
                category = "Work"
            elif "personal" in tl:
                category = "Personal"
            elif "study" in tl or "education" in tl:
                category = "Study"
            elif "health" in tl or "fitness" in tl:
                category = "Health"
            
            for task in tasks:
                if task["id"] == pending["id"]:
                    task["category"] = category
                    break
            
            session.pop('conversation_state', None)
            session.modified = True
            
            return jsonify({
                "message": f"Perfect! Task '{pending['name']}' added with category {category}. What would you like to do next?",
                "continue_listening": True,
                "reload_page": True
            })
        
        # Handle add task - FIXED BUG HERE
        if "add" in tl or "create" in tl:
            task_name = ""
            if "add" in tl:
                parts = tl.split("add", 1)
                if len(parts) > 1:
                    task_name = parts[1].replace("task", "").strip()
            elif "create" in tl:
                parts = tl.split("create", 1)
                if len(parts) > 1:
                    task_name = parts[1].replace("task", "").strip()  # FIXED: was parts[2]
            
            if task_name:
                new_task = {
                    "id": next_id,
                    "name": task_name.title(),
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
                    "message": f"Adding '{task_name.title()}'. When is this due? Say 'today', 'tomorrow', or 'skip'.",
                    "continue_listening": True,
                    "reload_page": True
                })
            else:
                return jsonify({
                    "message": "What task should I add? Try saying 'add buy groceries'.",
                    "continue_listening": True
                })
        
        # Handle complete task
        if "complete" in tl or "finish" in tl:
            if not tasks:
                return jsonify({
                    "message": "You have no tasks to complete.",
                    "continue_listening": True
                })
            
            # Find task by name
            completed_task = None
            for task in tasks:
                if not task['completed'] and task['name'].lower() in tl:
                    task['completed'] = True
                    completed_task = task
                    break
            
            if completed_task:
                return jsonify({
                    "message": f"Excellent! Task '{completed_task['name']}' marked as complete!",
                    "continue_listening": True,
                    "reload_page": True
                })
            else:
                incomplete_tasks = [task for task in tasks if not task['completed']]
                if incomplete_tasks:
                    task_names = [task['name'] for task in incomplete_tasks[:3]]
                    return jsonify({
                        "message": f"Which task should I complete? You have: {', '.join(task_names)}",
                        "continue_listening": True
                    })
        
        # Handle list tasks
        if "list" in tl or "show" in tl:
            if not tasks:
                return jsonify({
                    "message": "You have no tasks yet.",
                    "continue_listening": True
                })
            
            incomplete_tasks = [task for task in tasks if not task['completed']]
            if not incomplete_tasks:
                return jsonify({
                    "message": "All tasks are complete! Great job!",
                    "continue_listening": True
                })
            
            task_names = [task['name'] for task in incomplete_tasks[:3]]
            message = f"You have {len(incomplete_tasks)} tasks: " + ", ".join(task_names)
            if len(incomplete_tasks) > 3:
                message += f" and {len(incomplete_tasks) - 3} more."
            
            return jsonify({
                "message": message,
                "continue_listening": True
            })
        
        # Handle skip during conversation
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
        
        # Default response
        return jsonify({
            "message": f"I heard: '{transcript}'. Try: 'add [task]', 'complete [task]', 'list tasks', or 'stop'.",
            "continue_listening": True
        })
    
    except Exception as e:
        print(f"[ERROR] Voice command error: {str(e)}")
        return jsonify({
            "message": "Sorry, I encountered an error. Please try again.", 
            "continue_listening": True
        }), 500

# API Routes
@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    return jsonify(tasks)

@app.route("/api/tasks", methods=["POST"])
def add_task_api():
    global next_id
    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({"error": "Task name is required"}), 400
    
    new_task = {
        "id": next_id,
        "name": data['name'],
        "completed": False,
        "due_date": data.get('due_date'),
        "category": data.get('category', 'Other')
    }
    tasks.append(new_task)
    next_id += 1
    return jsonify(new_task), 201

@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
def update_task_api(task_id):
    data = request.get_json()
    for task in tasks:
        if task["id"] == task_id:
            task.update({k: v for k, v in data.items() if k in task})
            return jsonify(task)
    return jsonify({"error": "Task not found"}), 404

@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def delete_task_api(task_id):
    global tasks
    original_length = len(tasks)
    tasks = [task for task in tasks if task["id"] != task_id]
    if len(tasks) < original_length:
        return jsonify({"message": "Task deleted"})
    return jsonify({"error": "Task not found"}), 404

if __name__ == '__main__':
    print("Starting DaySavvy Flask Application...")
    print("Voice commands available at: /voice/command")
    print("Main interface at: http://localhost:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)


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
