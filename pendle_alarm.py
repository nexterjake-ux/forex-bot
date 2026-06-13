import io, json, os, sys, time
import requests
import yfinance as yf
from datetime import datetime, timezone as _tz
import pytz
from dotenv import load_dotenv

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
elif hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

load_dotenv()
UTC = _tz.utc

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
CHAT_ID   = os.environ.get('CHAT_ID', '')
TIMEZONE  = os.environ.get('TIMEZONE', 'Asia/Seoul')

PENDLE_API = 'https://api-v2.pendle.finance/core/v1/1/markets?limit=100'
STATE_FILE = 'pendle_state.json'
MODEL_FILE = 'strc_apy_model.json'
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

_DEFAULT_MODEL = {
    'beta': 1.8982, 'intercept': 2.2247,
    'apyusd_par': 1.36, 'strc_par': 100.0, 'r2': 0.6186,
}


# ── 모델 ──────────────────────────────────────────────────────────────────────

def load_model():
    if os.path.exists(MODEL_FILE):
        try:
            with open(MODEL_FILE, encoding='utf-8') as f:
                return {**_DEFAULT_MODEL, **json.load(f)}
        except Exception:
            pass
    return _DEFAULT_MODEL.copy()


def maybe_update_model(state):
    """주 1회 beta/intercept 재계산."""
    if time.time() - float(state.get('model_update_ts', 0)) < 7 * 86400:
        return
    print('  [모델] 주간 업데이트 실행...')
    try:
        import strc_apy_analysis
        strc_apy_analysis.main()
        state['model_update_ts'] = time.time()
        print('  [모델] 업데이트 완료')
    except Exception as e:
        print(f'  [모델] 업데이트 실패: {e}')
        state['model_update_ts'] = time.time()  # 실패해도 타이머 리셋


def calc_fair_price(strc_price, model):
    strc_pct = (strc_price - model['strc_par']) / model['strc_par'] * 100
    return model['apyusd_par'] * (1 + (model['beta'] * strc_pct + model['intercept']) / 100)


def calc_deviation(apyusd_price, fair_price):
    return (apyusd_price - fair_price) / fair_price * 100


def deviation_tag(dev):
    if dev >= 3.0:  return f'{dev:+.2f}% 🔴 고평가'
    if dev <= -3.0: return f'{dev:+.2f}% ✅ 저평가'
    return              f'{dev:+.2f}% (적정)'


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


def fetch_strc_price():
    ticker = yf.Ticker('STRC')
    try:
        p = ticker.fast_info.last_price
        if p and p == p:  # not None / NaN
            return float(p)
    except Exception:
        pass
    df = ticker.history(period='2d', interval='1d')
    if df.empty:
        raise ValueError('STRC 데이터 없음')
    return float(df['Close'].iloc[-1])


# ── 시장 / 추세 헬퍼 ──────────────────────────────────────────────────────────

def is_us_market_open():
    now_et = datetime.now(pytz.timezone('America/New_York'))
    if now_et.weekday() >= 5:
        return False
    o = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    c = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return o <= now_et <= c


def strc_chg_pct(now, prev):
    if prev is None or prev == 0:
        return 0.0
    return (now - prev) / prev * 100


def get_apy_grade(diff):
    if diff >= 1.0:   return '🟢🟢 매수 강추'
    if diff >= 0.4:   return '🟢  매수 추천'
    if diff >= 0.0:   return '🟡  매수 고려'
    if diff >= -0.4:  return '⚪  매수 관망'
    if diff >= -1.0:  return '🟠  매도 고려'
    return               '🔴  매도 강추'


def _now_str():
    return datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M')


def _apy_lines(apy_map):
    lines = []
    for m in MARKETS:
        raw = apy_map.get(m['address'].lower())
        if raw is None:
            lines.append(f"PT-apyUSD {m['days']}일: 조회 실패")
        else:
            curr = round(float(raw) * 100, 2)
            diff = curr - m['median']
            lines.append(f"PT-apyUSD {m['days']}일: {curr:.2f}%  ({diff:+.2f}% vs 중앙값)")
    return '\n'.join(lines)


def _price_line(apyusd, fair, strc, model):
    strc_pct = (strc - model['strc_par']) / model['strc_par'] * 100
    dev      = calc_deviation(apyusd, fair)
    return (f"apyUSD: ${apyusd:.4f}  |  적정가: ${fair:.4f}  (괴리 {deviation_tag(dev)})\n"
            f"STRC: ${strc:.2f}  ({strc_pct:+.2f}% vs par)")


# ── 메시지 빌더 (①~⑤) ────────────────────────────────────────────────────────

