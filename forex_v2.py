import csv
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

BOT_TOKEN        = os.environ.get("BOT_TOKEN", "")
CHAT_ID          = os.environ.get("CHAT_ID", "")
TIMEZONE         = os.environ.get("TIMEZONE", "Asia/Seoul")
UPBIT_URL        = "https://api.upbit.com/v1/ticker?markets=KRW-USDT"
SIMULATION_CSV   = os.environ.get("SIMULATION_CSV",   "simulation.csv")
SIMULATION_STATE = os.environ.get("SIMULATION_STATE", "simulation_state.json")
STARTING_SEED    = int(os.environ.get("STARTING_SEED", "10000000"))

YAHOO_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36'
}
CALENDAR_EVENTS = {
    "2026-06-17": ["FOMC"],
    "2026-07-29": ["FOMC"],
    "2026-09-16": ["FOMC"],
    "2026-07-14": ["CPI"],
    "2026-08-12": ["CPI"],
    "2026-09-11": ["CPI"],
    "2026-07-02": ["NFP"],
    "2026-08-06": ["NFP"],
}


# ── 데이터 수집 ──────────────────────────────────────────────────────────────

def fetch_yahoo_data():
    """USD/KRW 일봉: (현재가, 전일종가, 오늘시가, 전일고가, 전일저가)"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X?interval=1d&range=10d"
        resp = requests.get(url, headers=YAHOO_HEADERS, timeout=15)
        resp.raise_for_status()
        result = resp.json().get('chart', {}).get('result', [None])[0]
        if not result:
            return None, None, None, None, None
        q      = result.get('indicators', {}).get('quote', [{}])[0]
        opens  = [x for x in q.get('open',  []) if x is not None]
        closes = [x for x in q.get('close', []) if x is not None]
        highs  = [x for x in q.get('high',  []) if x is not None]
        lows   = [x for x in q.get('low',   []) if x is not None]
        if len(closes) < 2:
            return None, None, None, None, None
        return (
            closes[-1],
            closes[-2],
            opens[-1] if opens else closes[-1],
            highs[-2] if len(highs) >= 2 else None,
            lows[-2]  if len(lows)  >= 2 else None,
        )
    except Exception as e:
        print(f"Yahoo Finance 조회 실패: {e}")
        return None, None, None, None, None


def fetch_upbit_usdt():
    try:
        resp = requests.get(UPBIT_URL, timeout=10)
        data = resp.json()
        if isinstance(data, list) and data:
            return float(data[0].get('trade_price', 0))
    except Exception as e:
        print(f"업비트 조회 실패: {e}")
    return None


def get_b_pattern_count():
    """NDF 갭 +0.3% 초과 2일 연속 → 2, 아니면 0"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X?interval=1d&range=10d"
        resp = requests.get(url, headers=YAHOO_HEADERS, timeout=15)
        resp.raise_for_status()
        result = resp.json().get('chart', {}).get('result', [None])[0]
        if not result:
            return 0
        q     = result.get('indicators', {}).get('quote', [{}])[0]
        pairs = [(o, c) for o, c in zip(q.get('open', []), q.get('close', []))
                 if o is not None and c is not None]
        if len(pairs) < 3:
            return 0
        gaps = [((pairs[i][0] - pairs[i-1][1]) / pairs[i-1][1]) * 100
                for i in range(1, len(pairs))]
        return 2 if len(gaps) >= 2 and gaps[-1] > 0.3 and gaps[-2] > 0.3 else 0
    except Exception as e:
        print(f"B패턴 계산 실패: {e}")
        return 0


def get_today_events():
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
    return CALENDAR_EVENTS.get(today, [])


# ── 점수 계산 ────────────────────────────────────────────────────────────────

def _ndf_buy(gap):
    if gap is None: return 0, None
    if gap <= -0.5: return  30, '+30 NDF 갭 -0.5% 이하'
    if gap <= -0.3: return  20, '+20 NDF 갭 -0.3%~-0.5%'
    if gap <=  0.0: return  10, '+10 NDF 갭 0%~-0.3%'
    if gap <=  0.3: return   0, None
    if gap <=  0.5: return -10, '-10 NDF 갭 +0.3%~+0.5%'
    if gap <=  1.0: return -20, '-20 NDF 갭 +0.5%~+1%'
    return                -30, '-30 NDF 갭 +1% 초과'


