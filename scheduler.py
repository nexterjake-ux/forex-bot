import csv

import os

import time

from datetime import datetime, timedelta

import pytz

import schedule

from analyzer import ForexAnalyzer

from telegram_sender import (

    build_market_summary,

    send_score_briefing,

    send_afternoon_check,

    send_alert,

    send_weekly_report,

    send_final_report,

)

from config import (

    BRIEFING_TIME,

    CHECK_TIMES,

    EVENT_WARNING_TIME,

    FINAL_REPORT_DATE,

    FINAL_REPORT_TIME,

    TIMEZONE,

    DATA_ARCHIVE_FILE,

    PAPER_TRADING_FILE,

    SIMULATION_START,

    SIMULATION_END,

    STARTING_SEED,

    CALENDAR_EVENTS,

)



class PaperPortfolio:

    def __init__(self, file_path, seed):

        self.file_path = file_path

        self.cash = float(seed)

        self.position = 0.0

        self.avg_cost = 0.0

        self.history = []

        self.equity_history = []

        self.load()



    def load(self):

        if not os.path.exists(self.file_path):

            return

        try:

            with open(self.file_path, newline='', encoding='utf-8-sig') as f:

                reader = csv.DictReader(f)

                for row in reader:

                    self.history.append(row)

        except Exception:

            pass



    def save_trade(self, trade_type, price, units, cash_change, profit_loss):

        now = datetime.now(pytz.timezone(TIMEZONE))

        row = {

            'datetime': now.strftime('%Y-%m-%d %H:%M:%S'),

            'type': trade_type,

            'price': f'{price:.2f}',

            'units': f'{units:.4f}',

            'cash_change': f'{cash_change:.0f}',

            'profit_loss': f'{profit_loss:.0f}',

            'cash': f'{self.cash:.0f}',

            'position': f'{self.position:.4f}',

            'avg_cost': f'{self.avg_cost:.2f}'

        }

        file_exists = os.path.exists(self.file_path)

        with open(self.file_path, 'a', newline='', encoding='utf-8-sig') as f:

            writer = csv.DictWriter(f, fieldnames=list(row.keys()))

            if not file_exists:

                writer.writeheader()

            writer.writerow(row)

        self.history.append(row)



    def buy(self, price):

        if self.position > 0:

            return False

        units = self.cash / price if price > 0 else 0

        if units <= 0:

            return False

        self.position = units

        self.avg_cost = price

        self.cash = 0.0

        self.save_trade('BUY', price, units, -units * price, 0.0)

        return True



    def sell(self, price):

        if self.position <= 0:

            return False

        proceeds = self.position * price

        profit_loss = proceeds - (self.position * self.avg_cost)

        self.cash += proceeds

        units = self.position

        self.position = 0.0

        self.avg_cost = 0.0

        self.save_trade('SELL', price, units, proceeds, profit_loss)

        return True



    def equity(self, market_price):

        return self.cash + self.position * market_price



    def stats(self, market_price):

        total_value = self.equity(market_price)

        win_trades = [float(r['profit_loss']) for r in self.history if r['type'] == 'SELL']

        wins = [p for p in win_trades if p > 0]

        win_rate = int((len(wins) / len(win_trades) * 100)) if win_trades else 0

        total_trades = len(win_trades)

        peak = 0

        max_dd = 0

        for value in self.equity_history:

            peak = max(peak, value)

            drawdown = peak - value

            max_dd = max(max_dd, drawdown)

        realized_profit = sum(float(r['profit_loss']) for r in self.history if r.get('type') == 'SELL')

        return {

            'cash': self.cash,

            'position': self.position,

            'avg_cost': self.avg_cost,

            'equity': total_value,

            'win_rate': win_rate,

            'total_trades': total_trades,

            'max_drawdown': max_dd,

            'trade_count': len(self.history),

            'realized_profit': realized_profit

        }



    def update_equity(self, market_price):

        self.equity_history.append(self.equity(market_price))





