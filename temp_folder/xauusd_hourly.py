"""
XAUUSD Hourly Profit Analysis
Shows past 5 days profit by hour, with AKCM vs Regular clients
Excludes test accounts (NAME/GROUP contains 'test')
Supports filtering by CMD (Buy/Sell) and SID (1/5/6)
"""
import os
from datetime import datetime, timedelta

import mysql.connector
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import json

# Load environment variables
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

app = FastAPI()

# Database config
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "charset": os.getenv("DB_CHARSET", "utf8mb4"),
}
FXBACK_DB = os.getenv("FXBACK_DB_NAME", "fxbackoffice").replace("'", "").replace('"', "").strip()


def get_trade_data():
    """
    Fetch XAUUSD closed trades for past 5 days.
    Returns raw data with CMD and SID for frontend filtering.
    """
    end_date = datetime(2026, 1, 30, 23, 59, 59)
    start_date = datetime(2026, 1, 26, 0, 0, 0)

    sql = f"""
    SELECT 
        t.close_time,
        t.sid,
        t.CMD,
        t.PROFIT,
        u.`GROUP` AS user_group
    FROM {FXBACK_DB}.mt4_trades t
    INNER JOIN {FXBACK_DB}.mt4_users u 
        ON u.LOGIN = t.LOGIN AND u.sid = t.sid
    WHERE t.closeDate >= '{start_date.strftime('%Y-%m-%d')}'
      AND t.closeDate <= '{end_date.strftime('%Y-%m-%d')}'
      AND t.CMD IN (0, 1)
      AND t.sid IN (1, 5, 6)
      AND t.symbol = 'XAUUSD'
      AND t.isDeleted = 0
      AND LOWER(u.NAME) NOT LIKE '%test%'
      AND LOWER(u.`GROUP`) NOT LIKE '%test%'
    """

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        print(f"Fetching data from {start_date} to {end_date}...")
        df = pd.read_sql(sql, conn)
        conn.close()
        print(f"Fetched {len(df)} trades")
        return df
    except Exception as e:
        print(f"Database error: {e}")
        return pd.DataFrame()


def process_data(df):
    """Process raw data: add hour bucket and client type"""
    if df.empty:
        return df
    
    df = df.copy()
    df["close_time"] = pd.to_datetime(df["close_time"])
    df["hour_str"] = df["close_time"].dt.strftime("%m-%d %H:00")
    df["hour_ts"] = df["close_time"].dt.floor("h").astype(int) // 10**9  # Unix timestamp
    
    # Identify AKCM clients
    df["client_type"] = df["user_group"].apply(
        lambda x: "AKCM" if str(x).upper().startswith("AKCM") else "Regular"
    )
    
    # Convert types for JSON
    df["sid"] = df["sid"].astype(int)
    df["CMD"] = df["CMD"].astype(int)
    df["PROFIT"] = df["PROFIT"].astype(float)
    
    return df


@app.get("/api/trades")
def get_trades():
    """API endpoint to get all trade data for frontend filtering"""
    df = get_trade_data()
    df = process_data(df)
    
    if df.empty:
        return JSONResponse({"data": [], "hours": []})
    
    # Generate all 120 hours for complete x-axis
    start_date = datetime(2026, 1, 26, 0, 0, 0)
    end_date = datetime(2026, 1, 30, 23, 0, 0)
    all_hours = pd.date_range(start=start_date, end=end_date, freq="h")
    hours_list = [{"hour_str": h.strftime("%m-%d %H:00"), "hour_ts": int(h.timestamp())} for h in all_hours]
    
    # Return trade data
    trades = df[["hour_str", "hour_ts", "sid", "CMD", "PROFIT", "client_type"]].to_dict(orient="records")
    
    return JSONResponse({"data": trades, "hours": hours_list})


