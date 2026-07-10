#!/usr/bin/env python3
"""
Backfill missing outdoor temperature data in the SQLite database.

MeteoSwiss publishes hourly data with ~30-minute delay. If the logger
runs before the data row for a given hour is published, outdoor columns
are stored as NULL. This script fetches the correct historical values
from MeteoSwiss and fills in the gaps.

Usage:
    python backfill_outdoor.py                  # backfill all time
    python backfill_outdoor.py --days 3         # last 3 days only
    python backfill_outdoor.py --dry-run        # show what would change
    python backfill_outdoor.py -d ~/scp/temperature.db  # custom DB path
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta

# Add the script's directory to the path so we can import meteoswiss_fetch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import meteoswiss_fetch
except Exception as e:
    print(f"FATAL: cannot import meteoswiss_fetch: {e}", flush=True)
    sys.exit(1)

DB_PATH = os.path.expanduser("~/temp_logger/data/temperature.db")
STATION_ID = "cgi"

# Outdoor data in the DB lags 2h behind indoor (set by the logger).
# The logger stores: outdoor_timestamp = now_dt - 2h
OUTDOOR_LAG_HOURS = 2


def get_null_rows(conn: sqlite3.Connection, days: int | None):
    """
    Return all rows where outdoor_temp_c IS NULL, optionally filtered
    to the last N days. Returns list of (id, timestamp) tuples.
    """
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            "SELECT id, timestamp FROM temperature_readings "
            "WHERE outdoor_temp_c IS NULL AND timestamp >= ? "
            "ORDER BY timestamp",
            (cutoff,),
        )
    else:
        cur = conn.execute(
            "SELECT id, timestamp FROM temperature_readings "
            "WHERE outdoor_temp_c IS NULL "
            "ORDER BY timestamp"
        )
    return cur.fetchall()


def backfill_row(conn: sqlite3.Connection, row_id: int, indoor_ts: str,
                 outdoor_data: dict, dry_run: bool) -> bool:
    """
    Apply already-fetched outdoor data to one row.
    Returns True if the row was updated.
    """
    try:
        from zoneinfo import ZoneInfo
        zurich = ZoneInfo("Europe/Zurich")
    except ImportError:
        zurich = None

    indoor_dt = datetime.strptime(indoor_ts, "%Y-%m-%d %H:%M:%S")
    if zurich:
        indoor_dt = indoor_dt.replace(tzinfo=zurich)

    outdoor_ts = (indoor_dt - timedelta(hours=OUTDOOR_LAG_HOURS)).strftime("%Y-%m-%d %H:%M:%S")

    if dry_run:
        print(
            f"  [DRY RUN] id={row_id}  indoor={indoor_ts}  "
            f"→ outdoor={outdoor_data['h0']:.1f}°C  (ref={outdoor_data['timestamp_zurich']})"
        )
        return False

    conn.execute(
        """UPDATE temperature_readings
           SET outdoor_temp_c = ?, outdoor_max_c = ?, outdoor_min_c = ?,
               outdoor_timestamp = ?
           WHERE id = ?""",
        (outdoor_data['h0'], outdoor_data['hx'], outdoor_data['hn'], outdoor_ts, row_id),
    )
    return True


def fetch_outdoor_for_rows(null_rows: list, dry_run: bool, batch_delay: float):
    """
    Group rows by unique outdoor hour, fetch MeteoSwiss data once per hour,
    then yield (row_id, indoor_ts, outdoor_data) for each row that can be backfilled.

    Only makes one HTTP request per unique outdoor hour — a huge reduction
    since many rows share the same hour.
    """
    import time
    from collections import defaultdict

    # Group rows by the outdoor hour they need (floor indoor_ts - 2h to the hour)
    hour_groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row_id, indoor_ts in null_rows:
        indoor_dt = datetime.strptime(indoor_ts, "%Y-%m-%d %H:%M:%S")
        target_dt = indoor_dt - timedelta(hours=OUTDOOR_LAG_HOURS)
        hour_key = target_dt.strftime("%Y-%m-%d %H:00")
        hour_groups[hour_key].append((row_id, indoor_ts))

    unique_hours = sorted(hour_groups.keys())
    print(f"  (grouped into {len(unique_hours)} unique outdoor hour(s))")

    # Fetch once per unique hour
    hourly_data: dict[str, dict | None] = {}
    fetch_count = 0

    for hour_key in unique_hours:
        # Parse the hour_key back to a datetime
        hour_dt = datetime.strptime(hour_key, "%Y-%m-%d %H:%M")
        try:
            from zoneinfo import ZoneInfo
            hour_dt = hour_dt.replace(tzinfo=ZoneInfo("Europe/Zurich"))
        except ImportError:
            pass

        fetch_count += 1
        print(f"  Fetching MeteoSwiss for {hour_key} ... ", end="", flush=True)
        try:
            data = meteoswiss_fetch.fetch_for_hour(hour_dt, STATION_ID)
        except Exception as e:
            print(f"✗ error: {e}")
            data = None

        if data and data.get('h0') is not None:
            print(f"✓ {data['h0']:.1f}°C")
            hourly_data[hour_key] = data
        else:
            print(f"✗ not available yet" if data is None else "✗ no data")
            hourly_data[hour_key] = None

        if fetch_count < len(unique_hours):
            time.sleep(batch_delay)

    # Yield rows with their data
    total_yielded = 0
    for hour_key in unique_hours:
        data = hourly_data.get(hour_key)
        if data is None:
            continue
        for row_id, indoor_ts in hour_groups[hour_key]:
            yield row_id, indoor_ts, data
            total_yielded += 1

    return total_yielded


def main():
    parser = argparse.ArgumentParser(
        description="Backfill missing outdoor temperature data from MeteoSwiss"
    )
    parser.add_argument(
        "-d", "--db",
        default=None,
        help=f"Path to the SQLite database (default: {DB_PATH})",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Backfill only the last N days (default: 7, use 0 for all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes",
    )
    parser.add_argument(
        "--batch-delay",
        type=float,
        default=0.5,
        help="Seconds to wait between MeteoSwiss HTTP requests (default: 0.5)",
    )
    args = parser.parse_args()

    # Flush early — some environments buffer stdout aggressively
    sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None
    print("Starting backfill...", flush=True)

    db_path = os.path.expanduser(args.db) if args.db else DB_PATH
    print(f"DB path: {db_path}", flush=True)
    if not os.path.exists(db_path):
        print(f"Error: database not found at {db_path}", file=sys.stderr, flush=True)
        sys.exit(1)

    days_arg = args.days if args.days > 0 else None

    conn = sqlite3.connect(db_path)
    try:
        null_rows = get_null_rows(conn, days_arg)
        total = len(null_rows)

        if total == 0:
            print("✓ No rows with missing outdoor data found.", flush=True)
            return

        print(f"Found {total} row(s) with missing outdoor data.", flush=True)
        if args.dry_run:
            print("--- DRY RUN (no changes will be made) ---", flush=True)

        # Fetch once per unique outdoor hour (not once per row!)
        updated = 0
        for row_id, indoor_ts, data in fetch_outdoor_for_rows(
            null_rows, args.dry_run, args.batch_delay
        ):
            if backfill_row(conn, row_id, indoor_ts, data, args.dry_run):
                updated += 1

        if not args.dry_run:
            conn.commit()
        skipped = total - updated
        if skipped > 0:
            print(
                f"\n{'✓' if updated > 0 else '⚠'} Backfill: {updated} updated, "
                f"{skipped} still missing (data not yet published by MeteoSwiss).",
                flush=True,
            )
        else:
            print(f"\n✓ Backfill complete: {updated}/{total} rows updated.", flush=True)
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
