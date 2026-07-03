from flask import Flask, jsonify, render_template_string, request
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
import os

app = Flask(__name__)
DB_PATH = os.path.expanduser('~/temp_logger/data/temperature.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def downsample_data(rows, max_points=500):
    """Downsample raw rows by averaging in chunks."""
    if len(rows) <= max_points:
        return rows

    step = len(rows) // max_points
    downsampled = []

    for i in range(0, len(rows), step):
        chunk = rows[i:i + step]
        if not chunk:
            continue

        def avg(col):
            vals = [r[col] for r in chunk if r[col] is not None]
            return sum(vals) / len(vals) if vals else None

        downsampled.append({
            'timestamp': chunk[0]['timestamp'],
            'temperature_c': avg('temperature_c'),
            'outdoor_temp_c': avg('outdoor_temp_c'),
            'outdoor_max_c': avg('outdoor_max_c'),
            'outdoor_min_c': avg('outdoor_min_c'),
            'outdoor_timestamp': chunk[0]['outdoor_timestamp'],
        })
    return downsampled


def aggregate_hourly(rows):
    """Group raw rows by outdoor_timestamp hour, returning one row per hour
    with indoor mean/min/max and outdoor mean/max/min."""
    groups = defaultdict(lambda: {
        'indoor': [],
        'outdoor_mean': [],
        'outdoor_max': [],
        'outdoor_min': [],
        'timestamp': None,
    })

    for r in rows:
        ots = r['outdoor_timestamp']
        if not ots:
            continue
        key = ots[:13]  # "YYYY-MM-DD HH"
        g = groups[key]
        g['timestamp'] = key + ":00:00"
        if r['temperature_c'] is not None:
            g['indoor'].append(r['temperature_c'])
        if r['outdoor_temp_c'] is not None:
            g['outdoor_mean'].append(r['outdoor_temp_c'])
        if r['outdoor_max_c'] is not None:
            g['outdoor_max'].append(r['outdoor_max_c'])
        if r['outdoor_min_c'] is not None:
            g['outdoor_min'].append(r['outdoor_min_c'])

    result = []
    for key in sorted(groups.keys()):
        g = groups[key]
        result.append({
            'timestamp': g['timestamp'],
            'temperature_c': sum(g['indoor']) / len(g['indoor']) if g['indoor'] else None,
            'indoor_min_c': min(g['indoor']) if g['indoor'] else None,
            'indoor_max_c': max(g['indoor']) if g['indoor'] else None,
            'outdoor_temp_c': sum(g['outdoor_mean']) / len(g['outdoor_mean']) if g['outdoor_mean'] else None,
            'outdoor_max_c': max(g['outdoor_max']) if g['outdoor_max'] else None,
            'outdoor_min_c': min(g['outdoor_min']) if g['outdoor_min'] else None,
            'outdoor_timestamp': g['timestamp'],
        })
    return result


def maybe_downsample(rows, aggregation, max_points=500):
    """Downsample raw rows, or trim hourly rows if too many."""
    if aggregation == 'hourly':
        if len(rows) > max_points:
            step = len(rows) // max_points
            return rows[::step] if step > 1 else rows
        return rows
    return downsample_data(rows, max_points)


HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pi Temp Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
    :root {
        --bg: #0f1117;
        --surface: #1a1d27;
        --surface2: #232738;
        --text: #d1d5db;
        --muted: #6b7280;
        --in: #34d399;
        --out: #38bdf8;
        --delta: #fbbf24;
        --out-max: #f87171;
        --out-min: #60a5fa;
        --border: #2d3148;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: var(--bg);
        color: var(--text);
        min-height: 100vh;
        padding: 24px 16px;
    }

    .wrap { max-width: 1300px; margin: 0 auto; }

    header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 12px;
        margin-bottom: 24px;
    }
    header h1 {
        font-size: 1.5rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: #f9fafb;
    }
    header h1 span { color: var(--in); }

    .controls {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
    }

    select {
        padding: 8px 14px;
        font-size: 0.875rem;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--surface2);
        color: var(--text);
        cursor: pointer;
        outline: none;
        transition: border-color 0.15s;
    }
    select:hover { border-color: #4b5563; }
    select:focus { border-color: var(--out); }

    .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 12px;
        margin-bottom: 24px;
    }

    .stat {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 16px;
        text-align: center;
    }
    .stat-label {
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--muted);
        margin-bottom: 6px;
    }
    .stat-value {
        font-size: 1.35rem;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
    }
    .stat-sub {
        font-size: 0.75rem;
        color: var(--muted);
        margin-top: 2px;
    }
    .c-in  { color: var(--in); }
    .c-out { color: var(--out); }
    .c-delta { color: var(--delta); }
    .c-max { color: var(--out-max); }
    .c-min { color: var(--out-min); }

    .card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 24px;
        margin-bottom: 20px;
    }
    .card h2 {
        font-size: 1rem;
        font-weight: 600;
        margin-bottom: 16px;
        color: #f3f4f6;
    }

    .chart-wrap {
        position: relative;
        width: 100%;
        aspect-ratio: 2.2 / 1;
    }
    .chart-wrap canvas { width: 100% !important; height: 100% !important; }

    .last-update {
        text-align: right;
        font-size: 0.7rem;
        color: var(--muted);
        margin-top: 8px;
    }

    @media (max-width: 640px) {
        header { flex-direction: column; align-items: flex-start; }
        .chart-wrap { aspect-ratio: 1.5 / 1; }
        .stat { padding: 12px 10px; }
        .stat-value { font-size: 1.15rem; }
    }