def _premium_buy(p):
    if p is None: return 0, None
    if p <= -2.0: return  30, f'+30 역프 {p:+.2f}%'
    if p <= -1.0: return  20, f'+20 역프 {p:+.2f}%'
    if p <= -0.5: return  10, f'+10 역프 {p:+.2f}%'
    if p <   0.0: return   0, None
    if p <   0.5: return -10, f'-10 김프 {p:+.2f}%'
    if p <   1.0: return -20, f'-20 김프 {p:+.2f}%'
    return              -30, f'-30 김프 {p:+.2f}%'


def _ndf_sell(gap):
    if gap is None: return 0, None
    if gap >= 0.5:  return  30, '+30 NDF 갭 +0.5% 이상'
    if gap >= 0.3:  return  20, '+20 NDF 갭 +0.3%~+0.5%'
    if gap >= 0.0:  return  10, '+10 NDF 갭 0%~+0.3%'
    if gap >= -0.3: return   0, None
    if gap >= -0.5: return -10, '-10 NDF 갭 -0.3%~-0.5%'
    if gap >= -1.0: return -20, '-20 NDF 갭 -0.5%~-1%'
    return                -30, '-30 NDF 갭 -1% 이하'


def _premium_sell(p):
    if p is None: return 0, None
    if p >= 1.0:  return  30, f'+30 김프 {p:+.2f}%'
    if p >= 0.5:  return  20, f'+20 김프 {p:+.2f}%'
    if p >= 0.0:  return  10, f'+10 김프 {p:+.2f}%'
    if p >= -0.5: return   0, None
    if p >= -1.0: return -10, f'-10 역프 {p:+.2f}%'
    if p >= -2.0: return -20, f'-20 역프 {p:+.2f}%'
    return              -30, f'-30 역프 {p:+.2f}%'


def calc_buy_score(gap, premium, events, b_count, prev_range):
    score, details = 0, []

    for pts, label in [_ndf_buy(gap), _premium_buy(premium)]:
        score += pts
        if label:
            details.append(label)

    if events:
        score -= 20
        details.append(f'-20 이벤트 당일 ({", ".join(events)})')
    else:
        score += 10
        details.append('+10 이벤트 없음')

    if prev_range is not None and prev_range >= 20:
        score += 10
        details.append(f'+10 전일 변동폭 {prev_range:.0f}원')

    if b_count >= 2:
        score -= 20
        details.append(f'-20 B패턴 {b_count}일 연속')

    # 특이점: NDF 갭 <= -0.3% AND 역프 <= -1% AND 이벤트 없음 AND 전일 변동폭 20원+
    special = (
        gap is not None and gap <= -0.3
        and premium is not None and premium <= -1.0
        and not events
        and prev_range is not None and prev_range >= 20
    )
    if special:
        score += 10
        details.append('+10 ⭐ 특이점')

    return max(0, min(score, 100)), details, special


def calc_sell_score(gap, premium, events, b_count, prev_range):
    score, details = 0, []

    for pts, label in [_ndf_sell(gap), _premium_sell(premium)]:
        score += pts
        if label:
            details.append(label)

    if events:
        score -= 20
        details.append(f'-20 이벤트 당일 ({", ".join(events)})')
    else:
        score += 10
        details.append('+10 이벤트 없음')

    if prev_range is not None and prev_range >= 20:
        score += 10
        details.append(f'+10 전일 변동폭 {prev_range:.0f}원')

    if b_count >= 2:
        score += 20
        details.append(f'+20 B패턴 {b_count}일 연속')

    # 특이점: NDF 갭 >= +0.3% AND (김프 전환 or 역프 축소: premium >= -0.5%)
    #         AND 이벤트 없음 AND B패턴 2일 연속
    special = (
        gap is not None and gap >= 0.3
        and premium is not None and premium >= -0.5
        and not events
        and b_count >= 2
    )
    if special:
        score += 10
        details.append('+10 ⭐ 특이점')

    return max(0, min(score, 100)), details, special


