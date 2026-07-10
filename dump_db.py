#!/usr/bin/env python3
"""
Dump the SQLite temperature database to CSV or Parquet.

Usage:
    python dump_db.py                  # → data/temperature.csv (default)
    python dump_db.py --format parquet # → data/temperature.parquet
    python dump_db.py -o ~/export.csv  # custom output path
    python dump_db.py --days 7         # last 7 days only
"""

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime, timedelta

DB_PATH = os.path.expanduser("~/temp_logger/data/temperature.db")
DEFAULT_OUT = os.path.expanduser("~/temp_logger/data/temperature_export")


def get_columns(conn: sqlite3.Connection) -> list[str]:
    """Return ordered list of column names from temperature_readings (excluding id)."""
    cur = conn.execute("PRAGMA table_info(temperature_readings)")
    return [row[1] for row in cur.fetchall() if row[1] != "id"]


def fetch_rows(conn: sqlite3.Connection, columns: list[str], days: int | None):
    """Yield rows from the database, optionally filtered to the last N days."""
    cols_str = ", ".join(columns)
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            f"SELECT {cols_str} FROM temperature_readings WHERE timestamp >= ? ORDER BY timestamp",
            (cutoff,),
        )
    else:
        cur = conn.execute(
            f"SELECT {cols_str} FROM temperature_readings ORDER BY timestamp"
        )
    yield columns  # header row first
    for row in cur:
        yield row


def dump_csv(conn: sqlite3.Connection, output_path: str, days: int | None):
    columns = get_columns(conn)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        count = 0
        for row in fetch_rows(conn, columns, days):
            if count == 0:
                count += 1
                continue  # skip header
            writer.writerow(row)
            count += 1
    file_size = os.path.getsize(output_path)
    print(f"✓ CSV exported: {output_path}  ({count - 1:,} rows, {file_size / 1024:.1f} KB)")


def dump_parquet(conn: sqlite3.Connection, output_path: str, days: int | None):
    try:
        import pandas as pd
    except ImportError:
        print(
            "Error: pandas is required for Parquet export.\n"
            "Install it with:  pip install pandas pyarrow\n"
            "Or use --format csv instead.",
            file=sys.stderr,
        )
        sys.exit(1)

    columns = get_columns(conn)
    rows = list(fetch_rows(conn, columns, days))
    header = rows[0]
    data = rows[1:]
    df = pd.DataFrame(data, columns=header)

    # Convert timestamp columns to proper datetime types
    for col in ("timestamp", "outdoor_timestamp"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Convert numeric columns
    for col in columns:
        if col not in ("timestamp", "outdoor_timestamp"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df.to_parquet(output_path, index=False, compression="zstd")
    file_size = os.path.getsize(output_path)
    print(
        f"✓ Parquet exported: {output_path}  "
        f"({len(df):,} rows, {file_size / 1024:.1f} KB)"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Dump temperature SQLite DB to CSV or Parquet"
    )
    parser.add_argument(
        "-d", "--db",
        default=None,
        help=f"Path to the SQLite database (default: {DB_PATH})",
    )
    parser.add_argument(
        "-f", "--format",
        choices=["csv", "parquet"],
        default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output file path (default: data/temperature_export.<ext>)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Export only the last N days of data",
    )
    args = parser.parse_args()

    db_path = os.path.expanduser(args.db) if args.db else DB_PATH

    if not os.path.exists(db_path):
        print(f"Error: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = os.path.expanduser(args.output)
    else:
        ext = ".parquet" if args.format == "parquet" else ".csv"
        output_path = DEFAULT_OUT + ext

    conn = sqlite3.connect(db_path)
    try:
        if args.format == "parquet":
            dump_parquet(conn, output_path, args.days)
        else:
            dump_csv(conn, output_path, args.days)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
