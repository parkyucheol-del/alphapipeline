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


def start_scheduler():
    scheduler.add_job(
        _job,
        IntervalTrigger(hours=settings.UNLOCK_REFRESH_INTERVAL_HOURS),
        id="unlock_refresh",
        replace_existing=True,
    )
    scheduler.start()
