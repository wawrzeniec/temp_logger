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
    """Return (indoor_hourly, outdoor_hourly) — two lists of dicts with
    their own timestamp axes, same shape as raw rows but averaged per hour."""
    # Indoor: group by timestamp hour
    in_groups = defaultdict(lambda: {'temps': [], 'ts': None})
    for r in rows:
        ts = r['timestamp']
        key = ts[:13]  # "YYYY-MM-DD HH"
        g = in_groups[key]
        g['ts'] = key + ":00:00"
        if r['temperature_c'] is not None:
            g['temps'].append(r['temperature_c'])

    indoor = []
    for key in sorted(in_groups.keys()):
        g = in_groups[key]
        if g['temps']:
            indoor.append({
                'timestamp': g['ts'],
                'outdoor_timestamp': None,
                'temperature_c': sum(g['temps']) / len(g['temps']),
                'indoor_min_c': min(g['temps']),
                'indoor_max_c': max(g['temps']),
                'outdoor_temp_c': None,
                'outdoor_max_c': None,
                'outdoor_min_c': None,
            })

    # Outdoor: group by outdoor_timestamp hour
    out_groups = defaultdict(lambda: {'temps': [], 'maxs': [], 'mins': [], 'ts': None})
    for r in rows:
        ots = r['outdoor_timestamp']
        if not ots:
            continue
        key = ots[:13]
        g = out_groups[key]
        g['ts'] = key + ":00:00"
        if r['outdoor_temp_c'] is not None:
            g['temps'].append(r['outdoor_temp_c'])
        if r['outdoor_max_c'] is not None:
            g['maxs'].append(r['outdoor_max_c'])
        if r['outdoor_min_c'] is not None:
            g['mins'].append(r['outdoor_min_c'])

    outdoor = []
    for key in sorted(out_groups.keys()):
        g = out_groups[key]
        if g['temps']:
            outdoor.append({
                'timestamp': g['ts'],
                'outdoor_timestamp': g['ts'],
                'temperature_c': None,
                'indoor_min_c': None,
                'indoor_max_c': None,
                'outdoor_temp_c': sum(g['temps']) / len(g['temps']),
                'outdoor_max_c': max(g['maxs']) if g['maxs'] else None,
                'outdoor_min_c': min(g['mins']) if g['mins'] else None,
            })

    return indoor, outdoor


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
<script src="https://cdn.jsdelivr.net/npm/date-fns@3.6.0/cdn.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
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

    #resetZoom {
        padding: 8px 14px;
        font-size: 1rem;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--surface2);
        color: var(--text);
        cursor: pointer;
        transition: border-color 0.15s, color 0.15s;
        line-height: 1;
    }
    #resetZoom:hover { border-color: #4b5563; color: #f9fafb; }

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

    #zoomBox {
        position: fixed;
        border: 1px dashed rgba(56, 189, 248, 0.7);
        background: rgba(56, 189, 248, 0.08);
        pointer-events: none;
        display: none;
        z-index: 1000;
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
        <button id="resetZoom" onclick="resetZoom()" title="Reset zoom">↺</button>
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

<div id="zoomBox"></div>

</div>

