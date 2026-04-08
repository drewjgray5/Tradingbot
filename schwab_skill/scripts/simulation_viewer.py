#!/usr/bin/env python3
"""
Minimal simulation viewer server. Serves simulation JSON and a simple HTML viewer.
Run: python scripts/simulation_viewer.py
Then open http://localhost:3000/simulation/{sim_id} from Discord links.

Set SIMULATION_VIEWER_URL in .env if using a different host/port.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SIMULATIONS_DIR = SKILL_DIR / "mirofish_sims"
PORT = 3000


def _load_env():
    path = SKILL_DIR / ".env"
    if not path.exists():
        return {}
    vals = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"\'')
    return vals


def _get_port():
    env = _load_env()
    url = env.get("SIMULATION_VIEWER_URL", "http://127.0.0.1:3000")
    try:
        if ":" in url.split("//")[-1]:
            return int(url.rstrip("/").rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        pass
    return PORT


VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MiroFish Simulation</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0f1419;
      --surface: #1a2332;
      --surface-hover: #243044;
      --border: #2d3a4d;
      --text: #e6edf3;
      --text-muted: #8b949e;
      --accent: #58a6ff;
      --bullish: #3fb950;
      --bearish: #f85149;
      --neutral: #8b949e;
    }
    * { box-sizing: border-box; }
    body {
      font-family: 'Outfit', system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      margin: 0;
      padding: 2rem 1rem;
      line-height: 1.6;
    }
    .container { max-width: 680px; margin: 0 auto; }
    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 1.5rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--border);
    }
    .logo { font-size: 0.75rem; color: var(--text-muted); font-family: 'JetBrains Mono', monospace; }
    .ticker {
      font-size: 1.75rem;
      font-weight: 700;
      color: var(--accent);
      letter-spacing: 0.05em;
    }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.25rem 1.5rem;
      margin-bottom: 1rem;
    }
    .card h2 {
      font-size: 0.8rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-muted);
      margin: 0 0 0.75rem 0;
    }
    .summary-badge {
      display: inline-block;
      font-size: 1.1rem;
      font-weight: 600;
      padding: 0.5rem 1rem;
      border-radius: 8px;
      margin: 0.25rem 0;
    }
    .summary-strong { background: rgba(63, 185, 80, 0.2); color: var(--bullish); }
    .summary-moderate { background: rgba(88, 166, 255, 0.2); color: var(--accent); }
    .summary-neutral { background: rgba(139, 148, 158, 0.2); color: var(--neutral); }
    .summary-pullback { background: rgba(248, 81, 73, 0.15); color: #ff7b72; }
    .summary-bulltrap { background: rgba(248, 81, 73, 0.2); color: var(--bearish); }
    .conviction-section { margin: 1rem 0; }
    .conviction-label {
      display: flex;
      justify-content: space-between;
      font-size: 0.9rem;
      margin-bottom: 0.5rem;
      color: var(--text-muted);
    }
    .conviction-value { font-weight: 600; color: var(--text); }
    .meter-track {
      height: 12px;
      background: var(--border);
      border-radius: 6px;
      overflow: hidden;
      margin: 0.5rem 0 1rem 0;
    }
    .meter-fill {
      height: 100%;
      border-radius: 6px;
      transition: width 0.4s ease;
    }
    .meter-legend {
      display: flex;
      justify-content: space-between;
      font-size: 0.7rem;
      color: var(--text-muted);
      font-family: 'JetBrains Mono', monospace;
    }
    .agent-grid { display: grid; gap: 0.75rem; }
    .agent-card {
      background: var(--surface-hover);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1rem 1.25rem;
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 1rem;
      align-items: start;
    }
    .agent-icon {
      width: 44px;
      height: 44px;
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.25rem;
    }
    .agent-icon.trend { background: rgba(63, 185, 80, 0.2); }
    .agent-icon.revert { background: rgba(88, 166, 255, 0.2); }
    .agent-icon.fomo { background: rgba(210, 153, 34, 0.2); }
    .agent-content { min-width: 0; }
    .agent-name { font-weight: 600; margin-bottom: 0.25rem; }
    .agent-score {
      font-family: 'JetBrains Mono', monospace;
      font-weight: 600;
      font-size: 1rem;
    }
    .agent-score.pos { color: var(--bullish); }
    .agent-score.neg { color: var(--bearish); }
    .agent-score.neutral { color: var(--neutral); }
    .agent-reason { font-size: 0.9rem; color: var(--text-muted); margin-top: 0.5rem; }
    .seed-section {
      font-size: 0.85rem;
      color: var(--text-muted);
      white-space: pre-wrap;
      font-family: 'JetBrains Mono', monospace;
      max-height: 200px;
      overflow-y: auto;
    }
    .meta { font-size: 0.75rem; color: var(--text-muted); margin-top: 1rem; }
    .error-page { text-align: center; padding: 3rem; }
    .error-page h1 { color: var(--bearish); margin-bottom: 0.5rem; }
    .loading { text-align: center; padding: 2rem; color: var(--text-muted); }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <span class="logo">MiroFish</span>
      <span class="ticker" id="ticker">—</span>
    </div>

    <div id="content">
      <div class="loading">Loading simulation…</div>
    </div>
  </div>

  <script>
    function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
    const AGENT_META = {
      institutional_trend: { label: 'Institutional Trend-Follower', icon: '📈', class: 'trend' },
      mean_reversion: { label: 'Mean-Reversion Bot', icon: '🔄', class: 'revert' },
      retail_fomo: { label: 'Retail FOMO Trader', icon: '📢', class: 'fomo' }
    };
    function summaryClass(s) {
      if (!s) return 'neutral';
      const u = s.toLowerCase();
      if (u.includes('strong')) return 'strong';
      if (u.includes('moderate') && u.includes('continuation')) return 'moderate';
      if (u.includes('neutral') || u.includes('mixed')) return 'neutral';
      if (u.includes('pullback')) return 'pullback';
      if (u.includes('bull trap')) return 'bulltrap';
      return 'moderate';
    }
    function meterColor(score) {
      if (score >= 20) return '#3fb950';
      if (score >= -20) return '#8b949e';
      return '#f85149';
    }
    const simId = (window.location.pathname.split('/').pop() || new URLSearchParams(location.search).get('id') || '').trim();
    if (!simId || simId === 'simulation') {
      document.getElementById('content').innerHTML = '<div class="card error-page"><h1>No simulation ID</h1><p>Open this page from the Discord link.</p></div>';
    } else {
      fetch('/api/simulation/' + simId)
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(data => {
          const score = data.conviction_score ?? 0;
          const pct = Math.round((score + 100) / 2);
          const sc = summaryClass(data.summary);
          let html = '';
          html += '<div class="card"><h2>Verdict</h2>';
          html += '<div class="summary-badge summary-' + sc + '">' + (data.summary || '—') + '</div>';
          html += '<div class="conviction-section">';
          html += '<div class="conviction-label"><span>Conviction Score</span><span class="conviction-value">' + score + ' / 100</span></div>';
          html += '<div class="meter-track"><div class="meter-fill" style="width:' + pct + '%;background:' + meterColor(score) + '"></div></div>';
          html += '<div class="meter-legend"><span>Bearish -100</span><span>0</span><span>+100 Bullish</span></div>';
          html += '</div></div>';

          const votes = (data.agent_votes || []).slice().sort((a,b) => Math.abs(b.score||0) - Math.abs(a.score||0));
          if (votes.length) {
            html += '<div class="card"><h2>Agent Sentiment</h2><div class="agent-grid">';
            votes.forEach(a => {
              const meta = AGENT_META[a.name] || { label: (a.name || 'Agent').replace(/_/g, ' '), icon: '🤖', class: 'revert' };
              const scoreClass = a.score > 0 ? 'pos' : a.score < 0 ? 'neg' : 'neutral';
              html += '<div class="agent-card">';
              html += '<div class="agent-icon ' + meta.class + '">' + meta.icon + '</div>';
              html += '<div class="agent-content">';
              html += '<div class="agent-name">' + meta.label + '</div>';
              html += '<span class="agent-score ' + scoreClass + '">' + a.score + '</span>';
              if (a.reason) html += '<div class="agent-reason">' + esc(a.reason) + '</div>';
              html += '</div></div>';
            });
            html += '</div></div>';
          }

          if (data.seed_preview) {
            html += '<div class="card"><h2>Market Context</h2>';
            html += '<div class="seed-section">' + esc(data.seed_preview) + '</div>';
            html += '</div>';
          }
          html += '<div class="meta">Simulation ID: ' + (data.simulation_id || simId) + '</div>';
          document.getElementById('ticker').textContent = data.ticker || simId;
          document.getElementById('content').innerHTML = html;
        })
        .catch(e => {
          document.getElementById('content').innerHTML = '<div class="card error-page"><h1>Simulation not found</h1><p>ID: ' + simId + '</p><p>The simulation may have expired or the link is invalid.</p></div>';
        });
    }
  </script>
</body>
</html>
"""


class SimulationHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        parts = [p for p in path.split("/") if p]

        if parts and parts[0] == "api" and parts[1] == "simulation" and len(parts) >= 3:
            sim_id = parts[2]
            return self._serve_json(sim_id)

        if parts and parts[0] == "simulation" and len(parts) >= 2:
            sim_id = parts[1]
            return self._serve_viewer(sim_id)

        if path in ("", "/") or path == "/index.html":
            return self._serve_index()
        if path == "/health":
            return self._serve_health()

        self.send_error(404)

    def _serve_health(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def _serve_json(self, sim_id: str):
        fpath = SIMULATIONS_DIR / f"{sim_id}.json"
        if not fpath.exists():
            self.send_error(404)
            return
        try:
            raw = fpath.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]  # strip BOM
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            print(f"[viewer] JSON read error for {sim_id}: {e}")
            self.send_error(500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _serve_viewer(self, sim_id: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(VIEWER_HTML.encode("utf-8"))

    def _serve_index(self):
        html = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MiroFish Simulation Viewer</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600&display=swap" rel="stylesheet">
<style>
  body{font-family:Outfit,system-ui,sans-serif;background:#0f1419;color:#e6edf3;margin:0;padding:2rem;min-height:100vh}
  .c{max-width:500px;margin:0 auto}
  h1{font-size:1.5rem;margin-bottom:0.5rem}
  p{color:#8b949e;line-height:1.6}
  code{background:#1a2332;padding:0.2em 0.5em;border-radius:4px;font-size:0.9em}
</style>
</head>
<body><div class="c">
  <h1>MiroFish Simulation Viewer</h1>
  <p>Open a simulation from the Discord link:</p>
  <p><code>http://127.0.0.1:3000/simulation/{sim_id}</code></p>
  <p>Each trade signal includes a link to view the full market context and agent sentiment.</p>
</div></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        print(f"[viewer] {args[0]}")


def main():
    port = _get_port()
    if not SIMULATIONS_DIR.exists():
        SIMULATIONS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Simulation viewer at http://127.0.0.1:{port}")
    print("Open links from Discord: http://127.0.0.1:{port}/simulation/{{sim_id}}")
    print("Health check: http://127.0.0.1:{port}/health")
    HTTPServer(("127.0.0.1", port), SimulationHandler).serve_forever()


if __name__ == "__main__":
    main()
