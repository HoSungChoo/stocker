"""매일 아침 보고서 스케줄러. 화~토 REPORT_TIME(KST)에 run_daily를 돌린다.

미국 장은 KST 기준 다음 날 새벽에 끝나므로, 월요일 아침은 금요일과 같은 데이터다."""

from __future__ import annotations

import logging
import re

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from stocker.report import env

logger = logging.getLogger(__name__)

TIMEZONE = "Asia/Seoul"
DEFAULT_TIME = "07:30"
DAYS = "tue-sat"


def parse_report_time(text: str | None = None) -> tuple[int, int]:
    """'HH:MM' → (시, 분). 생략하면 REPORT_TIME 환경변수, 그것도 없으면 07:30."""
    text = text or env("REPORT_TIME", DEFAULT_TIME)
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text)
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ValueError(f"REPORT_TIME은 HH:MM 형식(KST)이어야 한다: {text!r}")
    return int(m[1]), int(m[2])


def cron_trigger(report_time: str | None = None) -> CronTrigger:
    hour, minute = parse_report_time(report_time)
    return CronTrigger(day_of_week=DAYS, hour=hour, minute=minute, timezone=TIMEZONE)


def _job() -> None:
    """실패해도 스케줄러가 죽지 않게 모든 예외를 로그로만 남긴다."""
    from stocker.report.pipeline import run_daily

    try:
        report = run_daily()
        logger.info("보고서 완료: %s, 채널 %s", report.as_of.date(), report.channels)
    except Exception:  # noqa: BLE001
        logger.exception("보고서 생성 실패")


def main(run_now: bool = False) -> None:
    """BlockingScheduler를 띄운다. run_now면 시작 직후 1회 실행."""
    trigger = cron_trigger()
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        _job, trigger, id="daily_report", coalesce=True, max_instances=1, misfire_grace_time=3600
    )
    logger.info("스케줄 등록: 화~토 %s KST", env("REPORT_TIME", DEFAULT_TIME))
    if run_now:
        _job()
    scheduler.start()
