import serial
import sqlite3
import time
from datetime import datetime, timedelta
import os
import meteoswiss_fetch # Import our new module

DB_PATH = os.path.expanduser('~/temp_logger/data/temperature.db')
SERIAL_PORT = '/dev/ttyACM0'
BAUD_RATE = 9600
STATION_ID = "cgi" # Nyon/Changins

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS temperature_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            arduino_millis INTEGER,
            raw_voltage REAL,
            temperature_c REAL NOT NULL,
            outdoor_temp_c REAL,
            outdoor_max_c REAL,
            outdoor_min_c REAL,
            outdoor_timestamp TEXT
        )
    ''')
    
    # Migration: Add columns if upgrading from an older DB version
    cursor.execute("PRAGMA table_info(temperature_readings)")
    columns = [info[1] for info in cursor.fetchall()]
    if 'raw_voltage' not in columns:
        cursor.execute("ALTER TABLE temperature_readings ADD COLUMN raw_voltage REAL")
        print("Added raw_voltage column to database")
    if 'outdoor_temp_c' not in columns:
        cursor.execute("ALTER TABLE temperature_readings ADD COLUMN outdoor_temp_c REAL")
        print("Added outdoor_temp_c column to database")
    if 'outdoor_max_c' not in columns:
        cursor.execute("ALTER TABLE temperature_readings ADD COLUMN outdoor_max_c REAL")
        print("Added outdoor_max_c column to database")
    if 'outdoor_min_c' not in columns:
        cursor.execute("ALTER TABLE temperature_readings ADD COLUMN outdoor_min_c REAL")
        print("Added outdoor_min_c column to database")
    if 'outdoor_timestamp' not in columns:
        cursor.execute("ALTER TABLE temperature_readings ADD COLUMN outdoor_timestamp TEXT")
        print("Added outdoor_timestamp column to database")
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON temperature_readings(timestamp)')
    conn.commit()
    conn.close()

def insert_reading(timestamp, arduino_millis, raw_voltage, temp_c, outdoor_temp_c, outdoor_max_c=None, outdoor_min_c=None, outdoor_timestamp=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO temperature_readings (timestamp, arduino_millis, raw_voltage, temperature_c, outdoor_temp_c, outdoor_max_c, outdoor_min_c, outdoor_timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, arduino_millis, raw_voltage, temp_c, outdoor_temp_c, outdoor_max_c, outdoor_min_c, outdoor_timestamp))
    conn.commit()
    conn.close()

def main():
    init_db()
    time.sleep(2)
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
    print("Logger started. Waiting for data...")
    
    try:
        while True:
            line = ser.readline().decode('utf-8').strip()
            if line:
                data = line.split(',')
                if len(data) == 3:
                    try:
                        arduino_millis = int(data[0])
                        raw_voltage = float(data[1])
                        temp_c = float(data[2])
                        now_dt = datetime.now()
                        now = now_dt.strftime('%Y-%m-%d %H:%M:%S')
                        
                        # Fetch outdoor data (h0=mean, hx=max, hn=min)
                        outdoor_data = meteoswiss_fetch.get_outdoor_data(STATION_ID)
                        
                        if outdoor_data:
                            outdoor_temp = outdoor_data['h0']
                            outdoor_max = outdoor_data['hx']
                            outdoor_min = outdoor_data['hn']
                            # Shift outdoor timestamp by -2h to align with indoor readings
                            # (outdoor data lags ~2h behind real-time)
                            outdoor_ts = (now_dt - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
                            mts = outdoor_data['timestamp']  # raw UTC ref for logging
                            mts_zurich = outdoor_data['timestamp_zurich']
                            insert_reading(now, arduino_millis, raw_voltage, temp_c, outdoor_temp, outdoor_max, outdoor_min, outdoor_ts)
                            out_str = f"{outdoor_temp:.1f}°C" if outdoor_temp is not None else "N/A"
                            max_str = f"{outdoor_max:.1f}" if outdoor_max is not None else "N/A"
                            min_str = f"{outdoor_min:.1f}" if outdoor_min is not None else "N/A"
                            print(f"Logged: In={temp_c:.1f}°C (raw={raw_voltage:.0f}mV), Out={out_str} "
                                  f"(max={max_str}, min={min_str}) "
                                  f"[Outdoor ts: {outdoor_ts}, MeteoSwiss: {mts_zurich} ({mts} UTC)] at {now}")
                        else:
                            insert_reading(now, arduino_millis, raw_voltage, temp_c, None, None, None, None)
                            print(f"Logged: In={temp_c:.1f}°C (raw={raw_voltage:.0f}mV), Out=Failed at {now}")
                        
                    except ValueError as e:
                        print(f"Parse error: {e}")
                        
    except KeyboardInterrupt:
        print("Stopping logger...")
    finally:
        ser.close()

if __name__ == '__main__':
    main()
