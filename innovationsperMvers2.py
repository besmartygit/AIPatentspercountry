#!/usr/bin/env python3
"""
Compute patents-per-million from an OECD CSV export and build an HTML chart
with Highcharts, including export (PNG/JPEG/PDF/SVG) and a "Save PNG" button.

Chart is stacked-by-year:
- one bar per COUNTRY
- one color per YEAR (TIME_PERIOD)
- years are detected dynamically (no hard-coded years)
- typography + palette styled to echo the OECD logo
"""

import pandas as pd
from pathlib import Path
import json

# 👉 Set your paths here
input_file = Path("countriespatentsfiltered.csv")
output_file = Path("patents_per_million.csv")

# 1) Load the OECD CSV
df = pd.read_csv(input_file)

# 2) Population lookup (millions) — replace with official data if needed
population_millions = {
    "AUS": 27.0, "DEU": 83.0, "CHE": 9.0, "ESP": 48.0,
    "FRA": 68.0, "GBR": 68.0, "JPN": 125.0, "KOR": 52.0,
    "NLD": 18.0, "USA": 333.0, "WXOECD": 1350.0
}
pop_df = pd.DataFrame(list(population_millions.items()),
                      columns=["COUNTRY", "Population_millions"])

# 3) Merge and compute ratio (guard against missing pop and zeros)
merged = df.merge(pop_df, on="COUNTRY", how="left")
merged = merged.dropna(subset=["Population_millions"])
merged = merged[merged["Population_millions"] > 0]
merged["Patents_per_million"] = merged["OBS_VALUE"] / merged["Population_millions"]

# 4) Save output CSV (for reference / reuse)
cols_out = ["COUNTRY", "Country", "TIME_PERIOD",
            "OBS_VALUE", "Population_millions", "Patents_per_million"]
merged.to_csv(output_file, index=False,
              columns=[c for c in cols_out if c in merged.columns])
print(f"Saved: {output_file.resolve()}")

# 5) Prepare chart data for stacked columns (dynamic years)
df_ratio = pd.read_csv(output_file)

# Ensure TIME_PERIOD is numeric year
df_ratio["TIME_PERIOD"] = pd.to_numeric(df_ratio["TIME_PERIOD"], errors="coerce")
df_ratio = df_ratio.dropna(subset=["TIME_PERIOD", "Patents_per_million"])
df_ratio["TIME_PERIOD"] = df_ratio["TIME_PERIOD"].astype(int)

# Collapse duplicates per (COUNTRY, TIME_PERIOD)
df_ratio = (df_ratio
            .groupby(["COUNTRY", "TIME_PERIOD"], as_index=False)["Patents_per_million"]
            .mean())

# Pivot to countries × years
pivot = df_ratio.pivot(index="COUNTRY", columns="TIME_PERIOD", values="Patents_per_million")
pivot = pivot.sort_index()

# Dynamic years present (sorted)
years = sorted(pivot.columns.tolist())

# ---- Sorting mode for bar order ----
# "total"  -> sort by total stack height (best for stacked view)
# "latest" -> sort by each country's chronologically latest available value
# "year:YYYY" -> sort by a specific year present in the data
SORT_MODE = "total"

if SORT_MODE == "total":
    categories = (pivot.sum(axis=1, skipna=True)
                        .sort_values(ascending=False)
                        .index.tolist())
elif SORT_MODE.startswith("year:"):
    try:
        y = int(SORT_MODE.split(":", 1)[1])
    except Exception:
        y = max(years) if years else None
    if (y is None) or (y not in pivot.columns):
        y = max(years) if years else None
    categories = pivot.index.tolist() if y is None else pivot[y].sort_values(ascending=False).index.tolist()
else:  # "latest"
    pivot_chrono = pivot.sort_index(axis=1)  # ensure year ascending
    latest_vals = pivot_chrono.apply(
        lambda row: row.dropna().iloc[-1] if not row.dropna().empty else float("nan"),
        axis=1
    )
    categories = latest_vals.sort_values(ascending=False).index.tolist()

# Build JS-ready data dict: { "2019":[...], "2020":[...], ... } aligned to categories
def series_for_year(y: int):
    s = pivot.reindex(categories)[y] if y in pivot.columns else pd.Series(index=categories, dtype=float)
    return [None if pd.isna(v) else round(float(v), 2) for v in s.to_list()]

data_by_year = {str(y): series_for_year(y) for y in years}

