import src.app as app
import src.domain as domain


def test_app_exports_public_services():
    expected = {
        "AccountNavRecorderService",
        "AuditService",
        "AccountService",
        "AssetNameService",
        "BusinessCalendarService",
        "CashFlowSummaryService",
        "CashService",
        "CompensationService",
        "DailyAccountNavService",
        "DailyNavJobService",
        "DailyReportPayloadService",
        "FutuBalanceProvider",
        "FutuBalanceSnapshot",
        "FutuBalanceSyncService",
        "FutuOpenApiBalanceProvider",
        "NavBaselineService",
        "NavInitializationService",
        "NavPreviewService",
        "NavReadService",
        "NavRecordService",
        "NavSummaryPrinter",
        "PortfolioReadService",
        "ReportGenerationService",
        "ReportQueryService",
        "ReportingService",
        "ShareService",
        "SnapshotService",
        "TradeService",
        "ValuationService",
        "snapshot_digest",
    }

    assert set(app.__all__) == expected
    for name in expected:
        assert hasattr(app, name)


def test_domain_exports_public_helpers():
    expected = {
        "NavCalculator",
        "NavHistoryIndex",
        "NavPerformanceCalculator",
        "PayloadNormalizer",
        "merge_top_holdings",
    }

    assert set(domain.__all__) == expected
    for name in expected:
        assert hasattr(domain, name)
