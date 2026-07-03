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
        chunk = rows[i : i + step]
        if not chunk:
            continue

        def avg(col):
            vals = [r[col] for r in chunk if r[col] is not None]
            return sum(vals) / len(vals) if vals else None

        downsampled.append({
            'timestamp': chunk[0]['timestamp'],
            'outdoor_timestamp': chunk[0]['outdoor_timestamp'],
            'temperature_c': avg('temperature_c'),
            'outdoor_temp_c': avg('outdoor_temp_c'),
            'outdoor_max_c': avg('outdoor_max_c'),
            'outdoor_min_c': avg('outdoor_min_c'),
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
            'outdoor_timestamp': g['timestamp'],
            'temperature_c': sum(g['indoor']) / len(g['indoor']) if g['indoor'] else None,
            'indoor_min_c': min(g['indoor']) if g['indoor'] else None,
            'indoor_max_c': max(g['indoor']) if g['indoor'] else None,
            'outdoor_temp_c': sum(g['outdoor_mean']) / len(g['outdoor_mean']) if g['outdoor_mean'] else None,
            'outdoor_max_c': max(g['outdoor_max']) if g['outdoor_max'] else None,
            'outdoor_min_c': min(g['outdoor_min']) if g['outdoor_min'] else None,
        })
    return result


def maybe_downsample(rows, aggregation, max_points=500):
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
    header h1 span.in { color: var(--in); }
    header h1 span.out { color: var(--out); }

    .controls { display: flex; gap: 10px; flex-wrap: wrap; }

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

    .stat-row {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 24px;
        margin-bottom: 20px;
        flex-wrap: wrap;
    }
    .stat {
        background: var(--surface2);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 12px 20px;
        text-align: center;
    }
    .stat-label {
        font-size: 0.7rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--muted);
        margin-bottom: 4px;
    }
    .stat-value {
        font-size: 1.4rem;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
    }
    .stat-sub {
        font-size: 0.7rem;
        color: var(--muted);
        margin-top: 2px;
    }
    .c-in { color: var(--in); }
    .c-out { color: var(--out); }

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
    }
</style>
</head>
<body>
<div class="wrap">

<header>
    <h1>🌡️ <span class="in">Indoor</span> vs <span class="out">Outdoor</span></h1>
    <div class="controls">
        <select id="aggSelect" onchange="fetchData()">
            <option value="raw">Raw (5 min)</option>
            <option value="hourly">Hourly Avg</option>
        </select>
        <select id="timeRange" onchange="fetchData()">
            <option value="1d">24 Hours</option>
            <option value="2d">48 Hours</option>
            <option value="1w">7 Days</option>
            <option value="1m">30 Days</option>
            <option value="all">All Time</option>
        </select>
    </div>
</header>

<div class="stat-row">
    <div class="stat">
        <div class="stat-label">Current Indoor</div>
        <div class="stat-value c-in" id="currentIn">--</div>
    </div>
    <div class="stat">
        <div class="stat-label">Avg Indoor</div>
        <div class="stat-value c-in" id="avgIn">--</div>
    </div>
    <div class="stat">
        <div class="stat-label">Avg Outdoor</div>
        <div class="stat-value c-out" id="avgOut">--</div>
    </div>
</div>

<div class="card">
    <h2>Temperature</h2>
    <div class="chart-wrap"><canvas id="tempChart"></canvas></div>
</div>

