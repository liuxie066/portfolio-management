"""Installed Linux unit identities shared by rendering and controlled upgrades."""

SERVICE_NAME = "portfolio-nav-daily.service"
TIMER_NAME = "portfolio-nav-daily.timer"
EVENING_SERVICE_NAME = "portfolio-futu-evening.service"
EVENING_TIMER_NAME = "portfolio-futu-evening.timer"
CASH_FLOW_SERVICE_NAME = "portfolio-cash-flow-scan.service"
CASH_FLOW_TIMER_NAME = "portfolio-cash-flow-scan.timer"
API_SERVICE_NAME = "portfolio-management-api.service"
QUALITY_SERVICE_NAME = "portfolio-quality-refresh.service"
QUALITY_TIMER_NAME = "portfolio-quality-refresh.timer"
RECEIPT_SERVICE_NAME = "portfolio-receipt-dispatch.service"
RECEIPT_TIMER_NAME = "portfolio-receipt-dispatch.timer"
HOLDINGS_EVENT_SERVICE_NAME = "portfolio-holdings-event-listener.service"
FEISHU_PREFLIGHT_SERVICE_NAME = "portfolio-feishu-preflight.service"
SCHEDULE_LOCK_FILE = "/var/lock/portfolio-management-scheduled.lock"
QUALITY_LOCK_FILE = "/var/lock/portfolio-management-quality.lock"
RECEIPT_LOCK_FILE = "/var/lock/portfolio-management-receipts.lock"

SERVICES = (API_SERVICE_NAME, HOLDINGS_EVENT_SERVICE_NAME)
TIMERS = (TIMER_NAME, EVENING_TIMER_NAME, CASH_FLOW_TIMER_NAME, QUALITY_TIMER_NAME, RECEIPT_TIMER_NAME)
JOBS = (SERVICE_NAME, EVENING_SERVICE_NAME, CASH_FLOW_SERVICE_NAME, QUALITY_SERVICE_NAME, RECEIPT_SERVICE_NAME)
JOB_LOCKS = (SCHEDULE_LOCK_FILE, QUALITY_LOCK_FILE, RECEIPT_LOCK_FILE)
