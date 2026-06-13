import io
import json
import sys
import numpy as np

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
elif hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import pandas as pd
import requests
import yfinance as yf
from datetime import datetime, timezone as _tz
UTC = _tz.utc

STRC_PAR   = 100.0
APYUSD_PAR = 1.36
DAYS       = 90


# ── 데이터 수집 ───────────────────────────────────────────────────────────────

def fetch_strc():
    print("  STRC (Yahoo Finance)...")
    ticker = yf.Ticker("STRC")
    df = ticker.history(period=f"{DAYS}d", interval="1d")
    if df.empty:
        raise ValueError("STRC 데이터 없음 — 티커를 확인하세요")
    s = df['Close'].dropna()
    s.index = pd.to_datetime(s.index).normalize().tz_localize(None)
    return s.rename('STRC')


def fetch_apyusd():
    print("  apyUSD (CoinGecko)...")
    url = "https://api.coingecko.com/api/v3/coins/apyusd/market_chart"
    # days > 90 이어야 CoinGecko가 daily granularity 반환
    params = {'vs_currency': 'usd', 'days': DAYS + 2}
    resp = requests.get(url, params=params,
                        headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
    resp.raise_for_status()
    prices = resp.json()['prices']  # [[timestamp_ms, price], ...]
    s = pd.Series(
        [p[1] for p in prices],
        index=pd.to_datetime([p[0] for p in prices], unit='ms', utc=True),
        name='apyUSD',
    )
    daily = s.resample('1D').last().dropna()
    daily.index = daily.index.tz_localize(None)
    return daily.tail(DAYS)


# ── 회귀 분석 ─────────────────────────────────────────────────────────────────

def calc_regression(x, y):
    x = np.array(x, dtype=float)
    y = np.array(y, dtype=float)

    corr      = np.corrcoef(x, y)[0, 1]
    cov_mat   = np.cov(x, y, ddof=1)
    beta      = cov_mat[0, 1] / np.var(x, ddof=1)
    intercept = np.mean(y) - beta * np.mean(x)

    y_pred = beta * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2     = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return corr, beta, intercept, r2


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    SEP = "=" * 58
    print(f"\n{SEP}")
    print("  STRC / apyUSD 상관관계 분석  (최근 90일)")
    print(SEP)

    # 1. 데이터 수집
    print("\n[1] 데이터 수집")
    strc_raw   = fetch_strc()
    apyusd_raw = fetch_apyusd()
    print(f"  STRC   : {len(strc_raw)}일  "
          f"{strc_raw.index[0].date()} ~ {strc_raw.index[-1].date()}")
    print(f"  apyUSD : {len(apyusd_raw)}일  "
          f"{apyusd_raw.index[0].date()} ~ {apyusd_raw.index[-1].date()}")

    # 공통 날짜 병합
    merged = pd.DataFrame({'STRC': strc_raw, 'apyUSD': apyusd_raw}).dropna()
    print(f"  공통   : {len(merged)}일")
    if len(merged) < 10:
        print("  [오류] 공통 데이터 10일 미만 — 분석 중단")
        sys.exit(1)

    # 2. 정규화 (par 대비 %)
    print(f"\n[2] 정규화  (STRC par={STRC_PAR}, apyUSD par={APYUSD_PAR})")
    strc_pct = (merged['STRC']   - STRC_PAR)   / STRC_PAR   * 100
    apy_pct  = (merged['apyUSD'] - APYUSD_PAR) / APYUSD_PAR * 100
    print(f"  STRC%   : {strc_pct.min():+.2f}% ~ {strc_pct.max():+.2f}%"
          f"  μ={strc_pct.mean():+.2f}%  σ={strc_pct.std():.2f}%")
    print(f"  apyUSD% : {apy_pct.min():+.2f}% ~ {apy_pct.max():+.2f}%"
          f"  μ={apy_pct.mean():+.2f}%  σ={apy_pct.std():.2f}%")

    # 3. 회귀 분석
    print(f"\n[3] 회귀 분석  (Y = apyUSD%,  X = STRC%)")
    corr, beta, intercept, r2 = calc_regression(strc_pct.values, apy_pct.values)
    print(f"  상관계수 (r)  : {corr:+.4f}")
    print(f"  베타 (a)      : {beta:+.4f}")
    print(f"  절편 (b)      : {intercept:+.4f}")
    print(f"  결정계수 (R²) : {r2:.4f}")
    print(f"\n  회귀식: apyUSD% ≈ {beta:+.4f} × STRC% + ({intercept:+.4f})")

    # 4. 현재가 기준 적정가 추정
    strc_last     = float(merged['STRC'].iloc[-1])
    apy_last      = float(merged['apyUSD'].iloc[-1])
    strc_last_pct = (strc_last - STRC_PAR) / STRC_PAR * 100
    apy_pred_pct  = beta * strc_last_pct + intercept
    apy_fair      = APYUSD_PAR * (1 + apy_pred_pct / 100)
    gap           = (apy_last - apy_fair) / apy_fair * 100

    print(f"\n{'─' * 58}")
    print("  [현재가 기준 적정가 추정]")
    print(f"  STRC 현재가     : ${strc_last:.4f}  ({strc_last_pct:+.2f}% vs par)")
    print(f"  apyUSD 현재가   : ${apy_last:.6f}")
    print(f"  apyUSD 모델 추정: ${apy_fair:.6f}  ({apy_pred_pct:+.2f}%)")
    print(f"  괴리            : {gap:+.2f}%  {'(고평가)' if gap > 0 else '(저평가)'}")
    print(f"{'─' * 58}")

    # 5. 최근 5일 비교 테이블
    print(f"\n  [최근 5일 비교]")
    print(f"  {'날짜':12s}  {'STRC':>8s}  {'STRC%':>7s}  "
          f"{'apyUSD':>10s}  {'모델추정':>10s}  {'괴리':>7s}")
    for dt, row in merged.tail(5).iterrows():
        sp   = (row['STRC']   - STRC_PAR)   / STRC_PAR   * 100
        fair = APYUSD_PAR * (1 + (beta * sp + intercept) / 100)
        g    = (row['apyUSD'] - fair) / fair * 100
        print(f"  {str(dt.date()):12s}  {row['STRC']:>8.4f}  {sp:>+6.2f}%  "
              f"  {row['apyUSD']:>10.6f}  {fair:>10.6f}  {g:>+6.2f}%")

    # 모델 저장 (봇에서 로드 가능)
    model = {
        'updated':    datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'days':       int(len(merged)),
        'corr':       round(float(corr),      6),
        'beta':       round(float(beta),      6),
        'intercept':  round(float(intercept), 6),
        'r2':         round(float(r2),        6),
        'strc_par':   STRC_PAR,
        'apyusd_par': APYUSD_PAR,
    }
    with open('strc_apy_model.json', 'w') as f:
        json.dump(model, f, indent=2)
    print(f"\n  [저장] strc_apy_model.json\n{SEP}\n")
    return model


if __name__ == '__main__':
    main()