</style>
</head>
<body>
<div class="wrap">

<header>
    <h1>🌡️ <span>Indoor</span> vs <span style="color:var(--out)">Outdoor</span></h1>
    <div class="controls">
        <label for="aggSelect" style="display:none">View</label>
        <select id="aggSelect" onchange="fetchData()">
            <option value="raw">Raw (5 min)</option>
            <option value="hourly">Hourly Avg</option>
        </select>
        <label for="timeRange" style="display:none">Range</label>
        <select id="timeRange" onchange="fetchData()">
            <option value="1d">24 Hours</option>
            <option value="2d">48 Hours</option>
            <option value="1w">7 Days</option>
            <option value="1m">30 Days</option>
            <option value="all">All Time</option>
        </select>
    </div>
</header>

<div class="grid">
    <div class="stat"><div class="stat-label">Indoor</div><div class="stat-value c-in"  id="currentIn">--</div><div class="stat-sub" id="rangeIn"></div></div>
    <div class="stat"><div class="stat-label">Outdoor</div><div class="stat-value c-out" id="currentOut">--</div><div class="stat-sub" id="rangeOut"></div></div>
    <div class="stat"><div class="stat-label">Δ Indoor−Outdoor</div><div class="stat-value c-delta" id="deltaTemp">--</div></div>
    <div class="stat"><div class="stat-label">Indoor Hour Max</div><div class="stat-value c-in" id="hourMaxIn">--</div></div>
    <div class="stat"><div class="stat-label">Indoor Hour Min</div><div class="stat-value c-in" id="hourMinIn">--</div></div>
    <div class="stat"><div class="stat-label">Outdoor Hour Max</div><div class="stat-value c-max" id="currentOutMax">--</div></div>
    <div class="stat"><div class="stat-label">Outdoor Hour Min</div><div class="stat-value c-min" id="currentOutMin">--</div></div>
    <div class="stat"><div class="stat-label">Outdoor Ref</div><div class="stat-value c-out" id="outdoorRefTime">--</div></div>
</div>

<div class="card">
    <h2>Temperature</h2>
    <div class="chart-wrap"><canvas id="tempChart"></canvas></div>
</div>

<div class="card">
    <h2>Delta (Indoor − Outdoor)</h2>
    <div class="chart-wrap"><canvas id="deltaChart"></canvas></div>
</div>

<div class="last-update" id="lastUpdate">--</div>

</div>

