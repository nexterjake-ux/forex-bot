import io
import json
import os
import sys
import time
import requests
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
elif hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

load_dotenv()

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
CHAT_ID    = os.environ.get("CHAT_ID", "")
TIMEZONE   = os.environ.get("TIMEZONE", "Asia/Seoul")

PENDLE_API = "https://api-v2.pendle.finance/core/v1/1/markets?limit=100"
STATE_FILE = "pendle_state.json"
INTERVAL   = 300  # 5분

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36'
}

MARKETS = [
    {
        'name':    'PT-apyUSD 27 Aug 2026',
        'address': '0x30bb9ee8dc6aab322dc3a0d36063cbf06a9e5952',
        'days':    76,
        'median':  17.62,
    },
    {
        'name':    'PT-apyUSD 05 Nov 2026',
        'address': '0xc5f938a8ef5f3bf9e72f5aa094baf5e03f4727d3',
        'days':    146,
        'median':  16.37,
    },
]


# ── 구간 판단 ────────────────────────────────────────────────────────────────

def get_grade(diff):
    if diff >= 1.0:   return '🟢🟢 매수 강추'
    if diff >= 0.7:   return '🟢  매수 추천'
    if diff >= 0.4:   return '🟡  매수 고려'
    if diff >= -0.2:  return '⚪  매수 관망'
    if diff >= -0.5:  return '🟠  매도 고려'
    if diff >= -1.0:  return '🔴  매도 추천'
    return               '🔴🔴 매도 강추'


# ── 메시지 조립 ──────────────────────────────────────────────────────────────

def build_change_message(market, prev_apy, curr_apy):
    change = curr_apy - prev_apy
    diff   = curr_apy - market['median']
    grade  = get_grade(diff)
    now    = datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M')
    return (
        f'📊 PT-apyUSD {market["days"]}일 변동  ({now})\n'
        f'{prev_apy:.2f}% → {curr_apy:.2f}% ({change:+.2f}%)\n'
        f'━━━━━━━━━━━━━━\n'
        f'중앙값: {market["median"]:.2f}%\n'
        f'현재 차이: {diff:+.2f}%\n'
        f'━━━━━━━━━━━━━━\n'
        f'{grade} 구간'
    )


def build_status_message(market, curr_apy):
    diff  = curr_apy - market['median']
    grade = get_grade(diff)
    now   = datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M')
    return (
        f'📊 PT-apyUSD {market["days"]}일 현재 상태  ({now})\n'
        f'현재: {curr_apy:.2f}%\n'
        f'━━━━━━━━━━━━━━\n'
        f'중앙값: {market["median"]:.2f}%\n'
        f'현재 차이: {diff:+.2f}%\n'
        f'━━━━━━━━━━━━━━\n'
        f'{grade} 구간'
    )


# ── 텔레그램 전송 ────────────────────────────────────────────────────────────

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print('[경고] BOT_TOKEN 또는 CHAT_ID 없음')
        return False
    try:
        payload = {'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}
        resp = requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json; charset=utf-8'},
            timeout=10
        )
        if resp.status_code == 200:
            print('  [전송] 성공')
            return True
        print(f'  [실패] {resp.status_code}: {resp.text[:80]}')
        return False
    except Exception as e:
        print(f'  [오류] {e}')
        return False


# ── 상태 관리 ────────────────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── APY 조회 ─────────────────────────────────────────────────────────────────

def fetch_apy_map():
    resp = requests.get(PENDLE_API, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return {
        m['address'].lower(): m.get('impliedApy')
        for m in resp.json().get('results', [])
    }


# ── 메인 체크 루프 ───────────────────────────────────────────────────────────

def check(force_send=False):
    now = datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M:%S')
    print(f'[{now}] APY 체크 중...')

    try:
        apy_map = fetch_apy_map()
    except Exception as e:
        print(f'  [오류] API 조회 실패: {e}')
        return

    state = load_state()

    for m in MARKETS:
        addr = m['address'].lower()
        raw  = apy_map.get(addr)
        if raw is None:
            print(f'  {m["name"]}: 조회 실패')
            continue

        curr = round(float(raw) * 100, 4)
        prev = state.get(addr)

        diff_from_median = curr - m['median']
        print(f'  {m["name"]}: {curr:.4f}%  '
              f'(중앙값 대비 {diff_from_median:+.2f}%)', end='')

        if prev is None or force_send:
            # 최초 실행 또는 강제 발송 → 현재 상태 메시지
            msg = build_status_message(m, curr)
            print(f'  → 상태 발송')
            print(f'\n{msg}\n')
            send_telegram(msg)
            state[addr] = curr
        else:
            change = curr - prev
            print(f'  변동: {change:+.4f}%')
            if abs(change) >= 0.1:
                msg = build_change_message(m, prev, curr)
                print(f'\n{msg}\n')
                send_telegram(msg)
                state[addr] = curr
            # 0.1% 미만 변동 → 상태만 업데이트, 발송 안 함

    save_state(state)


# ── 진입점 ───────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('[시작] Pendle APY 알람 (5분마다 체크, 0.1% 변동 시 발송)')
    print(f'       대상: {", ".join(m["name"] for m in MARKETS)}\n')

    first = True
    try:
        while True:
            check(force_send=first)
            first = False
            print(f'[대기] {INTERVAL // 60}분 후 재체크...\n')
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print('\n[종료] Pendle 알람을 종료합니다.')
