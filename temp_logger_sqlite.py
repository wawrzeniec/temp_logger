import serial
import sqlite3
import time
from datetime import datetime
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
            temperature_c REAL NOT NULL,
            outdoor_temp_c REAL
        )
    ''')
    
    # Migration: Add outdoor column if upgrading from an older DB version
    cursor.execute("PRAGMA table_info(temperature_readings)")
    columns = [info[1] for info in cursor.fetchall()]
    if 'outdoor_temp_c' not in columns:
        cursor.execute("ALTER TABLE temperature_readings ADD COLUMN outdoor_temp_c REAL")
        print("Added outdoor_temp_c column to database")
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON temperature_readings(timestamp)')
    conn.commit()
    conn.close()

def insert_reading(timestamp, arduino_millis, temp_c, outdoor_temp_c):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO temperature_readings (timestamp, arduino_millis, temperature_c, outdoor_temp_c)
        VALUES (?, ?, ?, ?)
    ''', (timestamp, arduino_millis, temp_c, outdoor_temp_c))
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
                if len(data) == 2:
                    try:
                        arduino_millis = int(data[0])
                        temp_c = float(data[1])
                        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        
                        # Fetch outdoor temp
                        outdoor_temp = meteoswiss_fetch.get_outdoor_temp(STATION_ID)
                        
                        insert_reading(now, arduino_millis, temp_c, outdoor_temp)
                        
                        out_str = f"{outdoor_temp:.1f}°C" if outdoor_temp is not None else "Failed"
                        print(f"Logged: In={temp_c:.1f}°C, Out={out_str} at {now}")
                        
                    except ValueError as e:
                        print(f"Parse error: {e}")
                        
    except KeyboardInterrupt:
        print("Stopping logger...")
    finally:
        ser.close()

if __name__ == '__main__':
    main()
