import csv
import json
import os
import requests
from datetime import datetime
import pytz
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "여기에입력")
CHAT_ID = os.environ.get("CHAT_ID", "여기에입력")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Seoul")
DATA_ARCHIVE_FILE = os.environ.get("DATA_ARCHIVE_FILE", "usd_krw_history.csv")
STARTING_SEED = int(os.environ.get("STARTING_SEED", "10000000"))


class TelegramSender:
    def __init__(self):
        self.bot_token = BOT_TOKEN
        self.chat_id = CHAT_ID
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        self.tz = pytz.timezone(TIMEZONE)

    def send_message(self, text):
        if self.bot_token == "여기에입력" or self.chat_id == "여기에입력":
            print("[경고] BOT_TOKEN 또는 CHAT_ID가 설정되지 않았습니다.")
            print(f"메시지:\n{text}")
            return False
        try:
            payload = {
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': 'HTML'
            }
            response = requests.post(
                self.api_url,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                headers={'Content-Type': 'application/json; charset=utf-8'},
                timeout=10
            )
            if response.status_code == 200:
                print("[전송] 텔레그램 메시지 발송 성공")
                return True
            print(f"[실패] 텔레그램 발송 실패: {response.status_code}")
            return False
        except Exception as e:
            print(f"[오류] 메시지 발송 중 오류 발생: {e}")
            return False

    def archive_data(self, row):
        file_exists = os.path.exists(DATA_ARCHIVE_FILE)
        with open(DATA_ARCHIVE_FILE, 'a', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)


def build_market_summary(usd_krw=None, upbit_price=None, premium=None):
    usd_label = 'N/A' if usd_krw is None else f"{usd_krw:,.0f}원"
    upbit_label = 'N/A' if upbit_price is None else f"{upbit_price:,.0f}원"
    premium_label = 'N/A' if premium is None else f"{premium:+.2f}%"
    return (
        f'💱 현재 시세\n'
        f'USD/KRW:      {usd_label}\n'
        f'업비트 USDT:  {upbit_label}\n'
        f'역프/김프:    {premium_label}\n'
        f'━━━━━━━━━━━━━━'
    )


def send_score_briefing(score_info, gap_percent, gap_amount, premium, event_name, portfolio, market_summary=None):
    score_text = f"{score_info['score']}점 / {score_info['action']}"
    score_lines = '\n'.join(f"- {item}" for item in score_info['details'])
    gap_label = 'N/A'
    if gap_percent is not None and gap_amount is not None:
        gap_label = f"{gap_amount:+,.2f}원 ({gap_percent:+.2f}%)"
    elif gap_percent is not None:
        gap_label = f"{gap_percent:+.2f}%"
    premium_label = 'N/A'
    if premium is not None:
        premium_label = f"{premium:+.2f}%"
    event_label = event_name if event_name else '없음'
    if portfolio['position'] > 0:
        portfolio_text = (
            f"상태: 보유 중\n"
            f"매수가: {portfolio['avg_cost']:,.0f}원 (업비트 USDT)\n"
            f"보유량: {portfolio['position']:,.0f} USDT\n"
            f"평가금액: {portfolio['equity']:,.0f}원\n"
            f"승률: {portfolio['win_rate']}% (매도 후 계산)"
        )
    else:
        portfolio_text = (
            f"상태: 대기 중\n"
            f"현금: {portfolio['cash']:,.0f}원\n"
            f"누적손익: {portfolio['realized_profit']:,.0f}원"
        )
    header = f"{market_summary}\n\n" if market_summary else ''

    message = f"""
{header}<b>① 점수 & 판단: {score_text}</b>

<b>② 항목별 점수 내역</b>
{score_lines}

<b>③ 지표</b>
- NDF 갭: {gap_label}
- 역프/김프: {premium_label}
- 이벤트: {event_label}

<b>④ 가상 포트폴리오 현황</b>
{portfolio_text}

<b>⑤ 다음 액션</b>
{score_info['next_action']}

<i>업데이트: {datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')}</i>
""".strip()
    sender = TelegramSender()
    sender.send_message(message)

    archive_row = {
        '날짜': datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d'),
        'NDF갭(%)': f"{gap_percent:+.2f}%" if gap_percent is not None else 'N/A',
        '프리미엄(%)': premium_label,
        '판단결과': score_info['action'],
        '점수': score_info['score'],
        '이벤트': event_label,
        '현금': f"{portfolio['cash']:,.0f}",
        '포지션': f"{portfolio['position']:.4f}",
        '자산가치': f"{portfolio['equity']:,.0f}",
        '기록시간': datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M:%S')
    }
    TelegramSender().archive_data(archive_row)


def send_weekly_report(portfolio, market_summary=None):
    header = f"{market_summary}\n\n" if market_summary else ''
    message = f"""
{header}<b>주간 리포트</b>

- 현금: {portfolio['cash']:,.0f}원
- 보유 수량: {portfolio['position']:.4f}
- 평균 단가: {portfolio['avg_cost']:,.2f}원
- 자산 가치: {portfolio['equity']:,.0f}원
- 총 거래: {portfolio['trade_count']}
- 승률: {portfolio['win_rate']}%
- 최대 낙폭: {portfolio['max_drawdown']:,.0f}원

<i>주간 점검을 통해 다음 주 전략을 준비하세요.</i>
""".strip()
    TelegramSender().send_message(message)


