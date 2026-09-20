"""Local web console for TradingAgents — start an analysis from the browser.

Standard library only. Run:  .venv/bin/python webui.py
Open: http://127.0.0.1:8756
"""
import json
import os
import subprocess
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
RUN_SCRIPT = ROOT / "webui_run.py"
LOG_DIR = ROOT / "reports" / "webui_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Keys to drop so .env (not stale shell exports) wins inside the subprocess.
STRIP_ENV = [
    "TRADINGAGENTS_LLM_PROVIDER", "TRADINGAGENTS_DEEP_THINK_LLM",
    "TRADINGAGENTS_QUICK_THINK_LLM", "TRADINGAGENTS_OUTPUT_LANGUAGE",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS", "TRADINGAGENTS_MAX_RISK_ROUNDS",
    "TRADINGAGENTS_LLM_MAX_RETRIES", "TRADINGAGENTS_CHECKPOINT_ENABLED",
    "DASHSCOPE_CN_API_KEY", "ALPHA_VANTAGE_API_KEY", "FRED_API_KEY",
]

STATE = {
    "running": False,
    "log_file": None,
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "ticker": None,
}


def clean_env():
    env = os.environ.copy()
    for k in STRIP_ENV:
        env.pop(k, None)
    return env


def start_run(ticker, date):
    if STATE["running"]:
        return False, "已有分析在运行,请等它跑完"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"{ticker}_{ts}.log"
    STATE.update(running=True, log_file=str(log_file), started_at=time.time(),
                 finished_at=None, exit_code=None, ticker=ticker)
    with log_file.open("w") as log_stream:
        proc = subprocess.Popen(
            [str(VENV_PY), str(RUN_SCRIPT), ticker, date],
            cwd=ROOT,
            env=clean_env(),
            stdout=log_stream,
            stderr=subprocess.STDOUT,
        )

    def watch():
        proc.wait()
        STATE["running"] = False
        STATE["exit_code"] = proc.returncode
        STATE["finished_at"] = time.time()

    threading.Thread(target=watch, daemon=True).start()
    return True, ts


