import requests
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
import time
import os

CACHE_DIR = Path(os.path.expanduser('~/temp_logger/data/'))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Zurich timezone offset (CET = UTC+1, CEST = UTC+2)
# For simplicity, we'll use a fixed offset. For production, use zoneinfo if available.
try:
    from zoneinfo import ZoneInfo
    ZURICH_TZ = ZoneInfo("Europe/Zurich")
except ImportError:
    # Fallback for older Python without zoneinfo
    ZURICH_TZ = timezone(timedelta(hours=1))

def get_cached_csv(url: str, asset_name: str, refresh_hours: float = 1.0) -> list[dict]:
    """Downloads CSV if missing/stale, returns list of dicts"""
    local_file = CACHE_DIR / asset_name
    is_historical = "historical" in asset_name
    needs_download = True

    if local_file.exists():
        if is_historical:
            needs_download = False
        else:
            file_age_hours = (time.time() - local_file.stat().st_mtime) / 3600
            if file_age_hours <= refresh_hours:
                needs_download = False

    if needs_download:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        with open(local_file, 'wb') as f:
            f.write(response.content)

    # Parse CSV with semicolon separator
    rows = []
    with open(local_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            rows.append(row)
    
    return rows

def get_outdoor_temp(station_id: str = "cgi"):
    """
    Fetches the latest available temperature from MeteoSwiss.
    Returns the temperature in Celsius, or None if it fails.
    """
    try:
        now_zurich = datetime.now(ZURICH_TZ)
        target_dt_local = now_zurich
        
        current_year = now_zurich.year
        current_month = now_zurich.month
        year = target_dt_local.year
        month = target_dt_local.month

        if year < 2020:
            decade_start = (year // 10) * 10
            decade_end = decade_start + 9
            asset_name = f"ogd-smn_{station_id}_h_historical_{decade_start}-{decade_end}.csv"
            refresh_hours = float('inf')
        elif 2020 <= year <= 2029:
            if year < current_year:
                asset_name = f"ogd-smn_{station_id}_h_historical_2020-2029.csv"
                refresh_hours = float('inf')
            else:
                if month < current_month:
                    asset_name = f"ogd-smn_{station_id}_h_recent.csv"
                    refresh_hours = 24 * 7
                else:
                    asset_name = f"ogd-smn_{station_id}_h_now.csv"
                    refresh_hours = 1.0
        else:
            asset_name = f"ogd-smn_{station_id}_h_now.csv"
            refresh_hours = 1.0

        csv_url = f"https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/{station_id}/{asset_name}"
        rows = get_cached_csv(csv_url, asset_name, refresh_hours)

        # Parse timestamps and find closest match
        min_diff = None
        closest_temp = None
        
        for row in rows:
            try:
                # Parse "DD.MM.YYYY HH:MM" format
                timestamp_str = row.get('reference_timestamp', '')
                dt_utc = datetime.strptime(timestamp_str, '%d.%m.%Y %H:%M').replace(tzinfo=timezone.utc)
                dt_local = dt_utc.astimezone(ZURICH_TZ)
                
                diff = abs((dt_local - target_dt_local).total_seconds())
                
                if min_diff is None or diff < min_diff:
                    min_diff = diff
                    # tre200h0 is the temperature column
                    closest_temp = float(row.get('tre200h0', 0))
            except (ValueError, KeyError):
                continue
        
        return closest_temp
        
    except Exception as e:
        print(f"MeteoSwiss fetch error: {e}")
        return None