<script>
    let tempChart = null;
    let deltaChart = null;

    const getRangeMinutes = r => ({'1d':1440,'2d':2880,'1w':10080,'1m':43200,'all':null})[r];

    const fmtTemp = v => v != null ? v.toFixed(1) + '\u00b0C' : '--';
    const fmtDelta = v => v != null ? ((v>=0?'+':'')+v.toFixed(1)+'\u00b0C') : '--';

    function buildFillDatasets(label, meanData, minData, maxData, color, orderBase) {
        const alpha = color.replace(')', ', 0.15)').replace('rgb', 'rgba');
        return [
            {
                label: label + ' Min',
                data: minData,
                borderColor: 'transparent',
                backgroundColor: 'transparent',
                pointRadius: 0,
                fill: false,
                tension: 0.3,
                order: orderBase + 2,
            },
            {
                label: label + ' Range',
                data: maxData,
                borderColor: 'transparent',
                backgroundColor: alpha,
                pointRadius: 0,
                fill: {target: '-1', above: alpha, below: alpha},
                tension: 0.3,
                order: orderBase + 1,
            },
            {
                label: label,
                data: meanData,
                borderColor: color,
                backgroundColor: 'transparent',
                pointRadius: 0,
                borderWidth: 2.5,
                fill: false,
                tension: 0.3,
                order: orderBase,
            },
        ];
    }

    async function fetchData() {
        const range = document.getElementById('timeRange').value;
        const agg = document.getElementById('aggSelect').value;
        const minutes = getRangeMinutes(range);
        const url = minutes
            ? '/data?minutes=' + minutes + '&aggregation=' + agg
            : '/data?all=1&aggregation=' + agg;
        const resp = await fetch(url);
        const data = await resp.json();
        if (data.error) return;

        document.getElementById('currentIn').textContent = fmtTemp(data.current_in);
        document.getElementById('currentOut').textContent = fmtTemp(data.current_out);
        document.getElementById('currentOutMax').textContent = fmtTemp(data.current_out_max);
        document.getElementById('currentOutMin').textContent = fmtTemp(data.current_out_min);
        document.getElementById('outdoorRefTime').textContent = data.outdoor_ref_time || '--';
        document.getElementById('deltaTemp').textContent = fmtDelta(data.current_delta);

        const iminA = data.indoor_min_temps;
        const imaxA = data.indoor_max_temps;
        const inMinNow = iminA && iminA.length ? iminA[iminA.length-1] : null;
        const inMaxNow = imaxA && imaxA.length ? imaxA[imaxA.length-1] : null;
        document.getElementById('hourMaxIn').textContent = fmtTemp(inMaxNow);
        document.getElementById('hourMinIn').textContent = fmtTemp(inMinNow);

        if (data.min_in != null && data.max_in != null)
            document.getElementById('rangeIn').textContent = data.min_in.toFixed(1) + ' \u2013 ' + data.max_in.toFixed(1) + '\u00b0C';
        else document.getElementById('rangeIn').textContent = '';
        if (data.min_out != null && data.max_out != null)
            document.getElementById('rangeOut').textContent = data.min_out.toFixed(1) + ' \u2013 ' + data.max_out.toFixed(1) + '\u00b0C';
        else document.getElementById('rangeOut').textContent = '';

        document.getElementById('lastUpdate').textContent = 'Updated ' + new Date().toLocaleTimeString();

        const labels = data.timestamps;
        const showDots = labels.length <= 120;

        if (tempChart) tempChart.destroy();
        const ctx1 = document.getElementById('tempChart').getContext('2d');
        const datasets = [];

        const hasIndoorBand = data.indoor_min_temps && data.indoor_max_temps && data.indoor_min_temps.length;
        if (hasIndoorBand) {
            datasets.push(...buildFillDatasets(
                'Indoor', data.indoor_temps, data.indoor_min_temps, data.indoor_max_temps,
                'rgb(52, 211, 153)', 1
            ));
            datasets[datasets.length-1].pointRadius = showDots ? 1.5 : 0;
        } else {
            datasets.push({
                label: 'Indoor',
                data: data.indoor_temps,
                borderColor: 'rgb(52, 211, 153)',
                backgroundColor: 'rgba(52, 211, 153, 0.08)',
                fill: { target: { value: -100 }, above: 'rgba(52, 211, 153, 0.08)' },
                tension: 0.3,
                pointRadius: showDots ? 1.5 : 0,
                borderWidth: 2.5,
                order: 1,
            });
        }

        const hasOutdoorBand = data.outdoor_min_temps && data.outdoor_max_temps && data.outdoor_min_temps.length;
        if (hasOutdoorBand) {
            datasets.push(...buildFillDatasets(
                'Outdoor', data.outdoor_temps, data.outdoor_min_temps, data.outdoor_max_temps,
                'rgb(56, 189, 248)', 4
            ));
            datasets[datasets.length-1].pointRadius = showDots ? 1.5 : 0;
        } else {
            datasets.push({
                label: 'Outdoor',
                data: data.outdoor_temps,
                borderColor: 'rgb(56, 189, 248)',
                tension: 0.3,
                pointRadius: showDots ? 1.5 : 0,
                borderWidth: 2.5,
                fill: false,
                order: 4,
            });
        }

        tempChart = new Chart(ctx1, {
            type: 'line',
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        labels: {
                            color: '#d1d5db',
                            usePointStyle: true,
                            pointStyleWidth: 8,
                            padding: 20,
                            filter: item => !item.text.endsWith(' Min') && !item.text.endsWith(' Range'),
                        },
                    },
                    tooltip: {
                        backgroundColor: '#1a1d27',
                        borderColor: '#2d3148',
                        borderWidth: 1,
                        titleColor: '#d1d5db',
                        bodyColor: '#d1d5db',
                        callbacks: {
                            label(ctx) {
                                const lbl = ctx.dataset.label || '';
                                if (lbl.endsWith(' Min') || lbl.endsWith(' Range')) return null;
                                return lbl + ': ' + (ctx.parsed.y != null ? ctx.parsed.y.toFixed(1) + '\u00b0C' : 'N/A');
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        ticks: { color: '#6b7280', maxTicksLimit: 10, maxRotation: 45, minRotation: 0 },
                        grid: { color: '#1f2937' },
                    },
                    y: {
                        ticks: { color: '#6b7280', callback: v => v + '\u00b0C' },
                        grid: { color: '#1f2937' },
                    },
                },
            },
        });

        if (deltaChart) deltaChart.destroy();
        const ctx2 = document.getElementById('deltaChart').getContext('2d');

        deltaChart = new Chart(ctx2, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: '\u0394 Indoor \u2212 Outdoor',
                    data: data.deltas,
                    borderColor: 'rgb(251, 191, 36)',
                    backgroundColor: 'rgba(251, 191, 36, 0.12)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: showDots ? 1.5 : 0,
                    borderWidth: 2,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { color: '#d1d5db', usePointStyle: true, padding: 20 } },
                    tooltip: {
                        backgroundColor: '#1a1d27',
                        borderColor: '#2d3148',
                        borderWidth: 1,
                        titleColor: '#d1d5db',
                        bodyColor: '#d1d5db',
                        callbacks: {
                            label(ctx) {
                                const v = ctx.parsed.y;
                                return v != null ? ('\u0394: ' + (v>=0?'+':'') + v.toFixed(1) + '\u00b0C') : '\u0394: N/A';
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        ticks: { color: '#6b7280', maxTicksLimit: 10, maxRotation: 45, minRotation: 0 },
                        grid: { color: '#1f2937' },
                    },
                    y: {
                        ticks: { color: '#6b7280', callback: v => (v>=0?'+':'') + v + '\u00b0C' },
                        grid: { color: '#1f2937' },
                    },
                },
            },
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
    aggregation = request.args.get('aggregation', 'raw')

    if all_time:
        cursor.execute(
            'SELECT timestamp, temperature_c, outdoor_temp_c, outdoor_max_c, outdoor_min_c, outdoor_timestamp '
            'FROM temperature_readings ORDER BY timestamp ASC'
        )
    else:
        mins = int(minutes) if minutes else 1440
        cutoff = (datetime.now() - timedelta(minutes=mins)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            'SELECT timestamp, temperature_c, outdoor_temp_c, outdoor_max_c, outdoor_min_c, outdoor_timestamp '
            'FROM temperature_readings WHERE timestamp >= ? ORDER BY timestamp ASC',
            (cutoff,),
        )

    rows = cursor.fetchall()
    conn.close()

    indoor_min_temps = None
    indoor_max_temps = None

    if aggregation == 'hourly' and rows:
        rows = aggregate_hourly(rows)
        indoor_min_temps = [r['indoor_min_c'] for r in rows]
        indoor_max_temps = [r['indoor_max_c'] for r in rows]

    if not rows:
        return jsonify({
            "timestamps": [], "indoor_temps": [], "outdoor_temps": [],
            "outdoor_max_temps": [], "outdoor_min_temps": [],
            "indoor_min_temps": [], "indoor_max_temps": [],
            "outdoor_timestamps": [], "deltas": [],
            "current_in": None, "current_out": None,
            "current_out_max": None, "current_out_min": None,
            "outdoor_ref_time": None, "current_delta": None,
            "min_in": None, "max_in": None,
            "min_out": None, "max_out": None,
            "min_out_range": None, "max_out_range": None,
        })

    downsampled = maybe_downsample(rows, aggregation, max_points=500)

    timestamps = [r['timestamp'].split(' ')[1][:5] for r in downsampled]
    indoor_temps = [r['temperature_c'] for r in downsampled]
    outdoor_temps = [r['outdoor_temp_c'] for r in downsampled]
    outdoor_max_temps = [r['outdoor_max_c'] for r in downsampled]
    outdoor_min_temps = [r['outdoor_min_c'] for r in downsampled]
    outdoor_timestamps = [
        r['outdoor_timestamp'].split(' ')[1][:5] if r['outdoor_timestamp'] else None
        for r in downsampled
    ]

    if aggregation == 'hourly' and indoor_min_temps and indoor_max_temps:
        max_pts = 500
        if len(indoor_min_temps) > max_pts:
            step = len(indoor_min_temps) // max_pts
            indoor_min_temps = indoor_min_temps[::step]
            indoor_max_temps = indoor_max_temps[::step]
    elif aggregation != 'hourly':
        indoor_min_temps = []
        indoor_max_temps = []

    deltas = []
    for r in downsampled:
        if r['temperature_c'] is not None and r['outdoor_temp_c'] is not None:
            deltas.append(r['temperature_c'] - r['outdoor_temp_c'])
        else:
            deltas.append(None)

    all_in = [r['temperature_c'] for r in rows if r['temperature_c'] is not None]
    all_out = [r['outdoor_temp_c'] for r in rows if r['outdoor_temp_c'] is not None]
    all_out_max = [r['outdoor_max_c'] for r in rows if r['outdoor_max_c'] is not None]
    all_out_min = [r['outdoor_min_c'] for r in rows if r['outdoor_min_c'] is not None]

    current_delta = None
    if all_in and all_out:
        current_delta = all_in[-1] - all_out[-1]

    outdoor_ref_time = None
    for r in reversed(rows):
        if r['outdoor_timestamp']:
            outdoor_ref_time = r['outdoor_timestamp'].split(' ')[1][:5]
            break

    return jsonify({
        "timestamps": timestamps,
        "indoor_temps": indoor_temps,
        "outdoor_temps": outdoor_temps,
        "outdoor_max_temps": outdoor_max_temps,
        "outdoor_min_temps": outdoor_min_temps,
        "indoor_min_temps": indoor_min_temps,
        "indoor_max_temps": indoor_max_temps,
        "outdoor_timestamps": outdoor_timestamps,
        "deltas": deltas,
        "current_in": all_in[-1] if all_in else None,
        "current_out": all_out[-1] if all_out else None,
        "current_out_max": all_out_max[-1] if all_out_max else None,
        "current_out_min": all_out_min[-1] if all_out_min else None,
        "outdoor_ref_time": outdoor_ref_time,
        "current_delta": current_delta,
        "min_in": min(all_in) if all_in else None,
        "max_in": max(all_in) if all_in else None,
        "min_out": min(all_out) if all_out else None,
        "max_out": max(all_out) if all_out else None,
        "min_out_range": min(all_out_min) if all_out_min else None,
        "max_out_range": max(all_out_max) if all_out_max else None,
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
