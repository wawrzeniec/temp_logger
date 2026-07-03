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

def get_outdoor_data(station_id: str = "cgi"):
    """
    Fetches the latest available hourly temperature data from MeteoSwiss.
    Data is typically posted ~30 minutes past the hour (UTC) and represents
    the previous hour's measurements. We use a 2-hour cutoff to guarantee
    the data has already been published when we query it.

    Returns a dict with:
        h0  - mean temperature for the hour (°C)
        hx  - max temperature for the hour (°C)
        hn  - min temperature for the hour (°C)
        timestamp - the raw reference timestamp string (DD.MM.YYYY HH:MM UTC)
        timestamp_zurich - reference timestamp as Zurich-local string (YYYY-MM-DD HH:MM:SS)
    Returns None if it fails.
    """
    try:
        now_zurich = datetime.now(ZURICH_TZ)
        # Always look back at least 2 hours to ensure the data row has
        # been published (rows for H:00 UTC appear at ~H:30 UTC).
        cutoff = now_zurich - timedelta(hours=2)
        
        current_year = now_zurich.year
        current_month = now_zurich.month
        year = cutoff.year
        month = cutoff.month

        if year < 2020:
            decade_start = (year // 10) * 10
            decade_end = decade_start + 9
            asset_name = f"ogd-smn_{station_id}_h_historical_{decade_start}-{decade_end}.csv"
        elif 2020 <= year <= 2029:
            if year < current_year:
                asset_name = f"ogd-smn_{station_id}_h_historical_2020-2029.csv"
            else:
                if month < current_month:
                    asset_name = f"ogd-smn_{station_id}_h_recent.csv"
                else:
                    asset_name = f"ogd-smn_{station_id}_h_now.csv"
        else:
            asset_name = f"ogd-smn_{station_id}_h_now.csv"

        csv_url = f"https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/{station_id}/{asset_name}"
        rows = fetch_csv(csv_url)

        # Find the latest data point whose timestamp <= cutoff
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
            except (ValueError, KeyError):
                continue
        
        return best_data
        
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
