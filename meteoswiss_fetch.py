import requests
import csv
from datetime import datetime, timezone, timedelta

# Zurich timezone (CET = UTC+1, CEST = UTC+2)
try:
    from zoneinfo import ZoneInfo
    ZURICH_TZ = ZoneInfo("Europe/Zurich")
except ImportError:
    ZURICH_TZ = timezone(timedelta(hours=1))


def fetch_csv(url: str) -> list[dict]:
    """Download CSV from URL, parse and return list of dicts (in memory only)."""
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    reader = csv.DictReader(response.content.decode('utf-8').splitlines(), delimiter=';')
    return list(reader)

def _resolve_asset_name(year: int, month: int, current_year: int, current_month: int, station_id: str) -> str:
    """Determine which MeteoSwiss CSV file covers the given year/month."""
    if year < 2020:
        decade_start = (year // 10) * 10
        decade_end = decade_start + 9
        return f"ogd-smn_{station_id}_h_historical_{decade_start}-{decade_end}.csv"
    elif 2020 <= year <= 2029:
        if year < current_year:
            return f"ogd-smn_{station_id}_h_historical_2020-2029.csv"
        else:
            if month < current_month:
                return f"ogd-smn_{station_id}_h_recent.csv"
            else:
                return f"ogd-smn_{station_id}_h_now.csv"
    else:
        return f"ogd-smn_{station_id}_h_now.csv"


def _scan_rows(rows: list[dict], cutoff: datetime):
    """Find the latest data row whose reference_timestamp <= cutoff (Zurich-local)."""
    # Ensure cutoff is timezone-aware to avoid TypeError when comparing
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=ZURICH_TZ)
    best_dt = None
    best_data = None
    for row in rows:
        try:
            timestamp_str = row.get('reference_timestamp', '')
            dt_utc = datetime.strptime(timestamp_str, '%d.%m.%Y %H:%M').replace(tzinfo=timezone.utc)
            dt_local = dt_utc.astimezone(ZURICH_TZ)
            if dt_local <= cutoff and (best_dt is None or dt_local > best_dt):
                best_dt = dt_local
                h0_str = row.get('tre200h0')
                hx_str = row.get('tre200hx')
                hn_str = row.get('tre200hn')
                best_data = {
                    'h0': float(h0_str) if h0_str and h0_str.strip() else None,
                    'hx': float(hx_str) if hx_str and hx_str.strip() else None,
                    'hn': float(hn_str) if hn_str and hn_str.strip() else None,
                    'timestamp': timestamp_str,
                    'timestamp_zurich': best_dt.strftime('%Y-%m-%d %H:%M:%S'),
                }
        except (ValueError, KeyError, TypeError):
            continue
    return best_data


def fetch_for_hour(target_dt: datetime, station_id: str = "cgi") -> dict | None:
    """
    Fetch MeteoSwiss data for a specific Zurich-local hour.

    Unlike get_outdoor_data() which always queries "latest available",
    this function looks up a precise hour. Used by the backfill script.

    Args:
        target_dt: A Zurich-local datetime. The returned data will be the
                   hour <= target_dt (usually target_dt itself, floored to the hour).
        station_id: MeteoSwiss station code.
    Returns the same dict shape as get_outdoor_data(), or None.
    """
    try:
        now = datetime.now(ZURICH_TZ)
        base_url = f"https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/{station_id}"

        # Floor target to the hour for exact lookup
        cutoff = target_dt.replace(minute=0, second=0, microsecond=0)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=ZURICH_TZ)

        # Try now.csv first (current day only)
        asset_now = _resolve_asset_name(target_dt.year, target_dt.month, now.year, now.month, station_id)
        rows = fetch_csv(f"{base_url}/{asset_now}")
        data = _scan_rows(rows, cutoff)

        # If now.csv didn't have it, try recent.csv (full year up to yesterday)
        # needed when target_dt is in the current month but not today
        if data is None and asset_now.endswith("_now.csv"):
            recent_name = f"ogd-smn_{station_id}_h_recent.csv"
            try:
                rows = fetch_csv(f"{base_url}/{recent_name}")
                data = _scan_rows(rows, cutoff)
            except Exception:
                pass

        return data

    except Exception as e:
        print(f"MeteoSwiss fetch_for_hour({target_dt}) error: {e}")
        return None


def get_outdoor_data(station_id: str = "cgi"):
    """
    Fetch the latest available hourly temperature data from MeteoSwiss.

    Strategy:
    1. Try a 2-hour cutoff (normal case: data published by H:30 UTC).
    2. If that returns nothing, try a 4-hour cutoff (delayed data).
    3. If both fail, return None (gaps will be handled by the
       standalone backfill script).

    Returns a dict with:
        h0  - mean temperature for the hour (°C)
        hx  - max temperature for the hour (°C)
        hn  - min temperature for the hour (°C)
        timestamp - raw reference timestamp string (DD.MM.YYYY HH:MM UTC)
        timestamp_zurich - Zurich-local string (YYYY-MM-DD HH:MM:SS)
    Returns None on failure.
    """
    try:
        now_zurich = datetime.now(ZURICH_TZ)
        now_year = now_zurich.year
        now_month = now_zurich.month
        base_url = f"https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/{station_id}"

        # Try 2-hour cutoff first (normal operating window)
        cutoff_2h = now_zurich - timedelta(hours=2)
        asset_2h = _resolve_asset_name(cutoff_2h.year, cutoff_2h.month, now_year, now_month, station_id)
        rows_2h = fetch_csv(f"{base_url}/{asset_2h}")
        data = _scan_rows(rows_2h, cutoff_2h)

        # Fall back to 4-hour cutoff
        if data is None:
            cutoff_4h = now_zurich - timedelta(hours=4)
            asset_4h = _resolve_asset_name(cutoff_4h.year, cutoff_4h.month, now_year, now_month, station_id)
            if asset_4h != asset_2h:
                rows_4h = fetch_csv(f"{base_url}/{asset_4h}")
                data = _scan_rows(rows_4h, cutoff_4h)
            else:
                data = _scan_rows(rows_2h, cutoff_4h)
            if data is not None:
                print(f"MeteoSwiss: using 4h fallback (data age: {data['timestamp_zurich']})")

        # If now.csv didn't have it (e.g., data is from yesterday, not today),
        # try recent.csv which covers the full year up to yesterday
        if data is None and asset_2h.endswith("_now.csv"):
            recent_name = f"ogd-smn_{station_id}_h_recent.csv"
            try:
                rows_recent = fetch_csv(f"{base_url}/{recent_name}")
                data = _scan_rows(rows_recent, cutoff_2h)
                if data is not None:
                    print(f"MeteoSwiss: using recent.csv fallback ({data['timestamp_zurich']})")
            except Exception:
                pass

        return data

    except Exception as e:
        print(f"MeteoSwiss fetch error: {e}")
        return None


def get_outdoor_temp(station_id: str = "cgi"):
    """
    Convenience wrapper: returns just the mean temperature (h0).
    Kept for backward compatibility.
    """
    data = get_outdoor_data(station_id)
    return data['h0'] if data else None
