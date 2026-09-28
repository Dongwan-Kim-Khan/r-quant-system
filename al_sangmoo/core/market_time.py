"""DST-aware US Eastern / Korea session clocks.

US equities use America/New_York (EST/EDT). KRX uses Asia/Seoul (no DST).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

_KST_FIXED = timezone(timedelta(hours=9))


def _zone(name: str, fallback: timezone):
    if ZoneInfo is None:
        return fallback
    try:
        return ZoneInfo(name)
    except Exception:
        return fallback


KST = _zone("Asia/Seoul", _KST_FIXED)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """weekday: Monday=0 ... Sunday=6. n is 1-based."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return date(year, month, 1 + offset + 7 * (n - 1))


def _us_eastern_offset(utc_dt: datetime) -> timedelta:
    """US Eastern offset at a UTC instant: EDT UTC-4, EST UTC-5 (post-2007 rules)."""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    else:
        utc_dt = utc_dt.astimezone(timezone.utc)
    year = utc_dt.year
    start_local = datetime.combine(_nth_weekday(year, 3, 6, 2), datetime.min.time().replace(hour=2))
    end_local = datetime.combine(_nth_weekday(year, 11, 6, 1), datetime.min.time().replace(hour=2))
    # Interpret wall times in EST (UTC-5) for the start, EDT (UTC-4) for the end.
    start_utc = (start_local - timedelta(hours=5)).replace(tzinfo=timezone.utc)
    end_utc = (end_local - timedelta(hours=4)).replace(tzinfo=timezone.utc)
    if start_utc <= utc_dt < end_utc:
        return timedelta(hours=-4)
    return timedelta(hours=-5)


def now_us_eastern(now: Optional[datetime] = None) -> datetime:
    if now is not None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        try:
            ny = _zone("America/New_York", timezone(_us_eastern_offset(now)))
            return now.astimezone(ny)
        except Exception:
            return now.astimezone(timezone(_us_eastern_offset(now)))
    try:
        ny = ZoneInfo("America/New_York") if ZoneInfo is not None else None
        if ny is not None:
            return datetime.now(ny)
    except Exception:
        pass
    utc_now = datetime.now(timezone.utc)
    return utc_now.astimezone(timezone(_us_eastern_offset(utc_now)))


def now_kst(now: Optional[datetime] = None) -> datetime:
    if now is not None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(KST)
    return datetime.now(KST)


def _previous_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def us_equity_session_date(now: Optional[datetime] = None) -> str:
    """Active US cash-session date. Before 09:30 America/New_York, use the prior weekday."""
    now_ny = now_us_eastern(now)
    if (now_ny.hour < 9) or (now_ny.hour == 9 and now_ny.minute < 30):
        d = (now_ny - timedelta(days=1)).date()
    else:
        d = now_ny.date()
    return _previous_weekday(d).strftime("%Y-%m-%d")


def kr_equity_session_date(now: Optional[datetime] = None) -> str:
    now_kr = now_kst(now)
    if now_kr.hour < 9:
        d = (now_kr - timedelta(days=1)).date()
    else:
        d = now_kr.date()
    return _previous_weekday(d).strftime("%Y-%m-%d")


def session_date_for_ticker(ticker_sym: str, now: Optional[datetime] = None) -> str:
    sym = str(ticker_sym or "").upper()
    if sym.endswith(".KS") or sym.endswith(".KQ"):
        return kr_equity_session_date(now)
    return us_equity_session_date(now)