<div class="card" id="deltaCard">
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

    /** "HH:MM" → minutes since midnight */
    function hmToMins(hm) {
        const [h, m] = hm.split(':').map(Number);
        return h * 60 + m;
    }

    /** minutes since midnight → "HH:MM" */
    function minsToHm(m) {
        const h = Math.floor(m / 60) % 24;
        const mi = Math.floor(m % 60);
        return String(h).padStart(2, '0') + ':' + String(mi).padStart(2, '0');
    }

    function buildFillDatasets(label, meanXY, minXY, maxXY, color, orderBase) {
        const alpha = color.replace(')', ', 0.15)').replace('rgb', 'rgba');
        return [
            {
                label: label + ' Min', data: minXY,
                borderColor: 'transparent', backgroundColor: 'transparent',
                pointRadius: 0, fill: false, tension: 0.3, order: orderBase + 2,
            },
            {
                label: label + ' Range', data: maxXY,
                borderColor: 'transparent', backgroundColor: alpha,
                pointRadius: 0, fill: {target: '-1', above: alpha, below: alpha},
                tension: 0.3, order: orderBase + 1,
            },
            {
                label: label, data: meanXY,
                borderColor: color, backgroundColor: 'transparent',
                pointRadius: 0, borderWidth: 2.5, fill: false, tension: 0.3, order: orderBase,
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
        document.getElementById('avgIn').textContent = fmtTemp(data.avg_in);
        document.getElementById('avgOut').textContent = fmtTemp(data.avg_out);
        document.getElementById('lastUpdate').textContent = 'Updated ' + new Date().toLocaleTimeString();

        const n = data.timestamps.length;
        const showDots = n <= 120;
        const isHourly = agg === 'hourly';

        // Convert HH:MM strings to numeric minutes
        const indoorX = data.timestamps.map(hmToMins);
        const outdoorX = (isHourly ? data.timestamps : data.outdoor_timestamps).map(hmToMins);

        // --- Temp Chart ---
        if (tempChart) tempChart.destroy();
        const ctx1 = document.getElementById('tempChart').getContext('2d');
        const datasets = [];

        const indoorXY = indoorX.map((x, i) => ({x, y: data.indoor_temps[i]}));

        if (isHourly && data.indoor_min_temps && data.indoor_min_temps.length) {
            const inMinXY = indoorX.map((x, i) => ({x, y: data.indoor_min_temps[i]}));
            const inMaxXY = indoorX.map((x, i) => ({x, y: data.indoor_max_temps[i]}));
            datasets.push(...buildFillDatasets('Indoor', indoorXY, inMinXY, inMaxXY, 'rgb(52, 211, 153)', 1));
            datasets[datasets.length - 1].pointRadius = showDots ? 1.5 : 0;
        } else {
            datasets.push({
                label: 'Indoor', data: indoorXY,
                borderColor: 'rgb(52, 211, 153)',
                backgroundColor: 'rgba(52, 211, 153, 0.08)',
                fill: { target: { value: -100 }, above: 'rgba(52, 211, 153, 0.08)' },
                tension: 0.3, pointRadius: showDots ? 1.5 : 0, borderWidth: 2.5, order: 1,
            });
        }

        const outXY = outdoorX.map((x, i) => ({x, y: data.outdoor_temps[i]}));
        const outMinXY = outdoorX.map((x, i) => ({x, y: data.outdoor_min_temps[i]}));
        const outMaxXY = outdoorX.map((x, i) => ({x, y: data.outdoor_max_temps[i]}));

        if (data.outdoor_temps.some(v => v != null)) {
            datasets.push(...buildFillDatasets('Outdoor', outXY, outMinXY, outMaxXY, 'rgb(56, 189, 248)', 4));
            datasets[datasets.length - 1].pointRadius = showDots ? 1.5 : 0;
        }

        tempChart = new Chart(ctx1, {
            type: 'line',
            data: { datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'x', intersect: false },
                plugins: {
                    legend: {
                        labels: {
                            color: '#d1d5db', usePointStyle: true,
                            pointStyleWidth: 8, padding: 20,
                            filter: item => !item.text.endsWith(' Min') && !item.text.endsWith(' Range'),
                        },
                    },
                    tooltip: {
                        backgroundColor: '#1a1d27', borderColor: '#2d3148', borderWidth: 1,
                        titleColor: '#d1d5db', bodyColor: '#d1d5db',
                        callbacks: {
                            title(items) { return items.length ? minsToHm(items[0].parsed.x) : ''; },
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
                        type: 'linear',
                        ticks: { color: '#6b7280', maxTicksLimit: 10, callback: v => minsToHm(v) },
                        grid: { color: '#1f2937' },
                    },
                    y: {
                        ticks: { color: '#6b7280', callback: v => v + '\u00b0C' },
                        grid: { color: '#1f2937' },
                    },
                },
            },
        });

        // --- Delta Chart ---
        if (deltaChart) deltaChart.destroy();
        const ctx2 = document.getElementById('deltaChart').getContext('2d');

        // In raw mode deltas are grouped by outdoor_timestamp hour;
        // in hourly mode they're per bucket. Use dedicated delta_timestamps.
        const deltaX = (data.delta_timestamps || data.timestamps).map(hmToMins);
        const deltaXY = deltaX.map((x, i) => ({x, y: data.deltas[i]}));

        deltaChart = new Chart(ctx2, {
                type: 'line',
                data: {
                    datasets: [{
                        label: '\u0394 Indoor \u2212 Outdoor',
                        data: deltaXY,
                        borderColor: 'rgb(251, 191, 36)',
                        backgroundColor: 'rgba(251, 191, 36, 0.12)',
                        fill: true, tension: 0.3,
                        pointRadius: showDots ? 1.5 : 0, borderWidth: 2,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'x', intersect: false },
                    plugins: {
                        legend: { labels: { color: '#d1d5db', usePointStyle: true, padding: 20 } },
                        tooltip: {
                            backgroundColor: '#1a1d27', borderColor: '#2d3148', borderWidth: 1,
                            titleColor: '#d1d5db', bodyColor: '#d1d5db',
                            callbacks: {
                                title(items) { return items.length ? minsToHm(items[0].parsed.x) : ''; },
                                label(ctx) {
                                    const v = ctx.parsed.y;
                                    return v != null ? ('\u0394: ' + (v>=0?'+':'') + v.toFixed(1) + '\u00b0C') : '\u0394: N/A';
                                },
                            },
                        },
                    },
                    scales: {
                        x: {
                            type: 'linear',
                            ticks: { color: '#6b7280', maxTicksLimit: 10, callback: v => minsToHm(v) },
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
            "outdoor_timestamps": [], "deltas": [], "delta_timestamps": [],
            "current_in": None, "avg_in": None, "avg_out": None,
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

    # --- Deltas ---
    # Raw mode: group by outdoor_timestamp hour — only compare indoor and
    #           outdoor that share the same outdoor_timestamp (i.e. the same
    #           MeteoSwiss hourly reading). One delta per outdoor hour.
    # Hourly mode: row-by-row — both values are already in the same bucket.
    if aggregation == 'hourly':
        deltas = []
        for r in downsampled:
            if r['temperature_c'] is not None and r['outdoor_temp_c'] is not None:
                deltas.append(r['temperature_c'] - r['outdoor_temp_c'])
            else:
                deltas.append(None)
        delta_timestamps = timestamps
    else:
        delta_groups = defaultdict(lambda: {'indoor': [], 'outdoor': None, 'ts': None})
        for r in rows:
            ots = r['outdoor_timestamp']
            if not ots or r['outdoor_temp_c'] is None or r['temperature_c'] is None:
                continue
            key = ots[:13]  # "YYYY-MM-DD HH"
            g = delta_groups[key]
            g['indoor'].append(r['temperature_c'])
            g['outdoor'] = r['outdoor_temp_c']
            g['ts'] = ots.split(' ')[1][:5] if ' ' in ots else ots

        deltas = []
        delta_timestamps = []
        for key in sorted(delta_groups.keys()):
            g = delta_groups[key]
            if g['indoor'] and g['outdoor'] is not None:
                delta_timestamps.append(g['ts'])
                deltas.append(sum(g['indoor']) / len(g['indoor']) - g['outdoor'])

    all_in = [r['temperature_c'] for r in rows if r['temperature_c'] is not None]
    all_out = [r['outdoor_temp_c'] for r in rows if r['outdoor_temp_c'] is not None]

    avg_in = sum(all_in) / len(all_in) if all_in else None
    avg_out = sum(all_out) / len(all_out) if all_out else None

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
        "delta_timestamps": delta_timestamps,
        "current_in": all_in[-1] if all_in else None,
        "avg_in": avg_in,
        "avg_out": avg_out,
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
