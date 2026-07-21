from __future__ import annotations

import inspect

import skill_api
from src.app.account_nav_recorder_service import AccountNavRecorderService
from src.app.daily_account_nav_service import DailyAccountNavService
from src.app.nav_record_service import NavRecordService
from src.feishu._nav_mixin import NavMixin
from src.feishu.repositories.nav_history_repository import NavHistoryRepository
from src.portfolio import PortfolioManager
from src.service.application import PortfolioService
from src.service.client import PortfolioServiceClient
from src.service.http import DailyReportBundleRequest, NavRecordRequest


def _default(callable_obj, parameter: str):
    return inspect.signature(callable_obj).parameters[parameter].default


def test_public_nav_write_defaults_do_not_overwrite_existing_rows():
    callables = [
        NavHistoryRepository._write_one_nav_record,
        NavHistoryRepository.write_nav_record,
        NavMixin.write_nav_record,
        NavRecordService.record_nav,
        AccountNavRecorderService.record,
        DailyAccountNavService.run,
        PortfolioManager.record_nav,
        PortfolioService.record_nav,
        PortfolioService.daily_report_bundle,
        PortfolioServiceClient.record_nav,
        PortfolioServiceClient.daily_report_bundle,
        skill_api.PortfolioSkill.daily_report_bundle,
        skill_api.PortfolioSkill.close_nav,
        skill_api.PortfolioSkill.record_nav,
        skill_api.record_nav,
        skill_api.daily_report_bundle,
        skill_api.close_nav,
    ]

    assert all(_default(callable_obj, 'overwrite_existing') is False for callable_obj in callables)
    assert NavRecordRequest().overwrite_existing is False
    assert DailyReportBundleRequest().overwrite_existing is False
