from flask import Flask, jsonify, render_template_string, request
import sqlite3
from datetime import datetime, timedelta
import os

app = Flask(__name__)
DB_PATH = os.path.expanduser('~/temp_logger/data/temperature.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def downsample_data(rows, max_points=500):
    if len(rows) <= max_points:
        return rows
    
    step = len(rows) // max_points
    downsampled = []
    
    for i in range(0, len(rows), step):
        chunk = rows[i:i+step]
        if chunk:
            valid_in = [r['temperature_c'] for r in chunk if r['temperature_c'] is not None]
            avg_in = sum(valid_in) / len(valid_in) if valid_in else None
            
            valid_out = [r['outdoor_temp_c'] for r in chunk if r['outdoor_temp_c'] is not None]
            avg_out = sum(valid_out) / len(valid_out) if valid_out else None
            
            downsampled.append({
                'timestamp': chunk[0]['timestamp'],
                'temperature_c': avg_in,
                'outdoor_temp_c': avg_out
            })
    return downsampled

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Pi Temp Monitor</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
            background: #1e1e1e; 
            color: #eee; 
            text-align: center; 
            margin: 0; 
            padding: 20px; 
        }
        h1 { color: #00ff00; margin-bottom: 10px; }
        h2 { color: #ffaa00; margin-top: 40px; margin-bottom: 10px; font-size: 20px; }
        .controls { 
            margin: 20px 0; 
            padding: 15px; 
            background: #2d2d2d; 
            border-radius: 8px;
            display: inline-block;
        }
        select {
            padding: 8px 15px;
            font-size: 16px;
            border-radius: 5px;
            border: 1px solid #555;
            background: #3d3d3d;
            color: #eee;
            cursor: pointer;
        }
        .stats {
            display: flex;
            justify-content: center;
            gap: 15px;
            margin: 20px 0;
            flex-wrap: wrap;
        }
        .stat-box {
            background: #2d2d2d;
            padding: 15px 20px;
            border-radius: 8px;
            min-width: 120px;
        }
        .stat-label { color: #888; font-size: 14px; margin-bottom: 5px; }
        .stat-value { font-size: 22px; font-weight: bold; }
        .in-color { color: #00ff00; }
        .out-color { color: #00bfff; }
        .delta-color { color: #ffaa00; }
        .chart-container { 
            width: 95%; 
            max-width: 1200px; 
            margin: auto; 
            background: #2d2d2d; 
            padding: 20px; 
            border-radius: 8px; 
            margin-bottom: 30px;
        }
        .last-update { color: #666; font-size: 12px; margin-top: 10px; }
    </style>
</head>
<body>
    <h1>🌡️ Indoor vs Outdoor Temperature</h1>
    
    <div class="controls">
        <label for="timeRange">Time Range: </label>
        <select id="timeRange" onchange="fetchData()">
            <option value="1d">Last 24 Hours</option>
            <option value="1w">Last 7 Days</option>
            <option value="1m">Last 30 Days</option>
            <option value="all">All Time</option>
        </select>
    </div>
    
    <div class="stats">
        <div class="stat-box">
            <div class="stat-label">Current Indoor</div>
            <div class="stat-value in-color" id="currentIn">--</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Current Outdoor</div>
            <div class="stat-value out-color" id="currentOut">--</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Δ Indoor - Outdoor</div>
            <div class="stat-value delta-color" id="deltaTemp">--</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Indoor Range</div>
            <div class="stat-value in-color" id="rangeIn">--</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Outdoor Range</div>
            <div class="stat-value out-color" id="rangeOut">--</div>
        </div>
    </div>
    
    <div class="chart-container">
        <canvas id="tempChart"></canvas>
    </div>
    
    <h2>Temperature Delta (Indoor - Outdoor)</h2>
    <div class="chart-container">
        <canvas id="deltaChart"></canvas>
    </div>
    
    <div class="last-update" id="lastUpdate">Last update: --</div>
    
    <script>
        let tempChart = null;
        let deltaChart = null;
        
        function getTimeRangeMinutes(range) {
            return {'1d': 1440, '1w': 10080, '1m': 43200, 'all': null}[range];
        }
        
        async function fetchData() {
            const range = document.getElementById('timeRange').value;
            const minutes = getTimeRangeMinutes(range);
            const url = minutes ? `/data?minutes=${minutes}` : '/data?all=1';
            const response = await fetch(url);
            const data = await response.json();
            
            if (data.error) return;
            
            // Update Stats
            document.getElementById('currentIn').textContent = data.current_in ? data.current_in.toFixed(1) + '°C' : '--';
            document.getElementById('currentOut').textContent = data.current_out ? data.current_out.toFixed(1) + '°C' : '--';
            
            if (data.current_delta !== null) {
                const deltaStr = (data.current_delta >= 0 ? '+' : '') + data.current_delta.toFixed(1) + '°C';
                document.getElementById('deltaTemp').textContent = deltaStr;
            } else {
                document.getElementById('deltaTemp').textContent = '--';
            }
            
            const inRange = data.min_in !== null ? `${data.min_in.toFixed(1)} - ${data.max_in.toFixed(1)}°C` : '--';
            const outRange = data.min_out !== null ? `${data.min_out.toFixed(1)} - ${data.max_out.toFixed(1)}°C` : '--';
            document.getElementById('rangeIn').textContent = inRange;
            document.getElementById('rangeOut').textContent = outRange;
            document.getElementById('lastUpdate').textContent = 'Last update: ' + new Date().toLocaleString();
            
            // Update Main Chart
            if (tempChart) tempChart.destroy();
            const ctx1 = document.getElementById('tempChart').getContext('2d');
            
            tempChart = new Chart(ctx1, {
                type: 'line',
                data: {
                    labels: data.timestamps,
                    datasets: [
                        {
                            label: 'Indoor (Arduino)',
                            data: data.indoor_temps,
                            borderColor: '#00ff00',
                            backgroundColor: 'rgba(0, 255, 0, 0.05)',
                            fill: {
                                target: { value: -1 }
                            },
                            tension: 0.3,
                            pointRadius: data.timestamps.length > 100 ? 0 : 2,
                            borderWidth: 3,
                            order: 1
                        },
                        {
                            label: 'Outdoor (MeteoSwiss)',
                            data: data.outdoor_temps,
                            borderColor: '#00bfff',
                            backgroundColor: 'rgba(0, 191, 255, 0.05)',
                            fill: false,
                            tension: 0.3,
                            pointRadius: data.timestamps.length > 100 ? 0 : 2,
                            borderWidth: 3,
                            order: 2
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    aspectRatio: 2,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { labels: { color: '#eee' } },
                        tooltip: {
                            backgroundColor: 'rgba(0, 0, 0, 0.8)',
                            callbacks: {
                                label: function(context) {
                                    return context.dataset.label + ': ' + (context.parsed.y !== null ? context.parsed.y.toFixed(1) + '°C' : 'N/A');
                                }
                            }
                        }
                    },
                    scales: {
                        x: { 
                            ticks: { color: '#aaa', maxTicksLimit: 8, maxRotation: 45, minRotation: 45 }, 
                            grid: { color: '#444' } 
                        },
                        y: { 
                            ticks: { color: '#aaa', callback: v => v + '°C' }, 
                            grid: { color: '#444' } 
                        }
                    }
                }
            });
            
            // Update Delta Chart
            if (deltaChart) deltaChart.destroy();
            const ctx2 = document.getElementById('deltaChart').getContext('2d');
            
            deltaChart = new Chart(ctx2, {
                type: 'line',
                data: {
                    labels: data.timestamps,
                    datasets: [
                        {
                            label: 'Delta (°C)',
                            data: data.deltas,
                            borderColor: '#ffaa00',
                            backgroundColor: 'rgba(255, 170, 0, 0.1)',
                            fill: false,
                            tension: 0.3,
                            pointRadius: data.timestamps.length > 100 ? 0 : 2,
                            borderWidth: 2
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    aspectRatio: 2,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { labels: { color: '#eee' } },
                        tooltip: {
                            backgroundColor: 'rgba(0, 0, 0, 0.8)',
                            callbacks: {
                                label: function(context) {
                                    const val = context.parsed.y;
                                    if (val !== null) {
                                        const sign = val >= 0 ? '+' : '';
                                        return 'Delta: ' + sign + val.toFixed(1) + '°C';
                                    }
                                    return 'Delta: N/A';
                                }
                            }
                        }
                    },
                    scales: {
                        x: { 
                            ticks: { color: '#aaa', maxTicksLimit: 8, maxRotation: 45, minRotation: 45 }, 
                            grid: { color: '#444' } 
                        },
                        y: { 
                            ticks: { 
                                color: '#aaa', 
                                callback: function(value) {
                                    const sign = value >= 0 ? '+' : '';
                                    return sign + value + '°C';
                                }
                            }, 
                            grid: { color: '#444' } 
                        }
                    }
                }
            });
        }
        
        fetchData();
        setInterval(fetchData, 60000);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/data')
def get_data():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    minutes = request.args.get('minutes')
    all_time = request.args.get('all')
    
    if all_time:
        cursor.execute('SELECT timestamp, temperature_c, outdoor_temp_c FROM temperature_readings ORDER BY timestamp ASC')
    else:
        mins = int(minutes) if minutes else 1440
        cutoff = (datetime.now() - timedelta(minutes=mins)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            SELECT timestamp, temperature_c, outdoor_temp_c 
            FROM temperature_readings WHERE timestamp >= ? ORDER BY timestamp ASC
        ''', (cutoff,))
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return jsonify({
            "timestamps": [], 
            "indoor_temps": [], 
            "outdoor_temps": [],
            "deltas": [],
            "current_in": None, 
            "current_out": None,
            "current_delta": None,
            "min_in": None, 
            "max_in": None, 
            "min_out": None, 
            "max_out": None
        })
    
    downsampled = downsample_data(rows, max_points=500)
    
    timestamps = [r['timestamp'].split(' ')[1][:5] for r in downsampled]
    indoor_temps = [r['temperature_c'] for r in downsampled]
    outdoor_temps = [r['outdoor_temp_c'] for r in downsampled]
    
    # Calculate deltas
    deltas = []
    for i, r in enumerate(downsampled):
        if r['temperature_c'] is not None and r['outdoor_temp_c'] is not None:
            deltas.append(r['temperature_c'] - r['outdoor_temp_c'])
        else:
            deltas.append(None)
    
    # Stats from full dataset
    all_in = [r['temperature_c'] for r in rows if r['temperature_c'] is not None]
    all_out = [r['outdoor_temp_c'] for r in rows if r['outdoor_temp_c'] is not None]
    
    current_delta = None
    if all_in and all_out:
        current_delta = all_in[-1] - all_out[-1]
    
    return jsonify({
        "timestamps": timestamps,
        "indoor_temps": indoor_temps,
        "outdoor_temps": outdoor_temps,
        "deltas": deltas,
        "current_in": all_in[-1] if all_in else None,
        "current_out": all_out[-1] if all_out else None,
        "current_delta": current_delta,
        "min_in": min(all_in) if all_in else None,
        "max_in": max(all_in) if all_in else None,
        "min_out": min(all_out) if all_out else None,
        "max_out": max(all_out) if all_out else None
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