def build_buy_msg(apy_map, apyusd, fair, strc, model):
    dev = calc_deviation(apyusd, fair)
    strc_pct = (strc - model['strc_par']) / model['strc_par'] * 100
    return (
        f'🟢 [매수 신호 - HIGH]  ({_now_str()})\n'
        f'{_apy_lines(apy_map)}\n\n'
        f'apyUSD: ${apyusd:.4f}  |  적정가: ${fair:.4f}\n'
        f'괴리: {dev:+.2f}%  ← 저평가 ✅\n\n'
        f'STRC: ${strc:.2f}  ({strc_pct:+.2f}% vs par)  안정/회복 ✅\n\n'
        f'📌 진입 추천'
    )


def build_hold_msg(apy_map, apyusd, fair, strc, model, reason):
    dev = calc_deviation(apyusd, fair)
    strc_pct = (strc - model['strc_par']) / model['strc_par'] * 100
    reason_str = {
        'overvalued': f'고평가 ⚠️  (괴리 {dev:+.2f}%)',
        'strc_drop':  f'STRC 하락 중 ⚠️  ({strc_pct:+.2f}% vs par)',
        'both':       f'고평가 + STRC 하락 ⚠️',
    }.get(reason, '')
    return (
        f'🟡 [매수 보류]  ({_now_str()})\n'
        f'APY 조건 충족 / 진입 조건 미달\n\n'
        f'{_apy_lines(apy_map)}\n\n'
        f'{_price_line(apyusd, fair, strc, model)}\n\n'
        f'{reason_str}'
    )


def build_sell_msg(apy_map, apyusd, fair, strc, model, entry_apyusd=None):
    dev = calc_deviation(apyusd, fair)
    strc_pct = (strc - model['strc_par']) / model['strc_par'] * 100
    pnl_line = ''
    if entry_apyusd:
        pnl = (apyusd - entry_apyusd) / entry_apyusd * 100
        pnl_line = f'\n진입가 대비: ${entry_apyusd:.4f} → ${apyusd:.4f}  ({pnl:+.2f}%)'
    return (
        f'🔴 [매도 신호]  ({_now_str()})\n'
        f'{_apy_lines(apy_map)}\n\n'
        f'apyUSD: ${apyusd:.4f}  |  적정가: ${fair:.4f}\n'
        f'괴리: {dev:+.2f}%  ← 고평가 🔴\n\n'
        f'STRC: ${strc:.2f}  ({strc_pct:+.2f}% vs par)'
        f'{pnl_line}\n\n'
        f'📌 포지션 매도 검토'
    )


def build_change_msg(market, prev_apy, curr_apy, apyusd=None, fair=None, strc=None, model=None):
    change = curr_apy - prev_apy
    diff   = curr_apy - market['median']
    grade  = get_apy_grade(diff)
    lines  = [
        f'📊 PT-apyUSD {market["days"]}일 변동  ({_now_str()})',
        f'{prev_apy:.2f}% → {curr_apy:.2f}%  ({change:+.2f}%)',
        f'━━━━━━━━━━━━━━',
        f'중앙값: {market["median"]:.2f}%  |  현재 차이: {diff:+.2f}%',
        f'{grade} 구간',
    ]
    if apyusd is not None and fair is not None and strc is not None and model is not None:
        strc_pct = (strc - model['strc_par']) / model['strc_par'] * 100
        dev = calc_deviation(apyusd, fair)
        lines += [
            f'━━━━━━━━━━━━━━',
            f'apyUSD: ${apyusd:.4f}  |  적정가: ${fair:.4f}  (괴리 {dev:+.2f}%)',
            f'STRC: ${strc:.2f}  ({strc_pct:+.2f}% vs par)',
        ]
    return '\n'.join(lines)


def build_strc_warn_msg(strc_now, strc_prev, apyusd, fair, model):
    chg = strc_chg_pct(strc_now, strc_prev)
    dev = calc_deviation(apyusd, fair)
    note = '→ 하락 여지 있음' if dev >= 0 else '→ 이미 저평가 반영 중'
    return (
        f'⚠️ [STRC 급락 경고]  ({_now_str()})\n'
        f'${strc_prev:.2f} → ${strc_now:.2f}  ({chg:+.2f}%)\n'
        f'→ apyUSD 하락 선행 가능성\n\n'
        f'apyUSD 현재가: ${apyusd:.4f}\n'
        f'모델 적정가: ${fair:.4f}  (현재 {dev:+.2f}%  {note})'
    )


