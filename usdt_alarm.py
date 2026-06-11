import io, json, os, sys, time, requests
from datetime import datetime, timedelta
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

UPBIT_TICKER  = 'https://api.upbit.com/v1/ticker?markets=KRW-USDT'
UPBIT_CANDLES = 'https://api.upbit.com/v1/candles/minutes/60'
STATE_FILE    = 'usdt_state.json'
INTERVAL      = 300  # 5분

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}


# ── 데이터 수집 ──────────────────────────────────────────────────────────────

def fetch_current_price():
    resp = requests.get(UPBIT_TICKER, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return float(resp.json()[0]['trade_price'])


def fetch_candles(count=720):
    """1시간봉 count개 최신순으로 가져오기 (페이지네이션)."""
    candles = []
    to = None
    while len(candles) < count:
        batch = min(200, count - len(candles))
        params = {'market': 'KRW-USDT', 'count': batch}
        if to:
            params['to'] = to
        resp = requests.get(UPBIT_CANDLES, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        candles.extend(data)
        if len(data) < batch:
            break
        oldest_utc = data[-1]['candle_date_time_utc']
        oldest_dt  = datetime.strptime(oldest_utc, '%Y-%m-%dT%H:%M:%S')
        to = (oldest_dt - timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%S')
        time.sleep(0.1)
    return candles  # 최신순 정렬


def avg_close(candles, n):
    closes = [c['trade_price'] for c in candles[:n]]
    return sum(closes) / len(closes) if closes else 0.0


def is_consecutive_down(candles):
    if len(candles) < 4:
        return False
    c = [c['trade_price'] for c in candles[:4]]
    return c[0] < c[1] and c[1] < c[2] and c[2] < c[3]


def is_consecutive_up(candles):
    if len(candles) < 4:
        return False
    c = [c['trade_price'] for c in candles[:4]]
    return c[0] > c[1] and c[1] > c[2] and c[2] > c[3]


# ── 시간 조건 ────────────────────────────────────────────────────────────────

def in_buy_time(hour):
    return 14 <= hour < 17


def in_sell_time(hour, minute):
    if hour == 22 and minute >= 30:
        return True
    return hour == 23 or hour == 0


# ── 스코어 계산 ──────────────────────────────────────────────────────────────

def calc_buy_score(curr, avg7, avg30, hour, minute, candles):
    score = 0
    conds = []

    if in_buy_time(hour):
        score += 1
        conds.append('시간대 ✅')

    diff7 = (curr - avg7) / avg7 * 100
    if diff7 <= -0.5:
        score += 2
        conds.append(f'7일 대비 {diff7:.2f}% ✅')
    elif diff7 <= -0.3:
        score += 1
        conds.append(f'7일 대비 {diff7:.2f}% ✅')

    diff30 = (curr - avg30) / avg30 * 100
    if diff30 <= -0.8:
        score += 2
        conds.append(f'30일 대비 {diff30:.2f}% ✅')
    elif diff30 <= -0.5:
        score += 1
        conds.append(f'30일 대비 {diff30:.2f}% ✅')

    if is_consecutive_down(candles):
        score += 1
        conds.append('연속 하락 ✅')

    return score, conds


def calc_sell_score(curr, avg7, avg30, hour, minute, candles):
    score = 0
    conds = []

    if in_sell_time(hour, minute):
        score += 1
        conds.append('시간대 ✅')

    diff7 = (curr - avg7) / avg7 * 100
    if diff7 >= 0.5:
        score += 2
        conds.append(f'7일 대비 {diff7:+.2f}% ✅')
    elif diff7 >= 0.3:
        score += 1
        conds.append(f'7일 대비 {diff7:+.2f}% ✅')

    diff30 = (curr - avg30) / avg30 * 100
    if diff30 >= 0.8:
        score += 2
        conds.append(f'30일 대비 {diff30:+.2f}% ✅')
    elif diff30 >= 0.5:
        score += 1
        conds.append(f'30일 대비 {diff30:+.2f}% ✅')

    if is_consecutive_up(candles):
        score += 1
        conds.append('연속 상승 ✅')

    return score, conds


def apply_trend_filter(buy_score, sell_score, avg7, avg30):
    diff_pct = (avg7 - avg30) / avg30 * 100
    if diff_pct > 0.1:    # 상승 추세 → 매도 스코어 약화
        sell_score = int(sell_score * 0.85)
    elif diff_pct < -0.1:  # 하락 추세 → 매수 스코어 약화
        buy_score  = int(buy_score  * 0.85)
    return buy_score, sell_score


# ── 메시지 조립 ──────────────────────────────────────────────────────────────

def build_buy_message(score, curr, avg7, avg30, hour, minute, conds):
    diff7  = (curr - avg7)  / avg7  * 100
    diff30 = (curr - avg30) / avg30 * 100
    header = '🟢🟢 [강한 매수 신호' if score >= 4 else '🟢 [매수 신호'
    return (
        f'{header} - {score}점]\n'
        f'현재가: {curr:,.0f}원\n'
        f'7일 평균: {avg7:,.0f}원 ({diff7:+.2f}%)\n'
        f'30일 평균: {avg30:,.0f}원 ({diff30:+.2f}%)\n'
        f'시간대: {hour:02d}:{minute:02d} KST (저가 구간)\n'
        f'조건: {" / ".join(conds)}'
    )


def build_sell_message(score, curr, avg7, avg30, hour, minute, conds):
    diff7  = (curr - avg7)  / avg7  * 100
    diff30 = (curr - avg30) / avg30 * 100
    header = '🔴🔴 [강한 매도 신호' if score >= 4 else '🔴 [매도 신호'
    return (
        f'{header} - {score}점]\n'
        f'현재가: {curr:,.0f}원\n'
        f'7일 평균: {avg7:,.0f}원 ({diff7:+.2f}%)\n'
        f'30일 평균: {avg30:,.0f}원 ({diff30:+.2f}%)\n'
        f'시간대: {hour:02d}:{minute:02d} KST (고가 구간)\n'
        f'조건: {" / ".join(conds)}'
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


# ── 메인 체크 ────────────────────────────────────────────────────────────────

def check(force_send=False):
    now_kst = datetime.now(pytz.timezone(TIMEZONE))
    hour, minute = now_kst.hour, now_kst.minute
    print(f'[{now_kst.strftime("%H:%M:%S")}] KRW-USDT 체크 중...')

    try:
        curr = fetch_current_price()
    except Exception as e:
        print(f'  [오류] 현재가 조회 실패: {e}')
        return

    try:
        candles = fetch_candles(720)
    except Exception as e:
        print(f'  [오류] 캔들 조회 실패: {e}')
        return

    if len(candles) < 168:
        print(f'  [경고] 캔들 부족: {len(candles)}개 (최소 168개 필요)')
        return

    n30   = min(720, len(candles))
    avg7  = avg_close(candles, 168)
    avg30 = avg_close(candles, n30)

    diff7_pct  = (curr - avg7)  / avg7  * 100
    diff30_pct = (curr - avg30) / avg30 * 100

    buy_score,  buy_conds  = calc_buy_score( curr, avg7, avg30, hour, minute, candles)
    sell_score, sell_conds = calc_sell_score(curr, avg7, avg30, hour, minute, candles)

    raw_buy, raw_sell = buy_score, sell_score
    buy_score, sell_score = apply_trend_filter(buy_score, sell_score, avg7, avg30)

    trend_pct = (avg7 - avg30) / avg30 * 100
    if abs(trend_pct) <= 0.1:
        trend_str = '횡보'
    elif trend_pct > 0:
        trend_str = f'상승추세 (+{trend_pct:.2f}%) → 매도 스코어 ×0.85 적용'
    else:
        trend_str = f'하락추세 ({trend_pct:.2f}%) → 매수 스코어 ×0.85 적용'

    print(f'  현재가: {curr:,.0f}원')
    print(f'  7일 평균: {avg7:,.0f}원 ({diff7_pct:+.2f}%)  |  30일 평균: {avg30:,.0f}원 ({diff30_pct:+.2f}%)')
    print(f'  추세: {trend_str}')
    print(f'  매수 {buy_score}점 (원점수 {raw_buy})  |  매도 {sell_score}점 (원점수 {raw_sell})')

    if force_send:
        msg = (
            f'✅ KRW-USDT 알람봇 시작\n'
            f'현재가: {curr:,.0f}원\n'
            f'7일 평균: {avg7:,.0f}원 ({diff7_pct:+.2f}%)\n'
            f'30일 평균: {avg30:,.0f}원 ({diff30_pct:+.2f}%)\n'
            f'추세: {trend_str.split(" →")[0]}\n'
            f'매수 스코어: {buy_score}점  |  매도 스코어: {sell_score}점\n'
            f'캔들 {len(candles)}개 로드 완료'
        )
        print(f'\n{msg}\n')
        send_telegram(msg)
        return

    state    = load_state()
    now_ts   = time.time()
    cooldown = 3600.0

    def elapsed(key):
        ts = state.get(key)
        return (now_ts - float(ts)) if ts is not None else float('inf')

    if buy_score >= 2 and elapsed('last_buy') >= cooldown:
        msg = build_buy_message(buy_score, curr, avg7, avg30, hour, minute, buy_conds)
        print(f'\n{msg}\n')
        if send_telegram(msg):
            state['last_buy'] = now_ts

    if sell_score >= 2 and elapsed('last_sell') >= cooldown:
        msg = build_sell_message(sell_score, curr, avg7, avg30, hour, minute, sell_conds)
        print(f'\n{msg}\n')
        if send_telegram(msg):
            state['last_sell'] = now_ts

    save_state(state)


# ── 진입점 ───────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('[시작] KRW-USDT 알람봇 (5분마다 체크, 조건 충족 시 발송)')
    print('       알람 기준: 매수/매도 각 2점 이상 (강한 알람: 4점 이상)\n')
    first = True
    try:
        while True:
            check(force_send=first)
            first = False
            print(f'[대기] {INTERVAL // 60}분 후 재체크...\n')
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print('\n[종료] USDT 알람봇을 종료합니다.')
