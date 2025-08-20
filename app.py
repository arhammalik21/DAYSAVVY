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
@csrf.exempt
@app.route("/voice/command", methods=["POST"])
def voice_command():
    try:
        data = request.get_json()
        transcript = data.get('transcript', '').strip()
        
        if not transcript:
            return jsonify({"message": "No speech detected", "success": False})
        
        global next_id
        transcript_lower = transcript.lower()
        
        # ADD commands
        if any(word in transcript_lower for word in ['add', 'create', 'new']):
            for word in ['add', 'create', 'new']:
                if word in transcript_lower:
                    parts = transcript_lower.split(word, 1)
                    if len(parts) > 1:
                        task_name = parts[1].strip()
                        # Clean up common words
                        task_name = task_name.replace('task', '').replace('to my list', '').strip()
                        if task_name:
                            tasks.append({
                                "id": next_id,
                                "name": task_name,
                                "completed": False,
                                "due_date": None,
                                "category": "Other"
                            })
                            next_id += 1
                            return jsonify({"message": f"Added: {task_name}", "success": True})
        
        # DELETE commands - this was missing!
        elif any(word in transcript_lower for word in ['delete', 'remove']):
            # Extract what to delete
            for word in ['delete', 'remove']:
                if word in transcript_lower:
                    parts = transcript_lower.split(word, 1)
                    if len(parts) > 1:
                        target = parts[1].strip()
                        target = target.replace('task', '').replace('from my list', '').replace('in my task', '').strip()
                        
                        # Find task by name
                        for i, task in enumerate(tasks):
                            if target.lower() in task['name'].lower():
                                removed_task = tasks.pop(i)
                                return jsonify({"message": f"Deleted: {removed_task['name']}", "success": True})
                        
                        return jsonify({"message": f"Couldn't find task containing '{target}'", "success": False})
        
        # COMPLETE commands
        elif any(word in transcript_lower for word in ['complete', 'done', 'finish']):
            for task in tasks:
                if not task['completed']:
                    task['completed'] = True
                    return jsonify({"message": f"Completed: {task['name']}", "success": True})
            return jsonify({"message": "No tasks to complete", "success": False})
        
        return jsonify({"message": f"Heard '{transcript}' but couldn't understand the command", "success": False})
        
    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500



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