@app.get("/", response_class=HTMLResponse)
def index():
    """Main page with Plotly chart and filters"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>XAUUSD Hourly Profit Analysis</title>
        <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #1a1a2e; 
                color: #eee; 
                padding: 20px;
            }
            .container { max-width: 1600px; margin: 0 auto; }
            h1 { 
                text-align: center; 
                margin-bottom: 5px;
                color: #f39c12;
            }
            .subtitle {
                text-align: center;
                color: #888;
                margin-bottom: 15px;
            }
            
            /* Filter Panel */
            .filter-panel {
                display: flex;
                justify-content: center;
                gap: 40px;
                background: #16213e;
                padding: 15px 25px;
                border-radius: 8px;
                margin-bottom: 15px;
            }
            .filter-group {
                display: flex;
                align-items: center;
                gap: 15px;
            }
            .filter-label {
                color: #f39c12;
                font-weight: bold;
                font-size: 0.9em;
            }
            .checkbox-group {
                display: flex;
                gap: 12px;
            }
            .checkbox-item {
                display: flex;
                align-items: center;
                gap: 5px;
                cursor: pointer;
            }
            .checkbox-item input[type="checkbox"] {
                width: 16px;
                height: 16px;
                cursor: pointer;
                accent-color: #3498db;
            }
            .checkbox-item label {
                cursor: pointer;
                font-size: 0.9em;
            }
            .checkbox-item.cmd-buy label { color: #2ecc71; }
            .checkbox-item.cmd-sell label { color: #e74c3c; }
            .checkbox-item.sid-1 label { color: #3498db; }
            .checkbox-item.sid-5 label { color: #9b59b6; }
            .checkbox-item.sid-6 label { color: #1abc9c; }
            
            /* Stats */
            .stat-grid {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 15px;
                margin-bottom: 15px;
            }
            .stat-card {
                background: #16213e;
                border-radius: 8px;
                padding: 12px;
                text-align: center;
            }
            .stat-card.akcm { border-left: 4px solid #e74c3c; }
            .stat-card.regular { border-left: 4px solid #3498db; }
            .stat-label { color: #888; font-size: 0.85em; margin-bottom: 3px; }
            .stat-value { font-size: 1.3em; font-weight: bold; }
            .stat-value.positive { color: #2ecc71; }
            .stat-value.negative { color: #e74c3c; }
            .stat-sub { color: #666; font-size: 0.8em; margin-top: 3px; }
            
            #chart { 
                width: 100%; 
                height: 520px; 
                background: #16213e;
                border-radius: 8px;
            }
            .footer {
                text-align: center;
                margin-top: 12px;
                color: #666;
                font-size: 0.8em;
            }
            .loading {
                text-align: center;
                padding: 50px;
                color: #888;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>XAUUSD Hourly Profit Analysis</h1>
            <p class="subtitle">2026-01-26 00:00 to 2026-01-30 23:00 (120 hours)</p>
            
            <!-- Filter Panel -->
            <div class="filter-panel">
                <div class="filter-group">
                    <span class="filter-label">CMD:</span>
                    <div class="checkbox-group">
                        <div class="checkbox-item cmd-buy">
                            <input type="checkbox" id="cmd-0" value="0" checked>
                            <label for="cmd-0">Buy (0)</label>
                        </div>
                        <div class="checkbox-item cmd-sell">
                            <input type="checkbox" id="cmd-1" value="1" checked>
                            <label for="cmd-1">Sell (1)</label>
                        </div>
                    </div>
                </div>
                <div class="filter-group">
                    <span class="filter-label">SID:</span>
                    <div class="checkbox-group">
                        <div class="checkbox-item sid-1">
                            <input type="checkbox" id="sid-1" value="1" checked>
                            <label for="sid-1">SID 1</label>
                        </div>
                        <div class="checkbox-item sid-5">
                            <input type="checkbox" id="sid-5" value="5" checked>
                            <label for="sid-5">SID 5</label>
                        </div>
                        <div class="checkbox-item sid-6">
                            <input type="checkbox" id="sid-6" value="6" checked>
                            <label for="sid-6">SID 6</label>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Stats -->
            <div class="stat-grid">
                <div class="stat-card">
                    <div class="stat-label">Total Profit</div>
                    <div class="stat-value" id="stat-total">$0.00</div>
                    <div class="stat-sub" id="stat-total-trades">0 trades</div>
                </div>
                <div class="stat-card akcm">
                    <div class="stat-label">AKCM Clients</div>
                    <div class="stat-value" id="stat-akcm">$0.00</div>
                    <div class="stat-sub" id="stat-akcm-trades">0 trades</div>
                </div>
                <div class="stat-card regular">
                    <div class="stat-label">Regular Clients</div>
                    <div class="stat-value" id="stat-regular">$0.00</div>
                    <div class="stat-sub" id="stat-regular-trades">0 trades</div>
                </div>
            </div>
            
            <div id="chart"><div class="loading">Loading data...</div></div>
            <p class="footer">AKCM = GROUP starts with 'AKCM' | Regular = All other valid clients | Excludes test accounts</p>
        </div>
        
        <script>
            // Global data storage
            let allTrades = [];
            let allHours = [];
            
            // Fetch data on load
            fetch('/api/trades')
                .then(res => res.json())
                .then(data => {
                    allTrades = data.data;
                    allHours = data.hours;
                    updateChart();
                })
                .catch(err => {
                    document.getElementById('chart').innerHTML = '<div class="loading">Error loading data</div>';
                });
            
            // Add event listeners to all checkboxes
            document.querySelectorAll('.filter-panel input[type="checkbox"]').forEach(cb => {
                cb.addEventListener('change', updateChart);
            });
            
            function getSelectedFilters() {
                const cmds = [];
                const sids = [];
                
                if (document.getElementById('cmd-0').checked) cmds.push(0);
                if (document.getElementById('cmd-1').checked) cmds.push(1);
                if (document.getElementById('sid-1').checked) sids.push(1);
                if (document.getElementById('sid-5').checked) sids.push(5);
                if (document.getElementById('sid-6').checked) sids.push(6);
                
                return { cmds, sids };
            }
            
            function updateChart() {
                const { cmds, sids } = getSelectedFilters();
                
                // Filter trades
                const filtered = allTrades.filter(t => 
                    cmds.includes(t.CMD) && sids.includes(t.sid)
                );
                
                // Aggregate by hour and client_type
                const hourMap = {};  // hour_str -> { AKCM: {profit, count}, Regular: {profit, count} }
                
                // Initialize all hours with zero
                allHours.forEach(h => {
                    hourMap[h.hour_str] = {
                        AKCM: { profit: 0, count: 0 },
                        Regular: { profit: 0, count: 0 }
                    };
                });
                
                // Aggregate filtered data
                filtered.forEach(t => {
                    if (hourMap[t.hour_str]) {
                        hourMap[t.hour_str][t.client_type].profit += t.PROFIT;
                        hourMap[t.hour_str][t.client_type].count += 1;
                    }
                });
                
                // Build traces
                const hours = allHours.map(h => h.hour_str);
                const akcmProfits = hours.map(h => hourMap[h].AKCM.profit);
                const akcmCounts = hours.map(h => hourMap[h].AKCM.count);
                const regularProfits = hours.map(h => hourMap[h].Regular.profit);
                const regularCounts = hours.map(h => hourMap[h].Regular.count);
                
                const traces = [
                    {
                        x: hours,
                        y: akcmProfits,
                        name: 'AKCM Clients',
                        type: 'bar',
                        marker: { color: '#e74c3c' },
                        text: akcmCounts,
                        hovertemplate: '%{x}<br>Profit: $%{y:,.2f}<br>Trades: %{text}<extra></extra>'
                    },
                    {
                        x: hours,
                        y: regularProfits,
                        name: 'Regular Clients',
                        type: 'bar',
                        marker: { color: '#3498db' },
                        text: regularCounts,
                        hovertemplate: '%{x}<br>Profit: $%{y:,.2f}<br>Trades: %{text}<extra></extra>'
                    }
                ];
                
                const layout = {
                    barmode: 'group',
                    paper_bgcolor: '#16213e',
                    plot_bgcolor: '#16213e',
                    font: { color: '#eee', size: 11 },
                    xaxis: {
                        title: 'Date & Hour',
                        tickangle: -90,
                        tickfont: { size: 8 },
                        gridcolor: '#2a2a4a',
                        dtick: 6
                    },
                    yaxis: {
                        title: 'Profit (USD)',
                        gridcolor: '#2a2a4a',
                        zeroline: true,
                        zerolinecolor: '#444'
                    },
                    legend: {
                        orientation: 'h',
                        y: 1.12,
                        x: 0.5,
                        xanchor: 'center'
                    },
                    margin: { t: 50, b: 90, l: 70, r: 20 },
                    bargap: 0.15,
                    bargroupgap: 0.1
                };
                
                Plotly.newPlot('chart', traces, layout, {responsive: true});
                
                // Update stats
                const akcmTotal = akcmProfits.reduce((a, b) => a + b, 0);
                const akcmTradeCount = akcmCounts.reduce((a, b) => a + b, 0);
                const regularTotal = regularProfits.reduce((a, b) => a + b, 0);
                const regularTradeCount = regularCounts.reduce((a, b) => a + b, 0);
                const grandTotal = akcmTotal + regularTotal;
                const grandTradeCount = akcmTradeCount + regularTradeCount;
                
                updateStat('stat-total', grandTotal, 'stat-total-trades', grandTradeCount);
                updateStat('stat-akcm', akcmTotal, 'stat-akcm-trades', akcmTradeCount);
                updateStat('stat-regular', regularTotal, 'stat-regular-trades', regularTradeCount);
            }
            
            function updateStat(valueId, profit, tradesId, trades) {
                const valueEl = document.getElementById(valueId);
                valueEl.textContent = '$' + profit.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
                valueEl.className = 'stat-value ' + (profit >= 0 ? 'positive' : 'negative');
                document.getElementById(tradesId).textContent = trades.toLocaleString() + ' trades';
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


if __name__ == "__main__":
    print("=" * 50)
    print("XAUUSD Hourly Profit Analysis Server")
    print("=" * 50)
    print("Open http://localhost:8112 in your browser")
    print("=" * 50)
    uvicorn.run("xauusd_hourly:app", host="0.0.0.0", port=8112, reload=False)