def tail(path, n=4000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n * 80))
            return f.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def latest_report_dirs():
    root = ROOT / "reports"
    dirs = [p for p in root.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [d.name for d in dirs[:5]]


PAGE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>TradingAgents 控制台</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: -apple-system, "PingFang SC", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; }
  .wrap { max-width: 960px; margin: 0 auto; padding: 24px 20px 60px; }
  h1 { font-size: 22px; } h1 small { color:#888; font-weight:normal; font-size:13px; margin-left:8px; }
  .card { background:#171a21; border:1px solid #262b36; border-radius:10px; padding:16px; margin-top:16px; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  label { font-size:13px; color:#9aa4b2; display:block; margin-bottom:4px; }
  input { background:#0f1115; border:1px solid #2c3340; color:#e6e6e6; border-radius:6px; padding:8px 10px; font-size:14px; width:140px; }
  input#ticker { width:110px; text-transform:uppercase; }
  button { background:#3b82f6; border:none; color:#fff; border-radius:6px; padding:9px 18px; font-size:14px; cursor:pointer; }
  button:disabled { background:#2a3442; cursor:not-allowed; }
  #status { font-size:13px; color:#9aa4b2; margin-top:10px; }
  #status.running { color:#fbbf24; } #status.ok { color:#34d399; } #status.err { color:#f87171; }
  pre { background:#0b0d11; border:1px solid #262b36; border-radius:8px; padding:12px; font-size:12px; line-height:1.5;
        overflow:auto; max-height:420px; white-space:pre-wrap; word-break:break-all; }
  h2 { font-size:15px; color:#cbd5e1; } h2 small { color:#64748b; font-weight:normal; margin-left:6px; }
  .hint { font-size:12px; color:#64748b; margin-top:8px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>TradingAgents 控制台<small>网页版快速启动</small></h1>
  <div class="card">
    <div class="row">
      <div><label for="ticker">股票代码</label><input id="ticker" value="AAPL" placeholder="AAPL"></div>
      <div><label for="date">分析日期</label><input id="date" type="date"></div>
      <div style="align-self:flex-end"><button id="runBtn" onclick="startRun()">▶ 开始分析</button></div>
    </div>
    <div class="hint">美股 AAPL / SPY;港股 0700.HK;A股 600519.SS;加密 BTC-USD。运行约 3–10 分钟,模型走百炼网关(DeepSeek v4 flash 0731)。</div>
    <div id="status"></div>
  </div>
  <div class="card">
    <h2>实时日志<small id="logMeta"></small></h2>
    <pre id="log">等待启动…</pre>
  </div>
  <div class="card">
    <h2>决策结果<small>reports/webui_decision_&lt;ticker&gt;.json</small></h2>
    <pre id="result">尚无结果</pre>
  </div>
  <div class="card">
    <h2>最近报告目录</h2>
    <pre id="reports">加载中…</pre>
  </div>
</div>
<script>
const today = new Date(); today.setDate(today.getDate() - 1);
document.getElementById('date').value = today.toISOString().slice(0,10);
let poll = null;

async function startRun() {
  const ticker = document.getElementById('ticker').value.trim().toUpperCase();
  const date = document.getElementById('date').value;
  if (!ticker || !date) return;
  const r = await fetch('/api/run', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ticker, date})}).then(x=>x.json());
  const st = document.getElementById('status');
  st.className = r.ok ? 'running' : 'err';
  st.textContent = r.ok ? `⏳ 运行中:${ticker} @ ${date}(已开启 checkpoint 断点续跑)…` : r.error;
  document.getElementById('runBtn').disabled = true;
  document.getElementById('log').textContent = '启动中…';
  if (r.ok && !poll) poll = setInterval(refresh, 2000);
}

async function refresh() {
  const s = await fetch('/api/status').then(x=>x.json());
  document.getElementById('log').textContent = s.log || '…';
  document.getElementById('logMeta').textContent = s.log_file ? s.log_file.split('/').pop() : '';
  const st = document.getElementById('status');
  if (s.running) { st.className='running'; st.textContent = `⏳ 运行中:${s.ticker}(已 ${Math.round((Date.now()-s.started_at*1000)/1000)}s)`; }
  else if (s.exit_code === 0) { st.className='ok'; st.textContent = '✅ 完成'; clearInterval(poll); poll=null;
    document.getElementById('runBtn').disabled = false;
    document.getElementById('result').textContent = s.decision || '决策已保存到 reports/';
    document.getElementById('reports').textContent = s.reports.join('\\n'); }
  else if (s.exit_code !== null) { st.className='err'; st.textContent = '❌ 失败(exit '+s.exit_code+'),日志见上'; clearInterval(poll); poll=null;
    document.getElementById('runBtn').disabled = false; }
}
refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/status":
            decision = ""
            if STATE["ticker"]:
                p = ROOT / "reports" / f"webui_decision_{STATE['ticker']}.json"
                if p.exists():
                    try:
                        decision = json.dumps(
                            json.loads(p.read_text()), ensure_ascii=False, indent=2)
                    except Exception:
                        decision = ""
            self._send(200, json.dumps({
                "running": STATE["running"],
                "log": tail(STATE["log_file"]) if STATE["log_file"] else "",
                "log_file": STATE["log_file"],
                "ticker": STATE["ticker"],
                "started_at": STATE["started_at"],
                "exit_code": STATE["exit_code"],
                "decision": decision,
                "reports": latest_report_dirs(),
            }))
        else:
            self._send(404, "not found")

    def do_POST(self):
        if self.path == "/api/run":
            n = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(n))
                ticker = str(data.get("ticker", "")).strip()
                date = str(data.get("date", "")).strip()
            except Exception:
                self._send(400, json.dumps({"ok": False, "error": "bad request"}))
                return
            ok, msg = start_run(ticker, date)
            self._send(200, json.dumps({"ok": ok, "error": msg if not ok else ""}))
        else:
            self._send(404, "not found")


if __name__ == "__main__":
    port = 8756
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"TradingAgents 控制台: http://127.0.0.1:{port}")
    webbrowser.open(f"http://127.0.0.1:{port}")
    server.serve_forever()