def is_us_half_day(dt: Optional[date | datetime] = None) -> bool:
    """
    Returns True if dt is a recognized US equity half-day session (early close at 13:00 ET).
    Standard NYSE/Nasdaq scheduled early-close days:
      1. Day after Thanksgiving (Black Friday, 4th Friday of November).
      2. Christmas Eve (Dec 24) when falling on Monday-Thursday.
      3. July 3rd when falling on a weekday preceding July 4th holiday (Tuesday-Friday).
    """
    if dt is None:
        d = now_us_eastern().date()
    elif isinstance(dt, datetime):
        d = now_us_eastern(dt).date()
    else:
        d = dt

    if d.weekday() >= 5:
        return False

    year = d.year
    # 1. Day after Thanksgiving (4th Friday of November)
    thanksgiving = _nth_weekday(year, 11, 3, 4)  # 4th Thursday (weekday=3)
    black_friday = thanksgiving + timedelta(days=1)
    if d == black_friday:
        return True

    # 2. Christmas Eve (Dec 24) when Mon-Thu
    if d.month == 12 and d.day == 24 and d.weekday() < 4:
        return True

    # 3. July 3rd when Mon-Thu and July 4 is Tue-Fri
    if d.month == 7 and d.day == 3 and d.weekday() < 4:
        july4_weekday = date(year, 7, 4).weekday()
        if 1 <= july4_weekday <= 4:
            return True

    return False


def get_us_market_close_minute(now: Optional[datetime] = None) -> int:
    """Returns market close minute of the day in ET (780 for 13:00, 960 for 16:00)."""
    now_ny = now_us_eastern(now)
    return (13 * 60) if is_us_half_day(now_ny.date()) else (16 * 60)


def is_us_regular_hours(now: Optional[datetime] = None) -> bool:
    now_ny = now_us_eastern(now)
    if now_ny.weekday() >= 5:
        return False
    secs = (now_ny.hour * 60 + now_ny.minute) * 60 + now_ny.second
    close_secs = get_us_market_close_minute(now_ny) * 60
    open_secs = (9 * 60 + 30) * 60
    return open_secs <= secs <= close_secs


def is_us_eod_window(now: Optional[datetime] = None, window_minutes: int = 10) -> bool:
    """
    Returns True if current US Eastern time is within the final session window before close.
    - Regular close is 16:00 ET -> window is 15:50 ~ 16:00 ET (for 10 min) or 15:45 ~ 16:00 (for 15 min).
    - Half-day close is 13:00 ET -> window is 12:50 ~ 13:00 ET.
    - Inclusive through 16:00:00 ET, exclusive after (16:00:05 ET is post-close).
    Automatically DST-aware via America/New_York:
      EDT (summer): 04:50 ~ 05:00 KST
      EST (winter): 05:50 ~ 06:00 KST
    """
    now_ny = now_us_eastern(now)
    if now_ny.weekday() >= 5:
        return False
    secs = (now_ny.hour * 60 + now_ny.minute) * 60 + now_ny.second
    close_secs = get_us_market_close_minute(now_ny) * 60
    start_secs = close_secs - max(1, int(window_minutes)) * 60
    return start_secs <= secs <= close_secs


def is_kr_regular_hours(now: Optional[datetime] = None) -> bool:
    now_kr = now_kst(now)
    if now_kr.weekday() >= 5:
        return False
    secs = (now_kr.hour * 60 + now_kr.minute) * 60 + now_kr.second
    open_secs = (9 * 60) * 60
    close_secs = (15 * 60 + 30) * 60
    return open_secs <= secs <= close_secs


def is_regular_hours_for_ticker(ticker_sym: str, now: Optional[datetime] = None) -> bool:
    sym = str(ticker_sym or "").upper()
    if sym.endswith(".KS") or sym.endswith(".KQ"):
        return is_kr_regular_hours(now)
    return is_us_regular_hours(now)


def is_eod_window_for_ticker(ticker_sym: str, now: Optional[datetime] = None, window_minutes: int = 10) -> bool:
    """Returns True if the asset's primary exchange is within its final closing window."""
    sym = str(ticker_sym or "").upper()
    if sym.endswith(".KS") or sym.endswith(".KQ"):
        now_kr = now_kst(now)
        if now_kr.weekday() >= 5:
            return False
        secs = (now_kr.hour * 60 + now_kr.minute) * 60 + now_kr.second
        close_secs = (15 * 60 + 30) * 60
        start_secs = close_secs - max(1, int(window_minutes)) * 60
        return start_secs <= secs <= close_secs
    return is_us_eod_window(now, window_minutes)