<script>
    let tempChart = null;
    let deltaChart = null;

    const getRangeMinutes = r => ({'1d':1440,'2d':2880,'1w':10080,'1m':43200,'all':null})[r];
    const fmtTemp = v => v != null ? v.toFixed(1) + '\u00b0C' : '--';
    const fmtTime = ts => {
        if (typeof ts === 'number') ts = new Date(ts).toISOString().replace('T', ' ').slice(0, 19);
        if (typeof ts !== 'string') return '';
        return new Date(ts.replace(' ', 'T')).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    };

    function buildFillDatasets(label, meanXY, minXY, maxXY, color, orderBase) {
        const alpha = color.replace(')', ', 0.15)').replace('rgb', 'rgba');
        return [
            {
                label: '__min_' + label, data: minXY,
                borderColor: 'transparent', backgroundColor: 'transparent',
                pointRadius: 0, fill: false, tension: 0.3, order: orderBase + 2,
            },
            {
                label: '__fill_' + label, data: maxXY,
                borderColor: 'transparent', backgroundColor: alpha,
                pointRadius: 0, fill: '-1', tension: 0.3, order: orderBase + 1,
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

        // --- Temp Chart ---
        if (tempChart) tempChart.destroy();
        const ctx1 = document.getElementById('tempChart').getContext('2d');
        const datasets = [];

        const indoorXY = data.timestamps.map((ts, i) => ({x: ts, y: data.indoor_temps[i]}));

        if (isHourly && data.indoor_min_temps && data.indoor_min_temps.length) {
            const inMinXY = data.timestamps.map((ts, i) => ({x: ts, y: data.indoor_min_temps[i]}));
            const inMaxXY = data.timestamps.map((ts, i) => ({x: ts, y: data.indoor_max_temps[i]}));
            datasets.push(...buildFillDatasets('Indoor', indoorXY, inMinXY, inMaxXY, 'rgb(52, 211, 153)', 1));
            datasets[datasets.length - 1].pointRadius = showDots ? 1.5 : 0;
        } else {
            datasets.push({
                label: 'Indoor', data: indoorXY,
                borderColor: 'rgb(52, 211, 153)',
                tension: 0.3, pointRadius: showDots ? 1.5 : 0, borderWidth: 2.5, order: 1,
            });
        }

        const outXY = data.outdoor_timestamps.map((ts, i) => ({x: ts, y: data.outdoor_temps[i]}));
        const outMinXY = data.outdoor_timestamps.map((ts, i) => ({x: ts, y: data.outdoor_min_temps[i]}));
        const outMaxXY = data.outdoor_timestamps.map((ts, i) => ({x: ts, y: data.outdoor_max_temps[i]}));

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
                            filter: item => !item.text.startsWith('__'),
                        },
                    },
                    tooltip: {
                        backgroundColor: '#1a1d27', borderColor: '#2d3148', borderWidth: 1,
                        titleColor: '#d1d5db', bodyColor: '#d1d5db',
                        callbacks: {
                            title(items) { return items.length ? fmtTime(items[0].parsed.x) : ''; },
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
                        type: 'time',
                        time: { unit: 'hour', displayFormats: { hour: 'HH:mm', minute: 'HH:mm', day: 'MM-dd HH:mm' } },
                        ticks: { color: '#6b7280', maxTicksLimit: 10 },
                        grid: { color: '#1f2937' },
                    },
                    y: {
                        ticks: { color: '#6b7280', callback: v => (v != null ? v.toFixed(1) + '\u00b0C' : '') },
                        grid: { color: '#1f2937' },
                    },
                },
            },
        });

        // --- Delta Chart ---
        if (deltaChart) deltaChart.destroy();
        const ctx2 = document.getElementById('deltaChart').getContext('2d');

        const deltaXY = data.timestamps.map((ts, i) => ({x: ts, y: data.deltas[i]}));

        deltaChart = new Chart(ctx2, {
            type: 'line',
            data: {
                datasets: [{
                    label: '\u0394 Indoor \u2212 Outdoor',
                    data: deltaXY,
                    borderColor: 'rgb(251, 191, 36)',
                    pointRadius: showDots ? 1.5 : 0, borderWidth: 2,
                    tension: 0.3,
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
                            title(items) { return items.length ? fmtTime(items[0].parsed.x) : ''; },
                            label(ctx) {
                                const v = ctx.parsed.y;
                                return v != null ? ('\u0394: ' + (v>=0?'+':'') + v.toFixed(1) + '\u00b0C') : '\u0394: N/A';
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        type: 'time',
                        time: { unit: 'hour', displayFormats: { hour: 'HH:mm', minute: 'HH:mm', day: 'MM-dd HH:mm' } },
                        ticks: { color: '#6b7280', maxTicksLimit: 10 },
                        grid: { color: '#1f2937' },
                    },
                    y: {
                        ticks: { color: '#6b7280', callback: v => (v != null ? (v>=0?'+':'') + v.toFixed(1) + '\u00b0C' : '') },
                        grid: { color: '#1f2937' },
                    },
                },
            },
        });
    }

    fetchData();
    setInterval(fetchData, 60000);

    // --- Zoom & Pan ---
    let _panning = false, _panCanvas = null, _panStartX = 0;
    let _zooming = false, _zoomCanvas = null, _zoomStartX = 0, _zoomStartY = 0;
    let _anchor = {};
    let _touches = 0, _pinchDist0 = 0;
    const zoomBox = document.getElementById('zoomBox');

    function timeScale(ch) { return ch ? ch.scales.x : null; }

    function applyXRange(ch, min, max) {
        ch.options.scales.x.min = min;
        ch.options.scales.x.max = max;
        ch.update('none');
    }

    function zoomAll(factor, centerMs) {
        for (const ch of [tempChart, deltaChart]) {
            if (!ch) continue;
            const ts = timeScale(ch);
            if (!ts) continue;
            const r = ts.max - ts.min;
            const t = centerMs ? ((centerMs - ts.min) / r) : 0.5;
            applyXRange(ch, centerMs - r * factor * t, centerMs + r * factor * (1 - t));
        }
    }

    function resetZoom() {
        for (const ch of [tempChart, deltaChart]) {
            if (!ch) continue;
            applyXRange(ch, undefined, undefined);
        }
    }

    function captureAnchor() {
        _anchor = {};
        for (const id of ['tempChart', 'deltaChart']) {
            const ch = id === 'tempChart' ? tempChart : deltaChart;
            const ts = timeScale(ch);
            if (ts) _anchor[id] = { min: ts.min, max: ts.max };
        }
    }

    function startPan(canvas, clientX) {
        _panning = true; _zooming = false;
        _panCanvas = canvas; _panStartX = clientX;
        captureAnchor();
    }

    function movePan(clientX) {
        if (!_panning || !_panCanvas) return;
        const ratio = (clientX - _panStartX) / _panCanvas.clientWidth;
        for (const id of ['tempChart', 'deltaChart']) {
            const a = _anchor[id];
            if (!a) continue;
            const ch = id === 'tempChart' ? tempChart : deltaChart;
            const r = a.max - a.min;
            applyXRange(ch, a.min - ratio * r, a.max - ratio * r);
        }
    }

    function endPan() { _panning = false; _panCanvas = null; }

    function startZoom(canvas, clientX, clientY) {
        _zooming = true; _panning = false;
        _zoomCanvas = canvas; _zoomStartX = clientX; _zoomStartY = clientY;
        zoomBox.style.display = 'block';
        updateZoomBox(clientX, clientY);
    }

    function updateZoomBox(clientX, clientY) {
        if (!_zooming) return;
        const l = Math.min(_zoomStartX, clientX);
        const t = Math.min(_zoomStartY, clientY);
        const w = Math.abs(clientX - _zoomStartX);
        const h = Math.abs(clientY - _zoomStartY);
        zoomBox.style.left = l + 'px';
        zoomBox.style.top = t + 'px';
        zoomBox.style.width = w + 'px';
        zoomBox.style.height = h + 'px';
    }

    function endZoom() {
        if (!_zooming || !_zoomCanvas) { _zooming = false; zoomBox.style.display = 'none'; return; }
        const rect = _zoomCanvas.getBoundingClientRect();
        const ch = _zoomCanvas.id === 'tempChart' ? tempChart : deltaChart;
        const ts = timeScale(ch);
        if (!ts) { _zooming = false; zoomBox.style.display = 'none'; return; }
        const range = ts.max - ts.min;
        const x1 = Math.min(_zoomStartX, _zoomMouseX || _zoomStartX);
        const x2 = Math.max(_zoomStartX, _zoomMouseX || _zoomStartX);
        const minMs = ts.min + ((x1 - rect.left) / rect.width) * range;
        const maxMs = ts.min + ((x2 - rect.left) / rect.width) * range;

        for (const ch2 of [tempChart, deltaChart]) {
            if (!ch2) continue;
            const ts2 = timeScale(ch2);
            if (!ts2) continue;
            const r2 = ts2.max - ts2.min;
            const t1 = (minMs - ts2.min) / r2;
            const t2 = (maxMs - ts2.min) / r2;
            applyXRange(ch2, ts2.min + t1 * r2, ts2.min + t2 * r2);
        }
        _zooming = false; zoomBox.style.display = 'none';
    }

    let _zoomMouseX = 0, _zoomMouseY = 0;

    // --- Mouse ---
    for (const id of ['tempChart', 'deltaChart']) {
        const c = document.getElementById(id);
        c.addEventListener('mousedown', e => {
            if (e.ctrlKey) startZoom(e.target, e.clientX, e.clientY);
            else startPan(e.target, e.clientX);
        });
    }

    window.addEventListener('mousemove', e => {
        _zoomMouseX = e.clientX; _zoomMouseY = e.clientY;
        if (_zooming) updateZoomBox(e.clientX, e.clientY);
        else movePan(e.clientX);
    });

    window.addEventListener('mouseup', () => {
        if (_zooming) endZoom();
        endPan();
    });

    // --- Touch ---
    for (const id of ['tempChart', 'deltaChart']) {
        const canvas = document.getElementById(id);
        canvas.addEventListener('touchstart', e => {
            if (e.touches.length === 1) {
                startPan(canvas, e.touches[0].clientX);
            } else if (e.touches.length === 2) {
                endPan(); endZoom();
                _touches = 2;
                _pinchDist0 = Math.hypot(
                    e.touches[0].clientX - e.touches[1].clientX,
                    e.touches[0].clientY - e.touches[1].clientY
                );
                captureAnchor();
            }
        }, { passive: true });

        canvas.addEventListener('touchmove', e => {
            if (_touches === 2 && e.touches.length === 2) {
                e.preventDefault();
                const dist = Math.hypot(
                    e.touches[0].clientX - e.touches[1].clientX,
                    e.touches[0].clientY - e.touches[1].clientY
                );
                if (_pinchDist0 > 0 && dist > 0) {
                    const factor = _pinchDist0 / dist;
                    const cx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
                    const ch = id === 'tempChart' ? tempChart : deltaChart;
                    const ts = timeScale(ch);
                    if (!ts) return;
                    const rect = canvas.getBoundingClientRect();
                    const centerMs = ts.min + (cx - rect.left) / canvas.clientWidth * (ts.max - ts.min);
                    zoomAll(factor, centerMs);
                    _pinchDist0 = dist;
                }
            } else if (_panning) {
                movePan(e.touches[0].clientX);
            }
        }, { passive: false });

        canvas.addEventListener('touchend', e => {
            if (e.touches.length === 0) { _touches = 0; endPan(); }
        });
    }

    // --- Wheel zoom (Ctrl+wheel) ---
    function onWheel(e) {
        if (!e.ctrlKey) return;
        e.preventDefault();
        const canvas = e.target;
        const ch = canvas.id === 'tempChart' ? tempChart : deltaChart;
        const ts = timeScale(ch);
        if (!ts) return;
        const rect = canvas.getBoundingClientRect();
        const cx = ts.min + (e.clientX - rect.left) / canvas.clientWidth * (ts.max - ts.min);
        zoomAll(e.deltaY < 0 ? 0.7 : 1.3, cx);
    }
    document.getElementById('tempChart').addEventListener('wheel', onWheel, { passive: false });
    document.getElementById('deltaChart').addEventListener('wheel', onWheel, { passive: false });
    document.getElementById('tempChart').addEventListener('dblclick', resetZoom);
    document.getElementById('deltaChart').addEventListener('dblclick', resetZoom);
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
        rows_in, rows_out = aggregate_hourly(rows)
        # Build a unified list for stats/compat: indoor first, then outdoor
        rows = rows_in + rows_out
        indoor_min_temps = [r['indoor_min_c'] for r in rows_in if r['indoor_min_c'] is not None]
        indoor_max_temps = [r['indoor_max_c'] for r in rows_in if r['indoor_max_c'] is not None]
    else:
        rows_in = None
        rows_out = None

    if not rows:
        return jsonify({
            "timestamps": [], "indoor_temps": [], "outdoor_temps": [],
            "outdoor_max_temps": [], "outdoor_min_temps": [],
            "indoor_min_temps": [], "indoor_max_temps": [],
            "outdoor_timestamps": [], "deltas": [],
            "current_in": None, "avg_in": None, "avg_out": None,
        })

    # --- Build response arrays ---
    if rows_in is not None and rows_out is not None:
        # Hourly mode: indoor and outdoor have their own timestamp axes
        in_ds = maybe_downsample(rows_in, 'raw', max_points=250)
        out_ds = maybe_downsample(rows_out, 'raw', max_points=250)

        timestamps = [r['timestamp'] for r in in_ds]
        indoor_temps = [r['temperature_c'] for r in in_ds]
        indoor_min_temps = [r['indoor_min_c'] for r in in_ds]
        indoor_max_temps = [r['indoor_max_c'] for r in in_ds]

        outdoor_temps = [r['outdoor_temp_c'] for r in out_ds]
        outdoor_max_temps = [r['outdoor_max_c'] for r in out_ds]
        outdoor_min_temps = [r['outdoor_min_c'] for r in out_ds]
        outdoor_timestamps = [r['timestamp'] for r in out_ds if r['timestamp']]

        # Delta: match each indoor hour against an outdoor hour at the same timestamp
        out_lookup = {
            r['timestamp']: r['outdoor_temp_c']
            for r in rows_out if r['outdoor_temp_c'] is not None
        }
        deltas = []
        for r in in_ds:
            out_val = out_lookup.get(r['timestamp'])
            if r['temperature_c'] is not None and out_val is not None:
                deltas.append(r['temperature_c'] - out_val)
            else:
                deltas.append(None)
    else:
        # Raw mode
        downsampled = maybe_downsample(rows, aggregation, max_points=500)

        timestamps = [r['timestamp'] for r in downsampled]
        indoor_temps = [r['temperature_c'] for r in downsampled]
        outdoor_temps = [r['outdoor_temp_c'] for r in downsampled]
        outdoor_max_temps = [r['outdoor_max_c'] for r in downsampled]
        outdoor_min_temps = [r['outdoor_min_c'] for r in downsampled]
        outdoor_timestamps = [
            r['outdoor_timestamp'] if r['outdoor_timestamp'] else None
            for r in downsampled
        ]
        indoor_min_temps = []
        indoor_max_temps = []

        # Build outdoor lookup from raw rows for delta (indexed by HH:MM)
        outdoor_by_ts = {}
        for r in rows:
            ots = r['outdoor_timestamp']
            if ots and r['outdoor_temp_c'] is not None:
                key = ots.split(' ')[1][:5] if ' ' in ots else ots[:5]
                outdoor_by_ts[key] = r['outdoor_temp_c']

        deltas = []
        for r in downsampled:
            ts_hm = r['timestamp'].split(' ')[1][:5]
            out_val = outdoor_by_ts.get(ts_hm)
            if r['temperature_c'] is not None and out_val is not None:
                deltas.append(r['temperature_c'] - out_val)
            else:
                deltas.append(None)

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
        "current_in": all_in[-1] if all_in else None,
        "avg_in": avg_in,
        "avg_out": avg_out,
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
