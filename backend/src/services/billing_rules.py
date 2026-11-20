"""Pure billing rules (stdlib only) so they can be checked without a database.

All months are interpreted in Beijing time (UTC+08:00).  A migrated row carries
``measured_at`` as a UTC ISO timestamp; the detection month is therefore always
derived by converting back to Beijing time first.  This matters at month
boundaries: ``2026-10-31T16:30:00+00:00`` is 2026-11 in Beijing.
"""
import re
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
MONTH_PATTERN = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')


def valid_month(month):
    return isinstance(month, str) and bool(MONTH_PATTERN.match(month))


def beijing_month(value):
    """Return the YYYY-MM Beijing-time month for a measured_at value."""
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError('invalid_measured_at')
    if moment.tzinfo is None:
        # Same convention as the migration converter: naive input means Beijing.
        moment = moment.replace(tzinfo=BEIJING)
    return moment.astimezone(BEIJING).strftime('%Y-%m')


def next_month(month):
    year, number = int(month[:4]), int(month[5:7])
    return '%04d-%02d' % (year + 1, 1) if number == 12 else '%04d-%02d' % (year, number + 1)


def resolve_billing_month(detection_month, closed_before_submit):
    """Attribute a detection month to the first month not closed at submit time.

    ``closed_before_submit`` is the set of months whose close request was
    submitted before this migration run.  If the detected month was already
    closed, the row moves forward to the first month that was still open at
    submission time.  Months closed *after* submission never move an in-flight
    row, which is how submit order wins a close/migration race.
    """
    month = detection_month
    guard = 0
    while month in closed_before_submit:
        month = next_month(month)
        guard += 1
        if guard > 10000:
            raise ValueError('billing_month_resolution_failed')
    return month
