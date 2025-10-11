# DAYSAVVY

DaySavvy is a fast, voice‑enabled task manager that helps you capture, organize, and complete work with zero friction. Built with Flask, SQLAlchemy, and Jinja, it provides secure user accounts with per‑user task isolation, lightning‑quick CRUD, due dates and times with automatic server‑side reminders, smart category badges and auto‑priority from task intent, powerful search and status filters, and a clean, responsive UI. Speak or type: the AI voice assistant can add, list, complete, and delete tasks using natural language. A session‑scoped REST API enables integrations, while CSRF protection and sane defaults keep things safe. Runs anywhere with SQLite by default and can scale to production databases via DATABASE_URL.


## Features

- Accounts & Security
  - Register, Login, Logout (session-based)
  - Per‑user task isolation (each user sees only their tasks)
  - CSRF‑protected forms and secure session handling

- Fast Task Management
  - Quick add, Edit, Complete, Delete (CRUD) with instant feedback
  - Due date and time support
  - Automatic priority classification (Urgent / High / Normal / Low) from task text
  - Categories with badges (Work, Personal, Study, Other)

- Smart Views
  - Quick search by task name (q parameter)
  - Filters: Incomplete, Completed, Overdue
  - Sorted lists (newest first) with clear separation of sections

- Reminders
  - Auto reminder_time = due_date + time
  - Lightweight background checker (every minute) prints “[REMINDER] …” to server console

- Voice Assistant
  - Natural voice/text commands via POST /voice/command
  - Add, list, complete, and delete tasks by phrase
  - Optional TTS feedback (gTTS + pygame), gracefully degrades to console

- REST API (session‑based)
  - GET /api/tasks, POST /api/tasks, PUT /api/tasks/<id>, DELETE /api/tasks/<id>
  - JSON responses with priority, due date/time, reminder_time

- UX & Polish
  - Responsive, mobile‑friendly layout
  - Flash messages for success/warnings/errors
  - Favicon served at /favicon.ico
  - No‑cache headers to always see fresh data
  - Friendly 404 handling for missing resources

- Dev & Infra
  - SQLite out of the box; DATABASE_URL supported for other DBs
  - Flask‑Migrate ready (migrations capable)
  - Clean project structure and Windows‑friendly run scripts

## Tech Stack

- Python 3.11+
- Flask, Jinja2
- Flask‑WTF (forms + CSRF)
- Flask‑SQLAlchemy (ORM)
- Flask‑Migrate
- Flask‑CORS
- Optional: OpenAI, gTTS, pygame
  

## Project Structure (simplified)

```
DAYSAVVY/
├─ app.py
├─ templates/
│  ├─ index.html
│  ├─ login.html
│  └─ register.html
├─ static/
│  ├─ favicon.ico            (recommended) OR
│  └─ favicon_ico/favicon-32x32.png
├─ DAYSAVVY.db               (created at runtime; SQLite)
└─ README.md
```

## Security & Privacy

- CSRF‑protected forms, session security
- SQLite local by default; bring your own DB via DATABASE_URL
- Report issues: arhammaliksg@gmail.com


## How to Run Locally

1. Clone the repository:  
   ```bash
   git clone https://github.com/arhammalik21/DAYSAVVY.git