# ── 텔레그램 / 상태 ───────────────────────────────────────────────────────────

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

    state = load_state()
    apy_keys = ', '.join(f'{k[:8]}..={v:.4f}%'
                         for k, v in state.items()
                         if isinstance(v, float) and k.startswith('0x'))
    print(f'  [상태] {apy_keys or "없음 (초기 실행)"}')

    # 주간 모델 업데이트
    maybe_update_model(state)
    model = load_model()

    # ── 데이터 수집 ──────────────────────────────────────────────────────────
    try:
        apy_map = fetch_apy_map()
    except Exception as e:
        print(f'  [오류] Pendle API 실패: {e}')
        return

    apyusd_price, strc_price, fair_price, deviation = None, None, None, None
    try:
        apyusd_price = fetch_apyusd_price()
    except Exception as e:
        print(f'  [경고] apyUSD 조회 실패: {e}')

    try:
        strc_price = fetch_strc_price()
    except Exception as e:
        print(f'  [경고] STRC 조회 실패: {e}')

    if strc_price is not None and apyusd_price is not None:
        fair_price = calc_fair_price(strc_price, model)
        deviation  = calc_deviation(apyusd_price, fair_price)
        strc_pct   = (strc_price - model['strc_par']) / model['strc_par'] * 100
        print(f'  apyUSD: ${apyusd_price:.4f}  |  STRC: ${strc_price:.2f} ({strc_pct:+.2f}%)')
        print(f'  적정가: ${fair_price:.4f}  괴리: {deviation:+.2f}%')

    now_ts   = time.time()
    COOLDOWN = 3600.0

    def elapsed(key):
        ts = state.get(key)
        return (now_ts - float(ts)) if ts is not None else float('inf')

    # ── 시작 알림 (force_send) ────────────────────────────────────────────────
    if force_send:
        lines = [f'✅ Pendle 알람봇 시작  ({_now_str()})']
        lines.append(_apy_lines(apy_map))
        if apyusd_price is not None and fair_price is not None:
            lines.append(f'\napyUSD: ${apyusd_price:.4f}  |  적정가: ${fair_price:.4f}'
                         f'  (괴리 {deviation:+.2f}%)')
        if strc_price is not None:
            strc_pct = (strc_price - model['strc_par']) / model['strc_par'] * 100
            lines.append(f'STRC: ${strc_price:.2f}  ({strc_pct:+.2f}% vs par)')
        send_telegram('\n'.join(lines))

    # ── ⑤ STRC 급락 경고 ─────────────────────────────────────────────────────
    strc_prev = state.get('strc_last')
    if (strc_price is not None and apyusd_price is not None and fair_price is not None
            and strc_prev is not None
            and strc_chg_pct(strc_price, strc_prev) <= -1.0
            and is_us_market_open()
            and elapsed('strc_warn_ts') >= COOLDOWN):
        msg = build_strc_warn_msg(strc_price, strc_prev, apyusd_price, fair_price, model)
        print(f'\n{msg}\n')
        if send_telegram(msg):
            state['strc_warn_ts'] = now_ts

    # ── ④ APY 변동 알람 ──────────────────────────────────────────────────────
    for m in MARKETS:
        addr = m['address'].lower()
        raw  = apy_map.get(addr)
        if raw is None:
            print(f'  {m["name"]}: 조회 실패')
            continue
        curr = round(float(raw) * 100, 4)
        prev = state.get(addr)
        diff = curr - m['median']
        print(f'  {m["name"]}: {curr:.4f}%  ({diff:+.2f}% vs 중앙값)', end='')

        if prev is None:
            print(f'  → 초기 기준값 설정')
            state[addr] = curr
        else:
            change = curr - prev
            print(f'  변동: {change:+.4f}% (기준: {prev:.4f}%)')
            if abs(change) >= 0.1:
                msg = build_change_msg(m, prev, curr,
                                       apyusd_price, fair_price, strc_price, model)
                print(f'\n{msg}\n')
                send_telegram(msg)
                state[addr] = curr
            else:
                print(f'  → 임계값 미달, 기준값 유지')

    # ── ①②③ 종합 매수/보류/매도 ─────────────────────────────────────────────
    if strc_price is not None and apyusd_price is not None and not force_send:
        apy_buy_ok = any(
            round(float(apy_map.get(m['address'].lower()) or 0) * 100, 4) > m['median'] + 0.4
            for m in MARKETS
        )
        strc_dropping = (strc_prev is not None
                         and strc_chg_pct(strc_price, strc_prev) <= -1.0)
        overvalued    = deviation >= 3.0
        undervalued   = deviation <= -3.0

        # ③ 매도 (최우선 체크)
        sell_by_apy = any(
            round(float(apy_map.get(m['address'].lower()) or 0) * 100, 4) <= 17.0
            for m in MARKETS
        ) and apyusd_price >= 1.32
        if (sell_by_apy or overvalued) and elapsed('sell_ts') >= COOLDOWN:
            entry_apyusd = state.get('entry_apyusd')
            msg = build_sell_msg(apy_map, apyusd_price, fair_price, strc_price, model,
                                 entry_apyusd)
            print(f'\n{msg}\n')
            if send_telegram(msg):
                state['sell_ts'] = now_ts
                state.pop('entry_apyusd', None)
                for m in MARKETS:
                    state.pop(f'entry_apy_{m["days"]}', None)

        # ① 매수 HIGH
        elif (apy_buy_ok and undervalued and not strc_dropping
              and elapsed('buy_ts') >= COOLDOWN):
            msg = build_buy_msg(apy_map, apyusd_price, fair_price, strc_price, model)
            print(f'\n{msg}\n')
            if send_telegram(msg):
                state['buy_ts']       = now_ts
                state['entry_apyusd'] = apyusd_price
                for m in MARKETS:
                    raw = apy_map.get(m['address'].lower())
                    if raw:
                        state[f'entry_apy_{m["days"]}'] = round(float(raw) * 100, 4)

        # ② 매수 보류
        elif (apy_buy_ok and (overvalued or strc_dropping)
              and elapsed('hold_ts') >= COOLDOWN):
            reason = ('both' if (overvalued and strc_dropping)
                      else 'overvalued' if overvalued else 'strc_drop')
            msg = build_hold_msg(apy_map, apyusd_price, fair_price, strc_price, model,
                                 reason)
            print(f'\n{msg}\n')
            if send_telegram(msg):
                state['hold_ts'] = now_ts

    if strc_price is not None:
        state['strc_last'] = strc_price

    save_state(state)
    print(f'  [상태] 저장 완료: {STATE_FILE}')


