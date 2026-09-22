"""One-shot BTC/USDT price listener. No trading credentials or orders."""
import argparse
import asyncio
import fcntl
import html
import json
import re
import ssl
import subprocess
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / 'reports' / 'btc-82000-listener'
STREAM = 'wss://data-stream.binance.vision/ws/btcusdt@trade'
THRESHOLD = Decimal('82000')


def quote(message, now=None):
    """Reject stale, wrong-market and malformed events before comparing price."""
    now = time.time() if now is None else now
    try:
        event = json.loads(message)
        price = Decimal(event['p'])
        stamp = int(event['T']) / 1000
        if (event.get('e') != 'trade' or event.get('s') != 'BTCUSDT'
                or not price.is_finite() or price <= 0 or not -5 <= now - stamp <= 30):
            return None
        return {'price': str(price), 'trade_time': stamp, 'received_at': now,
                'source': STREAM}
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


def save(state):
    temp = STATE_DIR / 'state.tmp'
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temp.replace(STATE_DIR / 'state.json')


def notify(message):
    try:
        subprocess.run(['/usr/bin/osascript', '-e',
                        'on run argv\ndisplay notification (item 1 of argv) with title "BTC 价格监听"\nend run',
                        message], check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print('Notification unavailable:', type(exc).__name__, flush=True)


def run_cli(folder, date):
    """Drive the installed interactive CLI through a real PTY."""
    import pexpect
    child = pexpect.spawn(str(ROOT / '.venv/bin/tradingagents'), ['--checkpoint'],
                          cwd=str(ROOT), encoding='utf-8', codec_errors='replace',
                          timeout=120, dimensions=(50, 140))
    try:
        with (folder / 'cli.log').open('a') as log:
            child.logfile_read = log
            child.expect('Enter ticker symbol.*:')
            child.sendline('BTC-USD')
            child.expect(r'\[\d{4}-\d{2}-\d{2}\]:')
            child.sendline(date)
            child.expect('News Analyst')
            # Initial checkbox frame ends at the third crypto analyst.
            if child.before.count('●') != 3:
                child.send('a')
            child.sendline('')
            child.expect('Selected analysts: market, social, news')
            child.expect(r'Save report\? \[Y\]:', timeout=7200)
            child.sendline('y')
            child.expect(r'Save path.*\]:')
            child.sendline(str(folder / 'source'))
            child.expect(r'Display full report on screen\? \[Y\]:')
            child.sendline('n')
            child.expect(pexpect.EOF)
        child.close()
        if child.exitstatus != 0 or not (folder / 'source/complete_report.md').exists():
            raise RuntimeError('CLI did not complete and save its report')
    finally:
        if child.isalive():
            child.terminate(force=True)


def render(folder, state):
    roles = [('技术面','1_analysts/market.md'), ('情绪','1_analysts/sentiment.md'),
             ('新闻','1_analysts/news.md'), ('公司基本面',None),
             ('看多研究员','2_research/bull.md'), ('看空研究员','2_research/bear.md'),
             ('研究经理','2_research/manager.md'), ('Trader','3_trading/trader.md'),
             ('激进风险','4_risk/aggressive.md'), ('保守风险','4_risk/conservative.md'),
             ('中性风险','4_risk/neutral.md'), ('Portfolio Manager','5_portfolio/decision.md')]
    rows = []
    for name, path in roles:
        if path is None:
            excerpt = '加密资产不适用公司财报分析；已跳过。'
        else:
            file = folder / 'source' / path
            if not file.exists():
                excerpt = '本角色未生成报告，请查看原始运行记录。'
            else:
                text = file.read_text()
                blocks = [b.strip() for b in text.split('\n\n') if b.strip() and not b.startswith('#')]
                # Literal source excerpts only; no generated investment recommendation.
                excerpt = '\n'.join(blocks[:2])[:500]
                if len('\n'.join(blocks[:2])) > 500:
                    excerpt += '…（原文续见链接）'
                excerpt = re.sub(r'[*`]', '', excerpt)
        label = html.escape(name)
        if path:
            label = f'<a href="source/{path}">{label}</a>'
        rows.append(f'<tr><th>{label}</th><td>{html.escape(excerpt)}</td></tr>')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BTC 到价研究</title><style>body{font:16px/1.7 system-ui;background:#f3f5f3;color:#20342d;margin:0}main{max-width:1000px;margin:auto;padding:30px}table{width:100%;border-collapse:collapse;background:white}th,td{padding:16px;text-align:left;border-bottom:1px solid #ddd;vertical-align:top}th{width:150px}td{white-space:pre-wrap}tr:last-child{background:#e0efe6}a{color:#176249}.notice{background:#fff0d4;padding:15px}@media(max-width:600px){main{padding:12px}th{width:100px}}</style><main>'''
    page += '<h1>BTC 到价研究</h1><p>触发价 ' + html.escape(state['trigger']['price']) + ' USDT · ' + html.escape(state['date']) + '</p>'
    page += '<p>下表为各角色原文摘录；点击角色可追溯完整结论。未执行交易。</p><table><thead><tr><th>角色</th><th>BTC-USD</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
    page += '<p class="notice">报告尚需复核：盘中日线不等于收盘；模型假设的仓位不代表你的仓位；缺资金费率、持仓量或源数据时不能直接照订单字段执行。详细缺口以原报告为准。</p><a href="source/complete_report.md">完整研究报告</a></main></html>'
    output = folder / 'report.html'
    output.write_text(page)
    return output


async def watch(probe=False):
    import truststore
    from websockets.asyncio.client import connect
    tls = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    state = {'status': 'listening', 'threshold': str(THRESHOLD)}
    delay = 1
    last_save = 0
    while True:
        try:
            async with connect(STREAM, ssl=tls, open_timeout=20, ping_interval=20, ping_timeout=20) as ws:
                delay = 1
                while True:
                    message = await asyncio.wait_for(ws.recv(), timeout=45)
                    tick = quote(message)
                    if tick is None:
                        continue
                    if probe:
                        print(json.dumps(tick), flush=True)
                        return
                    state.update(status='listening', last_quote=tick)
                    if tick['received_at'] - last_save > 30:
                        save(state)
                        last_save = tick['received_at']
                    if Decimal(tick['price']) <= THRESHOLD:
                        now = datetime.now(ZoneInfo('Asia/Shanghai'))
                        folder = STATE_DIR / now.strftime('trigger-%Y%m%d-%H%M%S')
                        folder.mkdir()
                        state.update(status='triggered', trigger=tick, date=now.date().isoformat(), folder=str(folder))
                        save(state)  # Durable one-shot latch before launching analysis.
                        notify('BTC 已到 ' + tick['price'] + ' USDT，开始 TradingAgents 研究。')
                        try:
                            run_cli(folder, state['date'])
                            output = render(folder, state)
                            state.update(status='completed', html=str(output))
                            save(state)
                            notify('BTC 分析完成，报告已保存并打开。')
                            subprocess.run(['/usr/bin/open', str(output)], check=False)
                        except Exception as exc:
                            state.update(status='failed', error=type(exc).__name__ + ': ' + str(exc)[-1000:])
                            save(state)
                            notify('BTC 分析失败，请查看监听目录 state.json 和 cli.log。')
                        return
        except Exception as exc:
            if probe:
                raise
            if 'trigger' in state:
                # Never reconnect and trigger again after committing the latch.
                raise
            if state.get('status') != 'reconnecting':
                notify('行情连接中断，正在重连；断线期间可能漏过瞬间触价。')
            state.update(status='reconnecting', error=type(exc).__name__, retry_seconds=delay)
            save(state)
            print('Connection lost; retry in', delay, type(exc).__name__, flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', action='store_true', help='Read one live tick; never trigger or write state')
    args = parser.parse_args()
    if args.probe:
        asyncio.run(watch(True))
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / 'listener.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        state_file = STATE_DIR / 'state.json'
        if state_file.exists():
            prior = json.loads(state_file.read_text())
            if prior.get('status') in {'triggered', 'completed', 'failed'}:
                if prior.get('status') == 'triggered':
                    prior.update(status='failed', error='Listener interrupted after trigger; inspect checkpoint before manually resuming')
                    save(prior)
                    notify('到价研究曾被中断，需检查断点后恢复，已阻止重复启动。')
                print('Already triggered; no duplicate analysis.', flush=True)
                return
        asyncio.run(watch())


if __name__ == '__main__':
    main()
