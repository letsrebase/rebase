"""The figures the cash pages and the schedule read from the server, never from the browser
(spec 2026-11-09, due items and the cash schedule, §§ 4 and 5)."""

# A movement that stays `unmatched` this long is flagged (`is_overdue_unmatched`).
UNMATCHED_OLD_DAYS = 14
# Days after a statement period ends before a missing statement is reported.
STATEMENT_GRACE_DAYS = 7
# How long a balance may go without a movement or a statement before it reads as stale,
# before the grace: a monthly account is read about once a month, a quarterly one once a
# quarter.
MONTHLY_PERIOD_DAYS = 31
QUARTERLY_PERIOD_DAYS = 92
