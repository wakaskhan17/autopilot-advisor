from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import datetime
import requests
import json
import os
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import schedule
import time
import threading
import sqlite3
import pytz
from queue import Queue

app = Flask(__name__)

# Configuration
DEEPSEEK_API_KEY = "sk-585abb6a00a34486a5b4f2d0bd312ec7"  # Replace with your DeepSeek API key
SHEET_ID = "1LsFDFqEGw8L0T8yclrUCYa9LSG1F_qOMgY9Wv26Qgpo"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly"
]
TWILIO_ACCOUNT_SID = os.getenv  # Replace with your Twilio Account SID
TWILIO_AUTH_TOKEN = os.getenv  # Replace with your Twilio Auth Token
TWILIO_SANDBOX_NUMBER = "whatsapp:+14155238886"
USER_NUMBER = "whatsapp:+447456142055"  # Replace with your WhatsApp number

# User goals
USER_GOALS = """
1. Become a Full Stack Developer (Top Priority)
2. Learn SAP
3. Grow in Project/Service Management
4. Learn Smart Contract Programming
5. Teach kids computer skills
"""

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect('/tmp/autopilot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS habits
                 (timestamp TEXT, user_number TEXT, habit_type TEXT, count INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tasks
                 (timestamp TEXT, user_number TEXT, task TEXT, completed BOOLEAN)''')
    c.execute('''CREATE TABLE IF NOT EXISTS focus_log
                 (timestamp TEXT, activity TEXT, success BOOLEAN)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_settings
                 (user_number TEXT PRIMARY KEY, timezone TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS webhook_log
                 (timestamp TEXT, user_number TEXT, request_body TEXT, error TEXT)''')
    c.execute("INSERT OR REPLACE INTO user_settings (user_number, timezone) VALUES (?, ?)",
              (USER_NUMBER, 'Asia/Karachi'))
    conn.commit()
    conn.close()

init_db()

# User habits
user_habits = {
    'last_message': '',
    'last_message_type': '',
    'last_productive_time': datetime.datetime.now(pytz.timezone('Asia/Karachi')),
    'procrastination': 0,
    'mobile_addiction': 0,
    'poor_personal_care': 0,
    'overthinking': 0
}

# Twilio client
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Message queue for async processing
message_queue = Queue()

def get_user_timezone(user_number):
    conn = sqlite3.connect('/tmp/autopilot.db')
    c = conn.cursor()
    c.execute("SELECT timezone FROM user_settings WHERE user_number = ?", (user_number,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 'Asia/Karachi'

def set_user_timezone(user_number, timezone):
    try:
        pytz.timezone(timezone)
        conn = sqlite3.connect('/tmp/autopilot.db')
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO user_settings (user_number, timezone) VALUES (?, ?)",
                  (user_number, timezone))
        conn.commit()
        conn.close()
        return True
    except pytz.exceptions.UnknownTimeZoneError:
        return False

def log_webhook_request(user_number, request_body, error=None):
    conn = sqlite3.connect('/tmp/autopilot.db')
    c = conn.cursor()
    user_timezone = get_user_timezone(user_number)
    c.execute("INSERT INTO webhook_log VALUES (?, ?, ?, ?)",
              (datetime.datetime.now(pytz.timezone(user_timezone)).strftime("%Y-%m-%d %H:%M:%S"),
               user_number, str(request_body), str(error)))
    conn.commit()
    conn.close()

def send_whatsapp_message(to_number, message):
    twilio_client.messages.create(
        body=message,
        from_=TWILIO_SANDBOX_NUMBER,
        to=to_number
    )

@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    try:
        incoming_msg = request.values.get('Body', '').lower()
        user_number = request.values.get('From', '')
        log_webhook_request(user_number, request.values)
        message_queue.put((incoming_msg, user_number))
        resp = MessagingResponse()
        resp.message("Processing...")  # Immediate response
        return Response(str(resp), status=200, mimetype='application/xml')
    except Exception as e:
        log_webhook_request(user_number, request.values, str(e))
        return Response(status=200)
@app.route("/", methods=['GET'])
def ping():
    return "OK", 200

def process_messages():
    while True:
        incoming_msg, user_number = message_queue.get()
        response_msg = process_message(incoming_msg, user_number)
        send_whatsapp_message(user_number, response_msg)
        message_queue.task_done()

def log_to_sheet(user_number, message, response):
    try:
        creds = Credentials.from_service_account_info(json.loads(os.getenv('GOOGLE_CREDENTIALS')), scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).sheet1
        user_timezone = get_user_timezone(user_number)
        sheet.append_row([
            datetime.datetime.now(pytz.timezone(user_timezone)).strftime("%Y-%m-%d %H:%M:%S"),
            user_number,
            message,
            response
        ])
    except Exception as e:
        print(f"❌ Sheets error: {e}")

def log_habit_to_db(user_number, habit_type, count):
    conn = sqlite3.connect('/tmp/autopilot.db')
    c = conn.cursor()
    user_timezone = get_user_timezone(user_number)
    c.execute("INSERT INTO habits VALUES (?, ?, ?, ?)",
              (datetime.datetime.now(pytz.timezone(user_timezone)).strftime("%Y-%m-%d %H:%M:%S"), user_number, habit_type, count))
    conn.commit()
    conn.close()

def log_task_to_db(user_number, task, completed=False):
    conn = sqlite3.connect('/tmp/autopilot.db')
    c = conn.cursor()
    user_timezone = get_user_timezone(user_number)
    c.execute("INSERT INTO tasks VALUES (?, ?, ?, ?)",
              (datetime.datetime.now(pytz.timezone(user_timezone)).strftime("%Y-%m-%d %H:%M:%S"), user_number, task, completed))
    conn.commit()
    conn.close()

def log_focus_time(activity, success, user_number):
    conn = sqlite3.connect('/tmp/autopilot.db')
    c = conn.cursor()
    user_timezone = get_user_timezone(user_number)
    c.execute("INSERT INTO focus_log VALUES (?, ?, ?)",
              (datetime.datetime.now(pytz.timezone(user_timezone)).strftime("%Y-%m-%d %H:%M:%S"), activity, success))
    conn.commit()
    conn.close()

def process_message(incoming_msg, user_number):
    global user_habits
    if incoming_msg.startswith("set timezone"):
        timezone = incoming_msg.replace("set timezone", "").strip()
        if set_user_timezone(user_number, timezone):
            response = f"Timezone set to {timezone}. Timestamps will now use this timezone."
        else:
            response = "Invalid timezone. Please use a valid timezone (e.g., 'Asia/Dubai', 'America/New_York')."
    elif user_habits.get('last_message_type') == 'followup':
        response = generate_followup_analysis(incoming_msg, user_number)
        user_habits['last_message_type'] = ''
    elif "focus mode" in incoming_msg:
        response = "🚀 Focus mode activated for 60 minutes! I'll mute notifications and block distractions."
    elif "check progress" in incoming_msg:
        response = generate_progress_report(user_number)
    elif "log mood" in incoming_msg:
        response = "How are you feeling today? (1-5 scale, 5=best)\nReply with 'mood 3' for example"
    elif "mood " in incoming_msg:
        mood = incoming_msg.split(' ')[1]
        response = f"Thanks for sharing! I've logged your mood as {mood}/5. Remember: progress isn't linear."
        log_to_sheet(user_number, incoming_msg, f"Mood: {mood}/5")
    elif "reset" in incoming_msg:
        for habit in ['procrastination', 'mobile_addiction', 'poor_personal_care', 'overthinking']:
            user_habits[habit] = 0
            log_habit_to_db(user_number, habit, 0)
        response = "🔄 Mental reset complete! Fresh start activated. You've got this!"
    else:
        response = generate_ai_response(incoming_msg)
    
    log_to_sheet(user_number, incoming_msg, response)
    update_habit_tracking(incoming_msg, user_number)
    user_habits['last_message'] = response
    return response

def generate_ai_response(user_input):
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    prompt = f"""User goals: {USER_GOALS}
    User message: {user_input}
    Detected habits: {user_habits}
    As an AI coach, provide:
    1. Brief analysis of current behavior
    2. Gentle correction if needed (e.g., for procrastination: 'Let’s pause scrolling and try a goal.')
    3. Suggested next action
    4. Motivational quote
    Keep response under 3 sentences."""
    data = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    try:
        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers=headers,
            json=data
        )
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"Error processing AI response: {str(e)}"

def generate_followup_analysis(activity, user_number):
    prompt = f"""User reported activity: {activity}
    User goals: {USER_GOALS}
    Analyze if this aligns with goals. Detect if off-track (e.g., scrolling, idle) or productive.
    Suggest a corrective action if needed."""
    response = generate_ai_response(prompt)
    log_focus_time(activity, 'productive' not in response.lower(), user_number)
    return response

def generate_progress_report(user_number):
    conn = sqlite3.connect('/tmp/autopilot.db')
    c = conn.cursor()
    user_timezone = get_user_timezone(user_number)
    c.execute("SELECT * FROM tasks WHERE timestamp >= ?", 
              ((datetime.datetime.now(pytz.timezone(user_timezone)) - datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"),))
    tasks = c.fetchall()
    completed = len([t for t in tasks if t[3]])
    total = len(tasks)
    completion_rate = (completed / total * 100) if total > 0 else 0
    c.execute("SELECT habit_type, count FROM habits WHERE timestamp >= ?",
              ((datetime.datetime.now(pytz.timezone(user_timezone)) - datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"),))
    habits = c.fetchall()
    conn.close()
    habit_summary = "\n".join([f"- {h[0]}: {h[1]} times" for h in habits])
    return f"📊 Weekly Progress:\n- Tasks completed: {completion_rate:.1f}% ({completed}/{total})\n- Habits:\n{habit_summary}\nKeep pushing forward!"

def update_habit_tracking(message, user_number):
    habits = {
        'procrastination': ['scroll', 'watching', 'waste', 'procrastinat'],
        'mobile_addiction': ['phone', 'youtube', 'social media'],
        'poor_personal_care': ['tired', 'skip meal', 'no sleep'],
        'overthinking': ['worry', 'overwhelm', 'stress']
    }
    for habit, keywords in habits.items():
        if any(keyword in message.lower() for keyword in keywords):
            user_habits[habit] += 1
            log_habit_to_db(user_number, habit, user_habits[habit])

def create_calendar_event(task, start_time, duration_hours=1, user_number=USER_NUMBER):
    creds = Credentials.from_service_account_info(json.loads(os.getenv('GOOGLE_CREDENTIALS')), scopes=SCOPES)
    service = build('calendar', 'v3', credentials=creds)
    user_timezone = get_user_timezone(user_number)
    event = {
        'summary': task,
        'start': {'dateTime': start_time.astimezone(pytz.timezone(user_timezone)).isoformat(), 'timeZone': user_timezone},
        'end': {'dateTime': (start_time + datetime.timedelta(hours=duration_hours)).astimezone(pytz.timezone(user_timezone)).isoformat(), 'timeZone': user_timezone}
    }
    service.events().insert(calendarId='primary', body=event).execute()

def check_goal_related_emails():
    creds = Credentials.from_service_account_info(json.loads(os.getenv('GOOGLE_CREDENTIALS')), scopes=SCOPES)
    service = build('gmail', 'v1', credentials=creds)
    results = service.users().messages().list(userId='me', q='from:* SAP training coding').execute()
    messages = results.get('messages', [])
    return [f"Found email: {msg['id']}" for msg in messages]

def nightly_checkin():
    message = "🌙 Nightly check-in: What did you accomplish today? Reply with tasks or activities."
    send_whatsapp_message(USER_NUMBER, message)
    user_habits['last_message_type'] = 'nightly'

def morning_prioritization():
    to_do_list = generate_daily_todo_list()
    message = f"☀️ Good morning! Here's your to-do list:\n{to_do_list}\nWhich 3 things are top for you today?"
    send_whatsapp_message(USER_NUMBER, message)
    user_habits['last_message_type'] = 'morning'

def two_hour_followup():
    message = "⏰ What are you doing now? Reply with your current activity."
    send_whatsapp_message(USER_NUMBER, message)
    user_habits['last_message_type'] = 'followup'

def weekly_review():
    creds = Credentials.from_service_account_info(json.loads(os.getenv('GOOGLE_CREDENTIALS')), scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).sheet1
    records = sheet.get_all_records()
    user_timezone = get_user_timezone(USER_NUMBER)
    week_data = [r for r in records if (datetime.datetime.now(pytz.timezone(user_timezone)) - datetime.datetime.strptime(r['Timestamp'], "%Y-%m-%d %H:%M:%S")).days <= 7]
    prompt = f"""Analyze this week's data: {json.dumps(week_data)}
    User goals: {USER_GOALS}
    Provide:
    1. Progress across goals
    2. Habit performance
    3. Suggestions for next week
    4. Mood trend (if available)"""
    analysis = generate_ai_response(prompt)
    send_whatsapp_message(USER_NUMBER, f"📅 Weekly Review:\n{analysis}")

def generate_daily_todo_list():
    conn = sqlite3.connect('/tmp/autopilot.db')
    c = conn.cursor()
    user_timezone = get_user_timezone(USER_NUMBER)
    c.execute("SELECT * FROM focus_log WHERE timestamp >= ?",
              ((datetime.datetime.now(pytz.timezone(user_timezone)) - datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"),))
    focus_data = c.fetchall()
    conn.close()
    prompt = f"""User goals: {USER_GOALS}
    Focus data: {json.dumps(focus_data)}
    Generate a daily to-do list with 5 tasks aligned with these goals. Prioritize based on past performance."""
    tasks = generate_ai_response(prompt).split('\n')[:5]
    start_time = datetime.datetime.now(pytz.timezone(user_timezone)).replace(hour=9, minute=0, second=0)
    for i, task in enumerate(tasks):
        create_calendar_event(task, start_time + datetime.timedelta(hours=i*2), user_number=USER_NUMBER)
        log_task_to_db(USER_NUMBER, task)
    return "\n".join(tasks)

# Schedule tasks
schedule.every().day.at("21:00").do(nightly_checkin)
schedule.every().day.at("08:00").do(morning_prioritization)
schedule.every(2).hours.do(two_hour_followup)
schedule.every().sunday.at("20:00").do(weekly_review)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    message_processor = threading.Thread(target=process_messages)
    message_processor.daemon = True
    message_processor.start()
    app.run(host='0.0.0.0', port=5000)