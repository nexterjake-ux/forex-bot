from config import BOT_TOKEN, CHAT_ID
import requests

text = "JDBOS 환율봇 테스트 메시지입니다"
url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

payload = {
    'chat_id': CHAT_ID,
    'text': text
}

try:
    resp = requests.post(url, json=payload, timeout=10)
    print('status_code:', resp.status_code)
    print('response:', resp.text)
except Exception as e:
    print('error:', e)