def send_final_report(portfolio, history, market_summary=None):
    total_profit = portfolio['equity'] - STARTING_SEED
    total_return = (portfolio['equity'] / STARTING_SEED - 1) * 100
    suggestion = '7월 6일 이후는 시장 변동성에 따라 보수적인 진입과 손절 선 설정이 필요합니다.'
    header = f"{market_summary}\n\n" if market_summary else ''
    message = f"""
{header}<b>최종 리포트</b>

- 최종 시드: {portfolio['equity']:,.0f}원
- 전체 수익률: {total_return:+.2f}%
- 승률: {portfolio['win_rate']}%
- 최대 낙폭: {portfolio['max_drawdown']:,.0f}원
- 거래 횟수: {portfolio['trade_count']}

<b>전체 거래 내역</b>
"""
    for row in history[-5:]:
        price_val = float(row['price'])
        pl_val = float(row['profit_loss'])
        message += f"\n- {row['datetime']} {row['type']} {price_val:,.2f}원 {row['units']}단위 P/L {pl_val:,.0f}원"
    message += f"\n\n<b>전략 제언</b>\n{suggestion}"
    TelegramSender().send_message(message)


def send_afternoon_check(score_info, gap_percent, b_pattern_count, premium, portfolio, sell_score=0, trade_price=None, ndf_streak=False, market_summary=None):
    if sell_score >= 90:
        grade = '강력 매도 (즉시 실행)'
        next_action = '즉시 매도 실행'
    elif sell_score >= 70:
        grade = '매도 추천 (익일 9시 매도)'
        next_action = '익일 9시 매도 실행'
    elif sell_score >= 50:
        grade = '매도 보류 (재확인 필요)'
        next_action = '매도 보류, 내일 재확인'
    elif sell_score >= 30:
        grade = '매도 비추천 (홀딩 유지)'
        next_action = '홀딩 유지'
    else:
        grade = '홀딩 (시그널 없음)'
        next_action = '홀딩 유지, 시그널 없음'

    b_label = f'연속 {b_pattern_count}일 ⚠️' if b_pattern_count >= 2 else '없음'
    ndf_label = '3일 연속 플러스 ⚠️' if ndf_streak else ('플러스' if gap_percent is not None and gap_percent > 0 else '마이너스' if gap_percent is not None else 'N/A')
    if premium is not None:
        premium_label = f'김프 ({premium:+.2f}%) ⚠️' if premium >= 0 else f'역프 ({premium:+.2f}%)'
    else:
        premium_label = 'N/A'

    avg_cost = portfolio.get('avg_cost', 0)
    cur = trade_price if trade_price else (portfolio['equity'] / portfolio['position'] if portfolio.get('position', 0) > 0 else 0)
    if avg_cost > 0 and cur > 0:
        profit_pct = (cur - avg_cost) / avg_cost * 100
        profit_label = f"{profit_pct:+.2f}% (매수가 {avg_cost:,.0f}원 → 현재 {cur:,.0f}원)"
    else:
        profit_label = 'N/A'

    header = f"{market_summary}\n" if market_summary else ''
    message = f"""{header}<b>🕒 14:50 매도 타이밍 체크</b>

매도 점수: {sell_score}점 — {grade}
━━━━━━━━━━━━━━
<b>매도 근거</b>
- 수익률 +1% 이상: {'예 ⚠️' if avg_cost > 0 and cur > 0 and (cur - avg_cost) / avg_cost * 100 >= 1 else '아니오'}
- 역프 축소 (-0.5%~0%): {'예 ⚠️' if premium is not None and -0.5 < premium < 0 else '아니오'}
- 김프 전환 (≥0%): {'예 ⚠️' if premium is not None and premium >= 0 else '아니오'}
- B패턴: {b_label}
- NDF 갭: {ndf_label}
━━━━━━━━━━━━━━
<b>📊 가상 포트폴리오</b>
상태: 보유 중
매수가: {avg_cost:,.0f}원 (업비트 USDT)
보유량: {portfolio['position']:,.0f} USDT
평가금액: {portfolio['equity']:,.0f}원
현재 수익률: {profit_label}
━━━━━━━━━━━━━━
<b>🎯 다음 액션: {next_action}</b>

<i>{datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')}</i>""".strip()
    TelegramSender().send_message(message)


def send_alert(title, content, action, market_summary=None):
    emoji_map = {
        '매수': '🟢',
        '관망': '🟡',
        '스킵': '⚪',
        '매도': '🔴',
        '상승': '📈',
        '하락': '📉',
        '확인': 'ℹ️',
        '경고': '⚠️'
    }
    emoji = emoji_map.get(action, '❗')
    header = f"{market_summary}\n\n" if market_summary else ''
    message = f"""
{header}<b>{emoji} {title}</b>

{content}

<i>{datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')}</i>
""".strip()
    TelegramSender().send_message(message)
