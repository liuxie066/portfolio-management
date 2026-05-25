import unittest
from unittest.mock import Mock

from skill_api import PortfolioSkill


class TestGenerateReportDailyNavMetrics(unittest.TestCase):
    def test_generate_report_daily_includes_nav_metrics_backward_compatible(self):
        skill = PortfolioSkill.__new__(PortfolioSkill)
        snapshot = {
            'snapshot_time': '2026-03-29T08:00:00',
            'overview': {},
        }

        full_payload = {
            'success': True,
            'overview': {
                'total_value': 1000.0,
                'cash_ratio': 0.2,
                'stock_ratio': 0.8,
                'fund_ratio': 0.0,
            },
            'nav': {
                'date': '2026-03-29',
                'nav': 1.234567,
                'total_value': 1000.0,
                'cash_flow': 10.0,
                'pnl': 5.0,
                'mtd_nav_change': 0.0123,
                'ytd_nav_change': 0.0456,
                'mtd_pnl': 12.3,
                'ytd_pnl': 45.6,
                'details': {},
            },
            'returns': {
                'since_inception': {
                    'success': True,
                    'cagr_pct': 8.88,
                }
            },
            'top_holdings': [],
            'warnings': [],
        }

        skill.full_report = Mock(return_value=full_payload)

        result = skill.generate_report(report_type='daily', snapshot=snapshot, navs=[])

        self.assertTrue(result['success'])
        # backward compatibility: old fields still present
        self.assertEqual(result['nav'], 1.234567)
        self.assertEqual(result['total_value'], 1000.0)
        self.assertEqual(result['cash_flow'], 10.0)
        # newly exposed daily NAV metrics
        self.assertEqual(result['pnl'], 5.0)
        self.assertEqual(result['mtd_nav_change'], 0.0123)
        self.assertEqual(result['ytd_nav_change'], 0.0456)
        self.assertEqual(result['mtd_pnl'], 12.3)
        self.assertEqual(result['ytd_pnl'], 45.6)

    def test_generate_report_does_not_record_nav(self):
        skill = PortfolioSkill.__new__(PortfolioSkill)
        skill.full_report = Mock(return_value={
            'success': True,
            'overview': {},
            'nav': {'date': '2026-03-29', 'nav': 1.0, 'details': {}},
            'returns': {},
            'top_holdings': [],
        })
        skill.record_nav = Mock(side_effect=AssertionError('record_nav should not run'))

        result = skill.generate_report(
            report_type='daily',
            snapshot={'snapshot_time': '2026-03-29T08:00:00'},
            navs=[],
        )

        self.assertTrue(result['success'])
        skill.record_nav.assert_not_called()

    def test_generate_report_daily_uses_nav_override_for_recorded_nav(self):
        skill = PortfolioSkill.__new__(PortfolioSkill)
        snapshot = {
            'snapshot_time': '2026-03-29T08:00:00',
            'overview': {},
        }

        skill.full_report = Mock(return_value={
            'success': True,
            'overview': {'total_value': 1000.0},
            'nav': {
                'date': '2026-03-29',
                'nav': 9.999999,
                'total_value': 9999.0,
                'pnl': 999.0,
                'details': {'is_synthetic': True},
            },
            'returns': {},
            'top_holdings': [],
            'warnings': [],
        })

        result = skill.generate_report(
            report_type='daily',
            snapshot=snapshot,
            navs=[],
            nav_override={
                'date': '2026-03-29',
                'nav': 1.234567,
                'total_value': 1000.0,
                'cash_flow': 10.0,
                'pnl': 5.0,
                'mtd_nav_change': 0.0123,
                'details': {'cagr_pct': 8.88},
            },
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['nav'], 1.234567)
        self.assertEqual(result['total_value'], 1000.0)
        self.assertEqual(result['pnl'], 5.0)
        self.assertEqual(result['mtd_nav_change'], 0.0123)
        self.assertEqual(result['cagr_pct'], 8.88)


if __name__ == '__main__':
    unittest.main()
