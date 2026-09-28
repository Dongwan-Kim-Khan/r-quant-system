import os
import sys
import json
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

import al_sangmoo_daily_bot
import db_manager

ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
FEED_FILE = os.path.join(PROJECT_ROOT, "dashboard_data.json")

def load_env():
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip("\"'")

def test_send():
    load_env()
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    receiver = os.environ.get("ALERT_EMAIL_RECEIVER", "user@example.com")
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    print("=" * 65)
    print("  R-SANGMOO QUANT PLATFORM: EMAIL DISPATCH TEST SUITE")
    print("=" * 65)
    print(f"  - Sender   : {gmail_user}")
    print(f"  - Receiver : {receiver}")
    
    if not gmail_user or not gmail_pass:
        print("\n[ERROR] GMAIL_USER or GMAIL_APP_PASSWORD not set in .env file.")
        return
        
    # Load live feed data if available
    feed_data = {}
    if os.path.exists(FEED_FILE):
        try:
            with open(FEED_FILE, "r", encoding="utf-8") as f:
                feed_data = json.load(f)
        except Exception:
            pass
            
    dual_consensus = feed_data.get("tier1", feed_data.get("dual_consensus", []))
    strat1_exclusive = feed_data.get("tier2", feed_data.get("strat1_exclusive", []))
    strat2_exclusive = feed_data.get("tier3", feed_data.get("strat2_exclusive", []))
    
    portfolio_alerts = al_sangmoo_daily_bot.evaluate_user_portfolio_positions()
    health_status = "전수 포워드 트래킹 정상 가동 중 (17년 퀀트 프레임워크)"
    
    # Generate 3-Column Tactical Email
    html_content = al_sangmoo_daily_bot.generate_email_content(
        today_str=today_str,
        dual_consensus=dual_consensus,
        strat1_exclusive=strat1_exclusive,
        strat2_exclusive=strat2_exclusive,
        portfolio_alerts=portfolio_alerts,
        health_status=health_status,
        feed_data=feed_data
    )
    
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[R-Sangmoo Quant Briefing] {today_str} Tactical 3-Column Quant Report"
        msg["From"] = f"R-Sangmoo Quant Engine <{gmail_user}>"
        msg["To"] = receiver
        
        msg.attach(MIMEText(html_content, "html", "utf-8"))
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, receiver, msg.as_string())
            
        print(f"\n[SUCCESS] Modernized 3-Column Tactical Email successfully sent to {receiver}!")
        print("  - Dual Consensus Candidates :", len(dual_consensus))
        print("  - Strategy 1 Candidates    :", len(strat1_exclusive))
        print("  - Strategy 2 Candidates    :", len(strat2_exclusive))
        print("  - Portfolio Alerts         :", len(portfolio_alerts))
        print("=" * 65)
    except Exception as e:
        print(f"\n[FAILURE] Email dispatch failed: {e}")

if __name__ == "__main__":
    test_send()
