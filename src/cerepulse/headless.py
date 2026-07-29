"""Headless sync — refreshes the cache without opening a window.

Useful three ways: as a smoke test that the whole stack still works against the live
portal, as a way to warm the cache before first launch, and as the command a scheduled
task would run.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from loguru import logger

from cerepulse.app import build_app
from cerepulse.capture import MissingCredentialsError, resolve_credentials
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import CerePulseError
from cerepulse.core.logging_setup import workflow
from cerepulse.intelligence.month import MonthAnalysis
from cerepulse.services.sync import SyncReport


def run_sync(
    *,
    config: AppConfig,
    secrets_path: str,
    year: int | None = None,
    month: int | None = None,
    backfill: bool = True,
    history: int | None = None,
    force: bool = False,
) -> int:
    """Entry point for ``cerepulse sync``. Returns a process exit code."""
    try:
        username, password = resolve_credentials(Path(secrets_path), config)
    except MissingCredentialsError as exc:
        print(exc, file=sys.stderr)
        return 2

    with workflow("sync"), build_app(config=config) as app:
        try:
            employee_code = app.sign_in(username, password)
        except CerePulseError as exc:
            logger.error("Sign-in failed: {}", exc)
            return 1

        today = date.today()
        report = app.sync.sync_all(
            employee_code,
            year=year or today.year,
            month=month or today.month,
            today=today,
            backfill=backfill,
        )

        if history is not None:
            # 0 means "use the configured length"; argparse const for a bare --history.
            history_report = app.sync.run(
                lambda: app.attendance.backfill_history(
                    employee_code,
                    months=history or None,
                    today=today,
                    force=force,
                    on_progress=_log_progress,
                )
            )
            logger.info(history_report.summary)
            for failure in history_report.failures:
                logger.warning("  {}", failure)

        view = app.attendance.load_month(
            employee_code, year or today.year, month or today.month, today=today
        )
        _summarize(report, view.analysis, view.pending_detail)
        app.sign_out()

    return 0 if report.succeeded else 1


def _log_progress(done: int, total: int, period: tuple[int, int]) -> bool:
    logger.info("  [{}/{}] {:04d}-{:02d}", done, total, *period)
    return True


def _summarize(report: SyncReport, analysis: MonthAnalysis, pending: int) -> None:
    logger.info("=" * 70)
    logger.info(
        "Synced in {:.1f}s — {} day(s) of detail fetched, {} still pending",
        report.duration_seconds,
        report.detail_days_fetched,
        pending,
    )
    logger.info(
        "Worked {} of {} across {} working day(s); {} short, {} overtime",
        analysis.total_worked,
        analysis.month_target,
        analysis.working_days_elapsed,
        analysis.short_days,
        analysis.total_overtime,
    )
    if analysis.unmeasured_days:
        logger.info(
            "{} day(s) excluded from the bank — the portal holds no punches for them",
            analysis.unmeasured_days,
        )
    if analysis.is_ahead:
        logger.success("Hours bank: {} ahead", analysis.bank_delta)
    else:
        logger.warning(
            "Hours bank: {} behind — {} per day over the remaining {} day(s) to finish even",
            analysis.bank_delta,
            analysis.required_daily_average,
            analysis.working_days_remaining,
        )

    for failure in report.failures:
        logger.error("Failed: {}", failure)