# 6) Build the HTML with Highcharts (stacked by year) + exporting
#    NOTE: This is a plain triple-quoted string (NOT an f-string).
#    We replace the placeholders __CATEGORIES__, __YEARS__, __DATA_BY_YEAR__ after.
html_template = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Patents per million — stacked by year</title>
  <!-- Font: Montserrat (close match to OECD wordmark) -->
  <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap" rel="stylesheet">
  <!-- Highcharts core + data + exporting modules -->
  <script src="https://code.highcharts.com/highcharts.js"></script>
  <script src="https://code.highcharts.com/modules/exporting.js"></script>
  <script src="https://code.highcharts.com/modules/offline-exporting.js"></script>
  <script src="https://code.highcharts.com/modules/export-data.js"></script>
  <style>
    :root {
      --brand-font: 'Montserrat', Arial, Helvetica, sans-serif;
      --brand-text: #4d4f53;   /* neutral text (grey) */
      --ui-bg: #ffffff;
      --ui-border: #e5e7eb;
    }
    * { box-sizing: border-box; }
    body {
      font-family: var(--brand-font);
      color: var(--brand-text);
      background: var(--ui-bg);
      margin: 20px;
    }
    h2 { font-weight: 600; margin: 0 0 8px; }
    #container { width: 100%; height: 560px; }
    .controls {
      margin: 12px 0 16px;
      display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
    }
    button, select {
      font-family: var(--brand-font);
      padding: 8px 12px; cursor: pointer; border-radius: 8px;
      border: 1px solid var(--ui-border); background: #f9fafb;
    }
    button:hover { background:#f3f4f6; }
  </style>
</head>
<body>
  <h2>Patents per Million — stacked by year</h2>
  <div class="controls">
    <button id="savePng">💾 Save PNG</button>
    <label>
      Stacking:
      <select id="stackingMode">
        <option value="normal" selected>Stacked</option>
        <option value="">Grouped</option>
        <option value="percent">100% Stacked</option>
      </select>
    </label>
  </div>
  <div id="container"></div>

  <script>
    // Embedded data from Python (dynamic, no hard-coded years)
    const categories = __CATEGORIES__;
    const years = __YEARS__;                 // e.g., [2019, 2020, 2021, 2022]
    const dataByYear = __DATA_BY_YEAR__;     // { "2019":[...], "2020":[...], ... }

    // OECD-like palette from the logo (blues & greens + grey for overflow)
    const brandPalette = [
      '#2E7CB6', // blue
      '#86BC25', // green
      '#0B4F8A', // deep blue
      '#A6CE39', // light green
      '#1C5A99', // mid blue
      '#6FAE21', // mid green
      '#154A7D', // navy
      '#C3E26A', // pale green
      '#6D6E71'  // grey
    ];

    // Apply font + colors globally to Highcharts
    Highcharts.setOptions({
      chart: {
        style: { fontFamily: "Montserrat, Arial, Helvetica, sans-serif" }
      },
      colors: brandPalette,
      title: { style: { fontWeight: '600' } },
      subtitle: { style: { fontWeight: '400' } },
      legend: { itemStyle: { fontWeight: '500' } }
    });

    // Build one series per year dynamically (colors come from brandPalette)
    const series = years.map(y => {
      const key = String(y);
      return {
        name: key,
        data: dataByYear[key] || []
      };
    });

    const subtitleText = years.length ? `Years: ${years[0]}–${years[years.length - 1]}` : '';

    const chart = Highcharts.chart('container', {
      chart: { type: 'column' },
      title: { text: 'Patents per Million (stacked by year)' },
      subtitle: { text: subtitleText },
      xAxis: {
        categories: categories,
        title: { text: 'Country' },
        tickInterval: 1
      },
      yAxis: {
        min: 0,
        title: { text: 'Patents per million people' },
        stackLabels: { enabled: true }
      },
      legend: { title: { text: 'Year' } },
      tooltip: {
        shared: true,
        headerFormat: '<b>{point.key}</b><br/>',
        pointFormat: '<span style="color:{point.color}">\u25CF</span> {series.name}: <b>{point.y:.2f}</b><br/>',
        footerFormat: '<span style="opacity:0.7">Total: {point.total:.2f}</span>'
      },
      plotOptions: {
        column: {
          stacking: 'normal',   // default; UI below can change it
          pointPadding: 0.05,
          borderWidth: 0,
          dataLabels: { enabled: false }
        },
        series: {
          animation: { duration: 300 }
        }
      },
      series,
      credits: { enabled: false },
      exporting: { enabled: true }  // shows the download menu (PNG/JPEG/PDF/SVG)
    });

    // Programmatic export (Save PNG button)
    document.getElementById('savePng').addEventListener('click', () => {
      chart?.exportChartLocal?.({ type: 'image/png', filename: 'patents_per_million_stacked' })
        ?? alert('Local export not available; use the chart menu (≡) to download.');
    });

    // Stacking mode toggle (stacked / grouped / 100%)
    document.getElementById('stackingMode').addEventListener('change', (e) => {
      const mode = e.target.value || undefined; // '' => grouped (no stacking)
      chart.update({ plotOptions: { column: { stacking: mode } } }, true, true, false);
    });
  </script>
</body>
</html>
"""

# Inject the JSON safely (no f-string) by replacing placeholders
html_filled = (
    html_template
    .replace("__CATEGORIES__", json.dumps(categories))
    .replace("__YEARS__", json.dumps(years))
    .replace("__DATA_BY_YEAR__", json.dumps(data_by_year))
)

# 7) Save the HTML file
html_path = Path("patents_per_million_chart.html")
html_path.write_text(html_filled, encoding="utf-8")
print(f"✅ Chart saved to: {html_path.resolve()}")
