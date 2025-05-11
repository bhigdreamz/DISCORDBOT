# keep_alive.py - A robust web server for keeping a Discord bot alive
import os
from flask import Flask, jsonify
from threading import Thread
import time
from datetime import datetime

# Initialize
app = Flask('')
start_time = datetime.now()
bot_status = {"status": "initializing", "user": None, "war_id": None}

@app.route('/')
def home():
    """Return a simple status message for uptime monitors"""
    uptime = str(datetime.now() - start_time).split('.')[0]  # Remove microseconds
    return f"Discord Bot is running! Uptime: {uptime}"

@app.route('/status')
def status():
    """Return detailed status for monitoring"""
    uptime = str(datetime.now() - start_time).split('.')[0]  # Remove microseconds
    return jsonify({
        "status": bot_status["status"],
        "uptime": uptime,
        "bot_name": bot_status["user"],
        "war_tracking": bool(bot_status["war_id"]),
        "war_id": bot_status["war_id"],
        "last_check": str(datetime.now()),
        "repl_owner": os.environ.get('REPL_OWNER', 'unknown'),
        "repl_slug": os.environ.get('REPL_SLUG', 'unknown')
    })

def run():
    """Run the Flask app for the web server"""
    try:
        # Use port 8080 for Replit
        app.run(host='0.0.0.0', port=8080, threaded=True)
    except Exception as e:
        print(f"Web server error: {e}")
        # If the web server crashes, try to restart it after a delay
        time.sleep(60)
        Thread(target=run).start()

def update_status(status="online", user=None, war_id=None):
    """Update the status information shown on the web server"""
    bot_status["status"] = status
    if user:
        bot_status["user"] = user
    if war_id:
        bot_status["war_id"] = war_id

def keep_alive(bot_user=None, current_war_id=None):
    """Start the web server in a separate thread and handle errors"""
    # Update status with initial data
    update_status("online", bot_user, current_war_id)
    
    # Start the web server
    print("\n=== STARTING KEEP-ALIVE SERVER ===")
    server_thread = Thread(target=run, daemon=True)
    server_thread.start()
    
    # Determine the URL for the Replit web server
    repl_owner = os.environ.get('REPL_OWNER', 'your-replit-username')
    repl_slug = os.environ.get('REPL_SLUG', 'your-repl-name')
    web_url = f"https://{repl_slug}.{repl_owner}.repl.co"
    
    print(f"✅ Web server started!")
    print(f"✅ Access it at: {web_url}")
    
    # Print instructions for uptime monitoring
    print("\n=== 24/7 UPTIME INSTRUCTIONS ===")
    print("To keep your bot running 24/7:")
    print("1. Create a free account at https://uptimerobot.com")
    print("2. Add a new HTTP(s) monitor with these settings:")
    print(f"   - URL: {web_url}")
    print("   - Friendly Name: Discord Bot")
    print("   - Monitoring Interval: 5 minutes")
    print("3. Save the monitor and it will ping your bot every 5 minutes")
    print("4. Your bot should now stay online indefinitely!\n")
    
    return web_url