def get_grade(score):
    if score >= 90: return '최강 (특이점)'
    if score >= 80: return '최강'
    if score >= 60: return '권장'
    if score >= 40: return '조건부'
    if score >= 20: return '관망/홀딩'
    return '스킵'


# ── 시뮬레이션 ──────────────────────────────────────────────────────────────

def _load_state():
    if os.path.exists(SIMULATION_STATE):
        try:
            with open(SIMULATION_STATE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'cash': STARTING_SEED, 'holding': False, 'buy_price': 0.0,
            'buy_date': '', 'cumulative_pnl': 0.0}


def _save_state(state):
    with open(SIMULATION_STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _append_csv(row):
    exists = os.path.exists(SIMULATION_CSV)
    with open(SIMULATION_CSV, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=['날짜', '매수가', '매도가', '수익률', '누적손익'])
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def run_simulation(buy_score, sell_score, trade_price):
    state = _load_state()
    now   = datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d %H:%M')

    if not state['holding'] and buy_score >= 80 and trade_price:
        state['holding']   = True
        state['buy_price'] = trade_price
        state['buy_date']  = now
        _save_state(state)
        print(f'[시뮬] 가상 매수: {trade_price:,.0f}원 ({now})')

    elif state['holding'] and sell_score >= 80 and trade_price:
        buy_p  = state['buy_price']
        pnl    = (trade_price - buy_p) / buy_p * 100
        profit = (trade_price - buy_p) * (state['cash'] / buy_p)
        state['cash']           += profit
        state['cumulative_pnl'] += profit
        _append_csv({
            '날짜':    now,
            '매수가':  f'{buy_p:,.0f}',
            '매도가':  f'{trade_price:,.0f}',
            '수익률':  f'{pnl:+.2f}%',
            '누적손익': f'{state["cumulative_pnl"]:,.0f}',
        })
        state['holding']   = False
        state['buy_price'] = 0.0
        _save_state(state)
        print(f'[시뮬] 가상 매도: {trade_price:,.0f}원 | 수익률 {pnl:+.2f}% | 누적손익 {state["cumulative_pnl"]:,.0f}원')

    return state


def build_sim_line(state, current_price):
    if state['holding'] and state['buy_price'] and current_price:
        pnl_pct = (current_price - state['buy_price']) / state['buy_price'] * 100
        return (f'📊 시뮬레이션\n'
                f'상태: 보유 중 | 매수가: {state["buy_price"]:,.0f}원 | 수익률: {pnl_pct:+.2f}%')
    seed = state.get('cash', STARTING_SEED)
    return (f'📊 시뮬레이션\n'
            f'상태: 대기 중 | 시드: {seed:,.0f}원')


# ── 메시지 조립 ──────────────────────────────────────────────────────────────

def build_message(usd_krw, upbit_usdt, premium,
                  buy_score, buy_details, buy_special,
                  sell_score, sell_details, sell_special,
                  sim_line):
    usd_label     = f'{usd_krw:,.0f}원'    if usd_krw    else 'N/A'
    upbit_label   = f'{upbit_usdt:,.0f}원'  if upbit_usdt else 'N/A'
    premium_label = f'{premium:+.2f}%'      if premium is not None else 'N/A'

    buy_grade  = get_grade(buy_score)
    sell_grade = get_grade(sell_score)

    if buy_score >= sell_score and buy_score >= 20:
        recommend = f'{buy_grade} 매수'
    elif sell_score > buy_score and sell_score >= 20:
        recommend = f'{sell_grade} 매도'
    else:
        recommend = '관망/홀딩'

    buy_lines      = '\n'.join(buy_details) if buy_details else '해당 없음'
    special_header = '⭐ 특이점 발생! 모든 핵심 조건 동시 충족\n' if (buy_special or sell_special) else ''

    return (
        f'{special_header}'
        f'💱 현재 시세\n'
        f'USD/KRW:     {usd_label}\n'
        f'업비트 USDT: {upbit_label}\n'
        f'역프/김프:   {premium_label}\n'
        f'━━━━━━━━━━━━━━\n'
        f'🟢 매수점수: {buy_score}점 ({buy_grade})\n'
        f'🔴 매도점수: {sell_score}점 ({sell_grade})\n'
        f'━━━━━━━━━━━━━━\n'
        f'매수 근거\n'
        f'{buy_lines}\n'
        f'━━━━━━━━━━━━━━\n'
        f'{sim_line}\n'
        f'━━━━━━━━━━━━━━\n'
        f'🎯 추천: {recommend}'
    )


# ── 텔레그램 전송 ────────────────────────────────────────────────────────────

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print('[경고] BOT_TOKEN 또는 CHAT_ID가 없습니다.')
        print(text)
        return False
    try:
        payload = {
            'chat_id':    CHAT_ID,
            'text':       text,
            'parse_mode': 'HTML',
        }
        resp = requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json; charset=utf-8'},
            timeout=10
        )
        if resp.status_code == 200:
            print('[전송] 텔레그램 메시지 발송 성공')
            return True
        print(f'[실패] {resp.status_code}: {resp.text}')
        return False
    except Exception as e:
        print(f'[오류] {e}')
        return False


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print('[시작] forex_v2 데이터 수집 중...')

    usd_krw, prev_close, today_open, prev_high, prev_low = fetch_yahoo_data()
    upbit_usdt = fetch_upbit_usdt()
    events     = get_today_events()
    b_count    = get_b_pattern_count()

    ndf_gap = None
    if prev_close and today_open and prev_close != 0:
        ndf_gap = round(((today_open - prev_close) / prev_close) * 100, 2)

    premium = None
    if usd_krw and upbit_usdt and usd_krw != 0:
        premium = round(((upbit_usdt - usd_krw) / usd_krw) * 100, 2)

    prev_range = None
    if prev_high is not None and prev_low is not None:
        prev_range = round(prev_high - prev_low, 2)

    print(f'  USD/KRW:     {usd_krw}')
    print(f'  업비트 USDT: {upbit_usdt}')
    print(f'  역프/김프:   {premium}%')
    print(f'  NDF 갭:      {ndf_gap}%')
    print(f'  전일 변동폭: {prev_range}원')
    print(f'  이벤트:      {events or "없음"}')
    print(f'  B패턴:       {b_count}일')

    buy_score,  buy_details,  buy_special  = calc_buy_score( ndf_gap, premium, events, b_count, prev_range)
    sell_score, sell_details, sell_special = calc_sell_score(ndf_gap, premium, events, b_count, prev_range)

    print(f'  매수점수:    {buy_score}점 ({get_grade(buy_score)}){" ⭐특이점" if buy_special else ""}')
    print(f'  매도점수:    {sell_score}점 ({get_grade(sell_score)}){" ⭐특이점" if sell_special else ""}')

    trade_price = upbit_usdt if upbit_usdt else usd_krw
    sim_state   = run_simulation(buy_score, sell_score, trade_price)
    sim_line    = build_sim_line(sim_state, trade_price)

    msg = build_message(
        usd_krw, upbit_usdt, premium,
        buy_score, buy_details, buy_special,
        sell_score, sell_details, sell_special,
        sim_line,
    )
    print('\n' + msg + '\n')
    send_telegram(msg)


def _seconds_until_next_hour():
    now       = datetime.now(pytz.timezone(TIMEZONE))
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return max((next_hour - now).total_seconds(), 1)


if __name__ == '__main__':
    print('[스케줄] 매 정시(00분)마다 자동 발송 시작 (Ctrl+C로 종료)')
    try:
        while True:
            main()
            secs     = _seconds_until_next_hour()
            next_run = (datetime.now(pytz.timezone(TIMEZONE)) + timedelta(seconds=secs)).strftime('%H:%M')
            print(f'[대기] 다음 실행: {next_run} (약 {int(secs // 60)}분 {int(secs % 60)}초 후)\n')
            time.sleep(secs)
    except KeyboardInterrupt:
        print('\n[종료] 봇을 종료합니다.')
