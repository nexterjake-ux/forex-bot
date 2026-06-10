import os

# Telegram Bot Configuration (GitHub Secrets 또는 환경변수에서 읽음)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "여기에입력")
CHAT_ID = os.environ.get("CHAT_ID", "여기에입력")

# Schedule Configuration
BRIEFING_TIME = "08:50"  # KST
CHECK_TIMES = ["09:00", "13:00", "14:50"]  # KST

# Threshold Configuration
NEGATIVE_GAP_THRESHOLD = 0  # 매수 추천: 갭 < 0%
NEUTRAL_UPPER_THRESHOLD = 0.3  # 관망: 0% ~ +0.3%
ALERT_THRESHOLD = 0.5  # 환율 급변 감지: ±0.5%

# Data Configuration
DATA_ARCHIVE_FILE = "usd_krw_history.csv"
PAPER_TRADING_FILE = "paper_trading.csv"
TIMEZONE = "Asia/Seoul"
SIMULATION_START = "2026-06-10"
SIMULATION_END = "2026-07-05"
STARTING_SEED = 10000000
EVENT_WARNING_TIME = "20:00"
WEEKLY_REPORT_DAY = "Sunday"
FINAL_REPORT_DATE = "2026-07-05"
FINAL_REPORT_TIME = "23:59"

# Yahoo Finance Symbol
CURRENCY_PAIR = "USDKRW=X"

# Upbit API
UPBIT_TICKER_URL = "https://api.upbit.com/v1/ticker?markets=KRW-USDT"

# Economic Calendar Events
CALENDAR_EVENTS = {
    "2026-06-11": ["FOMC"],
    "2026-06-12": ["CPI"],
    "2026-07-03": ["NFP"]
}
CURRENCY_PAIR = "USDKRW=X"
