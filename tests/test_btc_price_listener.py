import importlib.util
import json
from decimal import Decimal
from pathlib import Path

spec = importlib.util.spec_from_file_location('btc_listener', Path(__file__).resolve().parents[1] / 'scripts/btc_price_listener.py')
listener = importlib.util.module_from_spec(spec)
spec.loader.exec_module(listener)


def event(price='82000', **changes):
    value = {'e': 'trade', 's': 'BTCUSDT', 'p': price, 'T': 1000000}
    value.update(changes)
    return json.dumps(value)


def test_threshold_and_freshness():
    assert Decimal(listener.quote(event(), 1000)['price']) <= listener.THRESHOLD
    assert Decimal(listener.quote(event('82000.01'), 1000)['price']) > listener.THRESHOLD
    assert listener.quote(event(), 1031) is None
    assert listener.quote(event(T=1006000), 1000) is None


def test_wrong_symbol_and_invalid_data():
    for value in [event(s='ETHUSDT'), event(e='aggTrade'), event('NaN'), event('-1'), '{}', 'bad']:
        assert listener.quote(value, 1000) is None


def test_render_escapes_and_covers_roles(tmp_path):
    src = tmp_path / 'source/5_portfolio'
    src.mkdir(parents=True)
    (src / 'decision.md').write_text('**Rating**: Hold\n\n<script>alert(1)</script>')
    out = listener.render(tmp_path, {'trigger': {'price': '82000'}, 'date': '2026-09-22'})
    page = out.read_text()
    assert page.count('<tr>') == 13
    assert '<script>' not in page
    assert '&lt;script&gt;' in page


def test_durable_state(tmp_path, monkeypatch):
    monkeypatch.setattr(listener, 'STATE_DIR', tmp_path)
    listener.save({'status': 'triggered'})
    assert json.loads((tmp_path / 'state.json').read_text())['status'] == 'triggered'
    assert not (tmp_path / 'state.tmp').exists()


def test_notification_timeout_does_not_interrupt_analysis(monkeypatch):
    def timeout(*args, **kwargs):
        raise listener.subprocess.TimeoutExpired('osascript', 15)
    monkeypatch.setattr(listener.subprocess, 'run', timeout)
    listener.notify('test')


def test_cli_pty_end_to_end_without_model(tmp_path, monkeypatch):
    import sys
    cli = tmp_path / '.venv/bin/tradingagents'
    cli.parent.mkdir(parents=True)
    cli.write_text(f'#!{sys.executable}\n' + '''
from pathlib import Path
assert input('Enter ticker symbol:') == 'BTC-USD'
assert input('[2026-09-22]:') == '2026-09-22'
print('● Market Analyst\\n● Sentiment Analyst\\n● News Analyst', flush=True)
input()
print('Selected analysts: market, social, news', flush=True)
assert input('Save report? [Y]:') == 'y'
path = Path(input('Save path [default]:'))
path.mkdir(parents=True)
(path / 'complete_report.md').write_text('Test report')
assert input('Display full report on screen? [Y]:') == 'n'
''')
    cli.chmod(0o755)
    monkeypatch.setattr(listener, 'ROOT', tmp_path)
    listener.run_cli(tmp_path, '2026-09-22')
    assert (tmp_path / 'source/complete_report.md').read_text() == 'Test report'
