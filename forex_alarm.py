"""
USD/KRW 환율 텔레그램 알람 봇
매일 08:50 KST에 자동 실행되어 환율 판단을 텔레그램으로 발송합니다.
"""

from scheduler import ForexScheduler
import logging

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """메인 실행 함수"""
    print("""
╔═══════════════════════════════════════════════════════════════════╗
║                  USD/KRW 환율 텔레그램 알람 봇                    ║
║                                                                   ║
║  📊 기능:                                                          ║
║    • 매일 08:50 KST 자동 실행 → 텔레그램 브리핑 발송            ║
║    • USD/KRW NDF 갭 계산 및 Upbit 김프/역프 확인                 ║
║    • 경제 이벤트 자동 스킵/사전 경고                               ║
║    • 점수 기반 판단 및 페이퍼 트레이딩 시뮬레이션                  ║
║    • 환율 급변 감지 및 주간/최종 리포트 발송                      ║
║                                                                   ║
║  📋 설정 필요:                                                     ║
║    1. config.py에서 BOT_TOKEN 입력                                ║
║    2. config.py에서 CHAT_ID 입력                                  ║
║                                                                   ║
╚═══════════════════════════════════════════════════════════════════╝
    """)

    try:
        scheduler = ForexScheduler()
        scheduler.run()
    except KeyboardInterrupt:
        logger.info("사용자에 의해 봇이 중단되었습니다.")
        print("\n[종료] 봇을 종료합니다.")
    except Exception as e:
        logger.error(f"예상치 못한 오류가 발생했습니다: {e}")
        print(f"\n[오류] {e}")
        raise


if __name__ == "__main__":
    main()