class ForexScheduler:

    def __init__(self):

        self.tz = pytz.timezone(TIMEZONE)

        self.analyzer = ForexAnalyzer()

        self.portfolio = PaperPortfolio(PAPER_TRADING_FILE, STARTING_SEED)

        self.last_price = None

        self.final_report_sent = False



    def _today(self):

        return datetime.now(self.tz).date()



    def _tomorrow(self):

        return self._today() + timedelta(days=1)



    def is_simulation_active(self):

        today = self._today()

        start = datetime.strptime(SIMULATION_START, '%Y-%m-%d').date()

        end = datetime.strptime(SIMULATION_END, '%Y-%m-%d').date()

        return start <= today <= end



    def get_event_name(self, target_date=None):

        events = self.analyzer.get_event_status(target_date)

        return ', '.join(events) if events else None



    def should_skip_for_event(self):

        return bool(self.get_event_name(self._today()))



    def schedule_tasks(self):

        schedule.every().day.at(BRIEFING_TIME).do(self.daily_briefing)

        schedule.every().day.at(CHECK_TIMES[0]).do(self.morning_check)

        schedule.every().day.at(CHECK_TIMES[1]).do(self.midday_check)

        schedule.every().day.at(CHECK_TIMES[2]).do(self.afternoon_check)

        schedule.every().day.at(EVENT_WARNING_TIME).do(self.event_warning)

        schedule.every().sunday.at("09:00").do(self.weekly_report)

        schedule.every().day.at(FINAL_REPORT_TIME).do(self.daily_final_report_check)

        schedule.every(5).minutes.do(self.check_price_alert)

        print("[스케줄] 모든 작업이 등록되었습니다.")



    def daily_briefing(self):

        print("\n[08:50] 일일 브리핑 시작...")

        if self.should_skip_for_event():

            event_name = self.get_event_name(self._today())

            send_alert(

                title=f"이벤트 당일 자동 스킵: {event_name}",

                content="오늘은 경제 이벤트가 예정되어 있어 브리핑을 자동으로 생략합니다.",

                action="스킵"

            )

            return



        usd_krw = self.analyzer.get_latest_price()

        gap_info = self.analyzer.calculate_ndf_gap()

        gap_percent = gap_info['percent'] if gap_info else None

        gap_amount = gap_info['amount'] if gap_info else None

        upbit_price, premium = self.analyzer.fetch_upbit_price_and_premium(usd_krw) if usd_krw else (None, None)

        trade_price = upbit_price if upbit_price else usd_krw

        event_today = bool(self.get_event_name(self._today()))

        event_name = self.get_event_name(self._today())

        b_pattern_count = self.analyzer.get_b_pattern_count()

        score = self.analyzer.score_trade(gap_percent, premium, event_today, b_pattern_count=b_pattern_count)



        if self.is_simulation_active() and score['score'] >= 70:

            if self.portfolio.buy(trade_price):

                print('[페이퍼] 매수 기록됨')

        if self.portfolio.position > 0 and score['score'] < 0 and self.is_simulation_active():

            if self.portfolio.sell(trade_price):

                print('[페이퍼] 매도 기록됨')



        self.portfolio.update_equity(trade_price if trade_price else 0)

        portfolio_summary = self.portfolio.stats(trade_price if trade_price else 0)



        market_summary = build_market_summary(usd_krw, upbit_price, premium)

        send_score_briefing(

            score_info=score,

            gap_percent=gap_percent,

            gap_amount=gap_amount,

            premium=premium,

            event_name=event_name,

            portfolio=portfolio_summary,

            market_summary=market_summary,

        )



    def morning_check(self):

        print("[09:00] 아침 재확인 알람...")

        usd_krw = self.analyzer.get_latest_price()

        gap_info = self.analyzer.calculate_ndf_gap()

        gap_percent = gap_info['percent'] if gap_info else None

        gap_amount = gap_info['amount'] if gap_info else None

        premium = self.analyzer.fetch_upbit_premium(usd_krw) if usd_krw else None

        score = self.analyzer.score_trade(gap_percent, premium, bool(self.get_event_name(self._today())), b_pattern_count=self.analyzer.get_b_pattern_count())

        if score['action'] == '관망':

            send_alert(

                title="09:00 관망 재확인",

                content="추가 신호를 대기 중입니다.",

                action=score['action']

            )



    def midday_check(self):

        print("[13:00] 중간 점검...")

        if self.portfolio.position > 0:

            current_price = self.analyzer.get_latest_price()

            send_alert(

                title="13:00 포지션 점검",

                content=f"현재 환율: {current_price:.2f}\n포지션을 유지하거나 손절을 검토하세요.",

                action="확인"

            )



    def afternoon_check(self):

        print("[14:50] 매도 타이밍 체크...")

        if self.portfolio.position <= 0:

            return

        usd_krw = self.analyzer.get_latest_price()

        gap_info = self.analyzer.calculate_ndf_gap()

        gap_percent = gap_info['percent'] if gap_info else None

        upbit_price, premium = self.analyzer.fetch_upbit_price_and_premium(usd_krw) if usd_krw else (None, None)

        trade_price = upbit_price if upbit_price else usd_krw

        b_pattern_count = self.analyzer.get_b_pattern_count()

        score = self.analyzer.score_trade(gap_percent, premium, bool(self.get_event_name(self._today())), b_pattern_count=b_pattern_count)

        market_summary = build_market_summary(usd_krw, upbit_price, premium)

        portfolio_summary = self.portfolio.stats(trade_price if trade_price else 0)

        sell_score = 0

        if b_pattern_count >= 2:

            sell_score += 40

        if gap_percent is not None and gap_percent > 0:

            sell_score += 30

        if premium is not None and premium > 0:

            sell_score += 30

        send_afternoon_check(

            score_info=score,

            gap_percent=gap_percent,

            b_pattern_count=b_pattern_count,

            premium=premium,

            portfolio=portfolio_summary,

            sell_score=sell_score,

            market_summary=market_summary,

        )

        if sell_score >= 70:

            if self.is_simulation_active() and self.portfolio.sell(trade_price):

                print('[페이퍼] 매도 기록됨')



    def event_warning(self):

        tomorrow_event = self.get_event_name(self._tomorrow())

        if tomorrow_event:

            send_alert(

                title="경제 이벤트 사전경고",

                content=f"내일 예정된 이벤트: {tomorrow_event}\n거래를 신중히 준비하세요.",

                action="경고"

            )



    def check_price_alert(self):

        current_price = self.analyzer.get_latest_price()

        if current_price is None:

            return

        if self.last_price is not None:

            change_percent = ((current_price - self.last_price) / self.last_price) * 100

            if abs(change_percent) >= 0.5:

                direction = '상승' if change_percent > 0 else '하락'

                send_alert(

                    title="환율 급변 감지",

                    content=f"{direction} {abs(change_percent):.2f}%\n현재: {current_price:.2f}",

                    action=direction

                )

        self.last_price = current_price



    def weekly_report(self):

        if not self.is_simulation_active():

            return

        usd_krw = self.analyzer.get_latest_price()

        upbit_price, premium = self.analyzer.fetch_upbit_price_and_premium(usd_krw) if usd_krw else (None, None)

        trade_price = upbit_price if upbit_price else usd_krw

        market_summary = build_market_summary(usd_krw, upbit_price, premium)

        portfolio_summary = self.portfolio.stats(trade_price if trade_price else 0)

        send_weekly_report(portfolio_summary, market_summary=market_summary)



    def daily_final_report_check(self):

        if self.final_report_sent:

            return

        if self._today().strftime('%Y-%m-%d') == FINAL_REPORT_DATE:

            usd_krw = self.analyzer.get_latest_price()

            upbit_price, premium = self.analyzer.fetch_upbit_price_and_premium(usd_krw) if usd_krw else (None, None)

            trade_price = upbit_price if upbit_price else usd_krw

            market_summary = build_market_summary(usd_krw, upbit_price, premium)

            portfolio_summary = self.portfolio.stats(trade_price if trade_price else 0)

            send_final_report(portfolio_summary, self.portfolio.history, market_summary=market_summary)

            self.final_report_sent = True



    def run(self):

        self.schedule_tasks()

        print("[시작] ForEx 환율 봇이 시작되었습니다.")

        try:

            while True:

                schedule.run_pending()

                time.sleep(10)

        except KeyboardInterrupt:

            print("\n[종료] 봇을 종료합니다.")



if __name__ == '__main__':

    scheduler = ForexScheduler()

    scheduler.run()

