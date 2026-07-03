import sqlite3
import random
from datetime import datetime, timedelta
import os

DB_PATH = os.path.expanduser('~/temp_logger/data/temperature.db')

def create_fake_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create table with both indoor and outdoor columns
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS temperature_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            arduino_millis INTEGER,
            temperature_c REAL NOT NULL,
            outdoor_temp_c REAL,
            outdoor_max_c REAL,
            outdoor_min_c REAL,
            outdoor_timestamp TEXT
        )
    ''')
    
    # Clear existing data
    cursor.execute('DELETE FROM temperature_readings')
    
    # Generate data for the last 35 days
    now = datetime.now()
    start_time = now - timedelta(days=35)
    
    current_time = start_time
    millis = 0
    
    base_indoor_temp = 22.0
    base_outdoor_temp = 18.0
    
    while current_time <= now:
        hour = current_time.hour
        
        # Indoor: stable, peaks at 2pm, range ~20-24°C
        indoor_variation = 2.0 * (0.5 - abs(hour - 14) / 14.0)
        indoor_noise = random.uniform(-0.3, 0.3)
        indoor_temp = base_indoor_temp + indoor_variation + indoor_noise
        
        # Outdoor: more volatile, peaks at 3pm, range ~10-28°C
        # Outdoor has wider swings and peaks slightly later
        outdoor_variation = 8.0 * (0.5 - abs(hour - 15) / 15.0)
        outdoor_noise = random.uniform(-1.0, 1.0)
        outdoor_temp = base_outdoor_temp + outdoor_variation + outdoor_noise
        
        # Max/min for the hour: max is temp + small offset, min is temp - small offset
        outdoor_max = outdoor_temp + random.uniform(0.5, 1.5)
        outdoor_min = outdoor_temp - random.uniform(0.5, 1.5)
        
        # outdoor_timestamp: shifted -2h to match the real data lag pattern
        outdoor_ts = current_time - timedelta(hours=2)
        
        cursor.execute('''
            INSERT INTO temperature_readings (timestamp, arduino_millis, temperature_c, outdoor_temp_c, outdoor_max_c, outdoor_min_c, outdoor_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (current_time.strftime('%Y-%m-%d %H:%M:%S'), millis, indoor_temp, outdoor_temp, outdoor_max, outdoor_min,
              outdoor_ts.strftime('%Y-%m-%d %H:%M:%S')))
        
        current_time += timedelta(minutes=5)
        millis += 300000
    
    conn.commit()
    conn.close()
    print(f"✅ Generated fake data in {DB_PATH}")
    
    # Print summary
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM temperature_readings')
    count = cursor.fetchone()[0]
    cursor.execute('SELECT MIN(timestamp), MAX(timestamp) FROM temperature_readings')
    min_time, max_time = cursor.fetchone()
    cursor.execute('SELECT MIN(temperature_c), MAX(temperature_c), AVG(temperature_c) FROM temperature_readings')
    min_in, max_in, avg_in = cursor.fetchone()
    cursor.execute('SELECT MIN(outdoor_temp_c), MAX(outdoor_temp_c), AVG(outdoor_temp_c) FROM temperature_readings')
    min_out, max_out, avg_out = cursor.fetchone()
    cursor.execute('SELECT MIN(outdoor_min_c), MAX(outdoor_max_c) FROM temperature_readings')
    min_out_range, max_out_range = cursor.fetchone()
    cursor.execute('SELECT MIN(outdoor_timestamp), MAX(outdoor_timestamp) FROM temperature_readings WHERE outdoor_timestamp IS NOT NULL')
    min_ots, max_ots = cursor.fetchone()
    conn.close()
    
    print(f"Total readings: {count}")
    print(f"Time range: {min_time} to {max_time}")
    print(f"\nIndoor temps: {min_in:.1f}°C to {max_in:.1f}°C (avg: {avg_in:.1f}°C)")
    print(f"Outdoor temps: {min_out:.1f}°C to {max_out:.1f}°C (avg: {avg_out:.1f}°C)")
    print(f"Outdoor hourly max range: {min_out_range:.1f}°C to {max_out_range:.1f}°C")
    print(f"Outdoor timestamps: {min_ots} to {max_ots}")

if __name__ == '__main__':
    create_fake_data()
