import io, json, os, sys, time
import requests
from datetime import datetime
import pytz
from dotenv import load_dotenv

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
elif hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

load_dotenv()

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
CHAT_ID   = os.environ.get('CHAT_ID', '')
TIMEZONE  = os.environ.get('TIMEZONE', 'Asia/Seoul')

PENDLE_API = 'https://api-v2.pendle.finance/core/v1/1/markets?limit=100'
STATE_FILE = 'pendle_state.json'
INTERVAL   = 300  # 5분

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

MARKETS = [
    {'name': 'PT-apyUSD 27 Aug 2026', 'address': '0x30bb9ee8dc6aab322dc3a0d36063cbf06a9e5952',
     'days': 76,  'median': 17.62},
    {'name': 'PT-apyUSD 05 Nov 2026', 'address': '0xc5f938a8ef5f3bf9e72f5aa094baf5e03f4727d3',
     'days': 146, 'median': 16.37},
]


# ── 판단 등급 ─────────────────────────────────────────────────────────────────

def get_grade(apy, median):
    diff = apy - median
    if diff >=  1.2:  return '🟢🟢🟢 매수 강추'
    if diff >=  0.8:  return '🟢🟢 매수 추천'
    if diff >=  0.4:  return '🟢 매수 고려'
    if diff >= -0.4:  return '⚪ 관망'
    if diff >  -0.6:  return '🟠 매도 근접'
    return                   '🔴 매도 검토'


# ── 데이터 수집 ───────────────────────────────────────────────────────────────

def fetch_apy_map():
    resp = requests.get(PENDLE_API, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return {m['address'].lower(): m.get('impliedApy')
            for m in resp.json().get('results', [])}


def fetch_apyusd_price():
    resp = requests.get(
        'https://api.coingecko.com/api/v3/simple/price',
        params={'ids': 'apyusd', 'vs_currencies': 'usd'},
        headers=HEADERS, timeout=10,
    )
    resp.raise_for_status()
    return float(resp.json()['apyusd']['usd'])


# ── 메시지 조립 ───────────────────────────────────────────────────────────────

def build_change_msg(market, prev_apy, curr_apy, apyusd):
    change  = curr_apy - prev_apy
    diff    = curr_apy - market['median']
    grade   = get_grade(curr_apy, market['median'])
    apy_str = f'${apyusd:.2f}' if apyusd is not None else '조회 실패'
    return (
        f'📊 PT-apyUSD {market["days"]}일\n'
        f'APY: {prev_apy:.2f}% → {curr_apy:.2f}% ({change:+.2f}%)\n'
        f'중앙값: {market["median"]:.2f}% (차이 {diff:+.2f}%)\n'
        f'apyUSD: {apy_str}\n'
        f'━━━━━━━━━━━━━━\n'
        f'{grade} 구간'
    )


def build_status_msg(market, curr_apy, apyusd):
    diff    = curr_apy - market['median']
    grade   = get_grade(curr_apy, market['median'])
    apy_str = f'${apyusd:.2f}' if apyusd is not None else '조회 실패'
    now     = datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M')
    return (
        f'📊 PT-apyUSD {market["days"]}일  현재 상태  ({now})\n'
        f'APY: {curr_apy:.2f}%\n'
        f'중앙값: {market["median"]:.2f}% (차이 {diff:+.2f}%)\n'
        f'apyUSD: {apy_str}\n'
        f'━━━━━━━━━━━━━━\n'
        f'{grade} 구간'
    )


# ── 텔레그램 전송 ─────────────────────────────────────────────────────────────

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
            timeout=10,
        )
        if resp.status_code == 200:
            print('  [전송] 성공')
            return True
        print(f'  [실패] {resp.status_code}: {resp.text[:80]}')
        return False
    except Exception as e:
        print(f'  [오류] {e}')
        return False


# ── 상태 관리 ─────────────────────────────────────────────────────────────────

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


# ── 메인 체크 ─────────────────────────────────────────────────────────────────

def check(force_send=False):
    now_str = datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M:%S')
    print(f'[{now_str}] APY 체크 중...')

    try:
        apy_map = fetch_apy_map()
    except Exception as e:
        print(f'  [오류] Pendle API 실패: {e}')
        return

    apyusd = None
    try:
        apyusd = fetch_apyusd_price()
        print(f'  apyUSD: ${apyusd:.4f}')
    except Exception as e:
        print(f'  [경고] apyUSD 조회 실패: {e}')

    state = load_state()
    apy_log = ', '.join(f'{k[:8]}..={v:.4f}%'
                        for k, v in state.items()
                        if isinstance(v, float) and k.startswith('0x'))
    print(f'  [상태] {apy_log or "없음 (초기 실행)"}')

    for m in MARKETS:
        addr = m['address'].lower()
        raw  = apy_map.get(addr)
        if raw is None:
            print(f'  PT-apyUSD {m["days"]}일: 조회 실패')
            continue

        curr = round(float(raw) * 100, 4)
        prev = state.get(addr)
        diff = curr - m['median']
        print(f'  PT-apyUSD {m["days"]}일: {curr:.4f}%  ({diff:+.2f}% vs 중앙값)', end='')

        if prev is None or force_send:
            msg = build_status_msg(m, curr, apyusd)
            print(f'  → 상태 발송')
            print(f'\n{msg}\n')
            send_telegram(msg)
            state[addr] = curr
        else:
            change = curr - prev
            print(f'  변동: {change:+.4f}% (기준: {prev:.4f}%)')
            if abs(change) >= 0.1:
                msg = build_change_msg(m, prev, curr, apyusd)
                print(f'\n{msg}\n')
                send_telegram(msg)
                state[addr] = curr
            else:
                print(f'  → 임계값 미달, 기준값 유지')

    save_state(state)
    print(f'  [상태] 저장 완료: {STATE_FILE}')


# ── 진입점 ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('[시작] Pendle APY 알람봇 (5분마다 체크, 0.1% 변동 시 발송)')
    print(f'       대상: {", ".join(m["name"] for m in MARKETS)}\n')
    first = True
    try:
        while True:
            check(force_send=first)
            first = False
            print(f'[대기] {INTERVAL // 60}분 후 재체크...\n')
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print('\n[종료] Pendle 알람봇을 종료합니다.')
