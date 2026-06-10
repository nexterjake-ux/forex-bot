import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import pytz
from config import (
    NEGATIVE_GAP_THRESHOLD,
    NEUTRAL_UPPER_THRESHOLD,
    TIMEZONE,
    CURRENCY_PAIR,
    UPBIT_TICKER_URL,
    CALENDAR_EVENTS,
)

class ForexAnalyzer:
    def __init__(self):
        self.tz = pytz.timezone(TIMEZONE)
        self.currency = CURRENCY_PAIR
        self.current_price = None
        self.previous_close = None
        self.daily_data = []

    def fetch_data(self, period="5d"):
        """Yahoo Finance에서 USD/KRW 데이터 수집"""
        try:
            if isinstance(period, str) and period.endswith('d'):
                days = int(period[:-1])
            else:
                days = 5
            return self.fetch_yahoo_history(days)
        except Exception as e:
            print(f"데이터 수집 실패: {e}")
            return None

    def fetch_yahoo_history(self, days=5):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                              '(KHTML, like Gecko) Chrome/115.0 Safari/537.36'
            }
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{self.currency}"
                f"?interval=1d&range={days}d"
            )
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            chart = payload.get('chart', {}).get('result')
            if not chart:
                return None
            chart = chart[0]
            timestamps = chart.get('timestamp') or []
            quote = chart.get('indicators', {}).get('quote', [{}])[0]
            opens = quote.get('open', [])
            highs = quote.get('high', [])
            lows = quote.get('low', [])
            closes = quote.get('close', [])
            volumes = quote.get('volume', [])
            rows = []
            for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes):
                rows.append({
                    'datetime': datetime.fromtimestamp(ts, pytz.utc).astimezone(self.tz),
                    'open': o,
                    'high': h,
                    'low': l,
                    'close': c,
                    'volume': v,
                })
            return pd.DataFrame(rows)
        except Exception as e:
            print(f"Yahoo Finance 데이터 조회 실패: {e}")
            return None

    def get_latest_price(self):
        """현재 환율 조회"""
        try:
            df = self.fetch_yahoo_history(5)
            if df is not None and not df.empty and 'close' in df.columns:
                valid = df.dropna(subset=['close'])
                if len(valid) > 0:
                    self.current_price = float(valid['close'].iloc[-1])
                    return self.current_price
        except Exception as e:
            print(f"현재가 조회 실패: {e}")

        try:
            resp = requests.get('https://open.er-api.com/v6/latest/USD', timeout=10)
            result = resp.json()
            rate = result.get('rates', {}).get('KRW')
            if rate is not None:
                self.current_price = float(rate)
                return self.current_price
        except Exception as e:
            print(f"대체 환율 API 조회 실패: {e}")

        try:
            resp = requests.get('https://api.exchangerate-api.com/v4/latest/USD', timeout=10)
            result = resp.json()
            rate = result.get('rates', {}).get('KRW')
            if rate is not None:
                self.current_price = float(rate)
                return self.current_price
        except Exception as e:
            print(f"대체 환율 API 조회 실패: {e}")

        return None

    def _normalize_daily_history(self, data):
        df = data.copy()
        df['date'] = df['datetime'].dt.date
        daily = df.sort_values('datetime').groupby('date', as_index=False).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        })
        return daily

    def calculate_ndf_gap(self):
        """NDF 갭 계산: (오늘 시가 - 전날 종가) / 전날 종가 * 100"""
        try:
            data = self.fetch_yahoo_history(10)
            if data is None or data.empty:
                return None
            daily = self._normalize_daily_history(data)
            valid = daily.dropna(subset=['open', 'close'])
            if len(valid) < 2:
                return None
            prev_close = valid['close'].iloc[-2]
            today_open = valid['open'].iloc[-1]
            if prev_close == 0 or today_open is None:
                return None
            self.previous_close = prev_close
            gap_percent = ((today_open - prev_close) / prev_close) * 100
            gap_amount = today_open - prev_close
            return {
                'percent': round(gap_percent, 2),
                'amount': round(gap_amount, 2)
            }
        except Exception as e:
            print(f"NDF 갭 계산 실패: {e}")
            return None

    def get_b_pattern_count(self):
        """지난 2일 연속 B패턴 여부 확인"""
        try:
            data = self.fetch_yahoo_history(6)
            if data is None or data.empty:
                return 0
            valid = data.dropna(subset=['open', 'close'])
            if len(valid) < 3:
                return 0
            gaps = []
            for i in range(1, len(valid)):
                prev_close = valid['close'].iloc[i-1]
                today_open = valid['open'].iloc[i]
                if prev_close == 0 or today_open is None:
                    continue
                gap = ((today_open - prev_close) / prev_close) * 100
                gaps.append(gap)
            return 2 if len(gaps) >= 2 and gaps[-1] > 0.3 and gaps[-2] > 0.3 else 0
        except Exception as e:
            print(f"B패턴 계산 실패: {e}")
            return 0

    def fetch_upbit_premium(self, usd_krw):
        """업비트 KRW-USDT 가격으로 역프/김프 계산"""
        try:
            resp = requests.get(UPBIT_TICKER_URL, timeout=10)
            data = resp.json()
            if isinstance(data, list) and data:
                upbit_usdt = float(data[0].get('trade_price', 0))
                premium = ((upbit_usdt - usd_krw) / usd_krw) * 100
                return round(premium, 2)
        except Exception as e:
            print(f"업비트 데이터 조회 실패: {e}")
        return None

    def fetch_upbit_price_and_premium(self, usd_krw):
        """업비트 USDT 가격과 역프/김프를 함께 조회"""
        try:
            resp = requests.get(UPBIT_TICKER_URL, timeout=10)
            data = resp.json()
            if isinstance(data, list) and data:
                upbit_usdt = float(data[0].get('trade_price', 0))
                premium = ((upbit_usdt - usd_krw) / usd_krw) * 100
                return round(upbit_usdt, 2), round(premium, 2)
        except Exception as e:
            print(f"업비트 데이터 조회 실패: {e}")
        return None, None

    def get_event_status(self, target_date=None):
        """이벤트 여부 확인"""
        if target_date is None:
            target_date = datetime.now(self.tz).date()
        date_str = target_date.strftime('%Y-%m-%d')
        return CALENDAR_EVENTS.get(date_str, [])

    def get_seasonality_score(self, current_date=None):
        """계절성 점수 계산"""
        if current_date is None:
            current_date = datetime.now(self.tz).date()
        if current_date.month in (6, 7):
            return 10, '계절성 OK'
        return -10, '계절성 주의'

    def score_trade(self, gap_percent, premium, event_day, b_pattern_count=0):
        """점수제 판단 시스템"""
        score = 0
        details = []

        if gap_percent is not None:
            if gap_percent < 0:
                score += 40
                details.append('NDF 갭 마이너스: +40점')
            if gap_percent > 0.3:
                score -= 40
                details.append('NDF 갭 +0.3% 초과: -40점')

        if premium is not None:
            if premium <= -0.5:
                score += 20
                details.append('역프 -0.5% 이하: +20점')
            elif premium <= 0:
                score += 10
                details.append('역프 0%~-0.5%: +10점')
            if 0 < premium <= 1:
                score -= 10
                details.append('김프 0%~+1%: -10점')
            elif premium > 1:
                score -= 30
                details.append('김프 +1% 초과: -30점')

        if event_day:
            score -= 50
            details.append('이벤트 당일: -50점')
        else:
            score += 20
            details.append('이벤트 없음: +20점')

        season_score, season_label = self.get_seasonality_score()
        score += season_score
        details.append(f'{season_label}: {season_score:+d}점')

        if b_pattern_count >= 2:
            score -= 50
            details.append('B패턴 2일 연속: -50점')

        final_score = max(min(score, 100), -999)
        action = self.get_action_by_score(final_score)
        next_action = self.get_next_action(action, final_score)

        return {
            'score': final_score,
            'action': action,
            'next_action': next_action,
            'details': details
        }

    def get_action_by_score(self, score):
        if score >= 90:
            return '최강 매수'
        if score >= 70:
            return '매수 권장'
        if score >= 50:
            return '조건부 매수'
        if score >= 30:
            return '관망'
        if score >= 0:
            return '스킵'
        return '매도 검토'

    def get_next_action(self, action, score):
        if action == '매도 검토':
            return '익일 9시 매도 실행'
        if score >= 90:
            return '9시 개장 직후 즉시 매수'
        if score >= 70:
            return '9시 개장 후 매수 권장'
        if score >= 50:
            return '9시 10분 후 확인 후 진입'
        if score >= 30:
            return '오늘 관망, 내일 재확인'
        return '오늘 스킵'


if __name__ == '__main__':
    analyzer = ForexAnalyzer()
    gap = analyzer.calculate_ndf_gap()
    price = analyzer.get_latest_price()
    premium = analyzer.fetch_upbit_premium(price if price else 1300)
    event = analyzer.get_event_status()
    b_pattern_count = analyzer.get_b_pattern_count()
    score = analyzer.score_trade(gap, premium, bool(event), b_pattern_count=b_pattern_count)
    print(gap, price, premium, event, b_pattern_count, score)