# ── 테스트: ①~⑤ 형식별 1회 발송 ────────────────────────────────────────────

def test_all_messages():
    print('[테스트] ①~⑤ 형식 테스트 메시지 발송...\n')
    model = load_model()

    _AM = {
        '0x30bb9ee8dc6aab322dc3a0d36063cbf06a9e5952': 0.1803,   # 18.03%
        '0xc5f938a8ef5f3bf9e72f5aa094baf5e03f4727d3': 0.1684,   # 16.84%
    }

    # ① 매수 신호 (저평가: deviation ≤ -3%)
    strc1 = 95.20
    fair1 = calc_fair_price(strc1, model)
    apy1  = 1.218  # (1.218 - 1.2663) / 1.2663 * 100 ≈ -3.8%
    msg1  = build_buy_msg(_AM, apy1, fair1, strc1, model)
    print(f'①\n{msg1}\n')
    send_telegram(f'[테스트 ①]\n{msg1}')
    time.sleep(1)

    # ② 매수 보류 (고평가: deviation ≥ +3%)
    strc2 = 94.80
    fair2 = calc_fair_price(strc2, model)
    apy2  = 1.295  # (1.295 - 1.256) / 1.256 * 100 ≈ +3.1%
    msg2  = build_hold_msg(_AM, apy2, fair2, strc2, model, 'overvalued')
    print(f'②\n{msg2}\n')
    send_telegram(f'[테스트 ②]\n{msg2}')
    time.sleep(1)

    # ③ 매도 신호 (APY 하락 + 고평가)
    _AM3 = {
        '0x30bb9ee8dc6aab322dc3a0d36063cbf06a9e5952': 0.1685,
        '0xc5f938a8ef5f3bf9e72f5aa094baf5e03f4727d3': 0.1592,
    }
    strc3 = 96.50
    fair3 = calc_fair_price(strc3, model)
    apy3  = 1.342  # (1.342 - 1.300) / 1.300 * 100 ≈ +3.2%
    msg3  = build_sell_msg(_AM3, apy3, fair3, strc3, model, entry_apyusd=1.218)
    print(f'③\n{msg3}\n')
    send_telegram(f'[테스트 ③]\n{msg3}')
    time.sleep(1)

    # ④ 변동 알람
    strc4 = 94.80
    fair4 = calc_fair_price(strc4, model)
    apy4  = 1.284
    msg4  = build_change_msg(MARKETS[0], 17.47, 18.90, apy4, fair4, strc4, model)
    print(f'④\n{msg4}\n')
    send_telegram(f'[테스트 ④]\n{msg4}')
    time.sleep(1)

    # ⑤ STRC 급락 경고
    strc5_now  = 94.80
    strc5_prev = 95.77
    fair5 = calc_fair_price(strc5_now, model)
    msg5  = build_strc_warn_msg(strc5_now, strc5_prev, 1.284, fair5, model)
    print(f'⑤\n{msg5}\n')
    send_telegram(f'[테스트 ⑤]\n{msg5}')

    print('\n[테스트] 완료')


# ── 진입점 ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if '--test' in sys.argv:
        test_all_messages()
    else:
        print('[시작] Pendle 알람봇 v2  (APY + apyUSD + STRC  |  5분마다 체크)')
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
