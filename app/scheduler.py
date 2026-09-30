"""
Step 1 - 완전 자동화 스케줄러.
settings.UNLOCK_REFRESH_INTERVAL_HOURS(기본 24시간, 즉 하루 1회) 주기로
DeFiLlama 전체 프로토콜의 락업 해제 데이터를 스캔해 캐시에 저장한다.

락업 일정은 몇 주 전에 미리 확정되는 경우가 대부분이라 실시간 갱신이
필요 없다 - 그래서 이 스케줄러는 짧은 주기로 돌지 않고, .env의
UNLOCK_REFRESH_INTERVAL_HOURS 값만 조정하면 갱신 빈도를 더 늦출 수도(예: 48)
있다.
"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.logic import refresh_unlock_cache
from app.config import settings

logger = logging.getLogger("alphapipeline.scheduler")
scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


async def _job():
    try:
        result = await refresh_unlock_cache()
        logger.info(
            "unlock cache refreshed: %d protocols scanned, %d flagged",
            result.get("protocols_scanned", 0),
            result.get("count", 0),
        )
    except Exception:
        logger.exception("unlock cache refresh failed")


async def _hip4_events_job():
    # app/events.py 모듈 docstring 참고 - 진짜 이벤트 푸시가 아니라 짧은 주기
    # 폴링 + diff다. HIP4_EVENTS_ENABLED=false(기본값)면 이 job 자체가
    # 등록되지 않으므로 기존 기능에는 아무 영향이 없다.
    from app.events import poll_once  # 순환 임포트 방지를 위해 job 실행 시점에 지연 임포트

    try:
        delivered = await poll_once()
        if delivered:
            logger.info("HIP-4 이벤트 폴링: %d건 웹훅 전달 시도", delivered)
    except Exception:
        logger.exception("HIP-4 이벤트 폴링 실패")


def start_scheduler():
    scheduler.add_job(
        _job,
        IntervalTrigger(hours=settings.UNLOCK_REFRESH_INTERVAL_HOURS),
        id="unlock_refresh",
        replace_existing=True,
    )
    if settings.HIP4_EVENTS_ENABLED:
        scheduler.add_job(
            _hip4_events_job,
            IntervalTrigger(seconds=settings.HIP4_EVENTS_POLL_INTERVAL_SECONDS),
            id="hip4_events_poll",
            replace_existing=True,
        )
        logger.info(
            "HIP-4 이벤트 폴링 활성화됨 (%d초 주기, 임계값 %.3f)",
            settings.HIP4_EVENTS_POLL_INTERVAL_SECONDS,
            settings.HIP4_EVENTS_JUMP_THRESHOLD_PCT,
        )
    scheduler.start()
