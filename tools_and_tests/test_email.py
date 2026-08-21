import os
import sys
import smtplib
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
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")

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
    receiver = os.environ.get("ALERT_EMAIL_RECEIVER", "kdw58170425@gmail.com")
    
    print(f"📧 [테스트] 발신자: {gmail_user}")
    print(f"📧 [테스트] 수신자: {receiver}")
    
    if not gmail_user or not gmail_pass:
        print("❌ [.env 오류] GMAIL_USER 또는 GMAIL_APP_PASSWORD가 .env 파일에 비어 있습니다.")
        print("👉 해결 방법: 구글 계정 보안에서 '앱 비밀번호(16자리)'를 발급받아 .env 파일의 GMAIL_APP_PASSWORD= 에 입력해주세요.")
        return
        
    try:
        msg = MIMEMultipart()
        msg["Subject"] = "🧪 [알상무 퀀트] 이메일 발송 연동 테스트 성공!"
        msg["From"] = f"알상무 퀀트 봇 <{gmail_user}>"
        msg["To"] = receiver
        
        body = """
        <h2>🎉 알상무 퀀트 모닝 브리핑 이메일 연동 성공!</h2>
        <p>축하합니다. 이메일 SMTP 발송 설정이 정상적으로 완료되었습니다.</p>
        <p>이제 매일 아침 8시 30분에 <strong>2+2+2 자동 추천 6종목 및 보유 종목 매도/익절 알림</strong>이 본 메일함으로 자동 전송됩니다.</p>
        """
        msg.attach(MIMEText(body, "html", "utf-8"))
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, receiver, msg.as_string())
            
        print(f"✅ [성공] {receiver}에게 테스트 메일을 성공적으로 발송했습니다! 메일함을 확인해주세요.")
    except Exception as e:
        print(f"❌ [발송 실패] 오류 내용: {e}")

if __name__ == "__main__":
    test_send()
