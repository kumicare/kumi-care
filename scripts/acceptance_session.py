"""One-command physical acceptance session: observer + bridge + report."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.acceptance_report import report


def main():
    os.chdir(ROOT)
    os.umask(0o077)
    if not (ROOT / '.local/observer.env').exists():
        print('Missing .local/observer.env; observer setup is required.')
        return 1
    env = os.environ.copy()
    for line in (ROOT / '.env').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            name, value = line.split('=', 1)
            parsed = shlex.split(value)
            env[name.strip()] = parsed[0] if parsed else ''
    directory = ROOT / '.local/acceptance' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True, mode=0o700)
    (ROOT / '.local/acceptance/latest').write_text(str(directory) + '\n')
    ready, capture = directory / 'ready', directory / 'events.jsonl'
    observer = subprocess.Popen([sys.executable, 'scripts/mqtt_observer.py', '--output', str(capture),
                                 '--ready-file', str(ready), '--seconds', '1800'], env=env,
                                start_new_session=True)
    bridge = None
    try:
        deadline = time.monotonic() + 25
        while not ready.exists():
            if observer.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Observer not ready; physical test not started')
            time.sleep(0.1)
        print('\n請先保持門關閉。輸入 BLE key 後，等 ble_scanning 出現再開始。', flush=True)
        print('做 10 次「開門 → 等至少 5 秒 → 關門 → 等至少 5 秒」。', flush=True)
        print('每一步應看到 received 與 mqtt_ack；完成後等 5 秒，再按 Ctrl+C 產生報告。\n', flush=True)
        bridge = subprocess.Popen([sys.executable, 'mqtt_bridge.py'], env=env)
        while bridge.poll() is None:
            if observer.poll() is not None:
                raise RuntimeError('Observer stopped; restart the acceptance session')
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if bridge and bridge.poll() is None:
            bridge.send_signal(signal.SIGINT)
            try:
                bridge.wait(timeout=20)
            except subprocess.TimeoutExpired:
                bridge.terminate()
                bridge.wait(timeout=5)
        # Allow in-flight subscriber callbacks to finish before creating report.
        time.sleep(1)
        if observer.poll() is None:
            observer.terminate()
        observer.wait(timeout=10)
    if not capture.exists():
        return 1
    result = report(capture, ROOT / '.local/bridge.log')
    if bridge is None or bridge.returncode != 0:
        result['failures'].append('bridge_did_not_exit_cleanly')
        result['status'] = 'FAIL'
    if observer.returncode != 0:
        result['failures'].append('observer_did_not_exit_cleanly')
        result['status'] = 'FAIL'
    (directory / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print('\n' + json.dumps(result, ensure_ascii=False, indent=2))
    print('Report: ' + str(directory / 'report.json'))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('Acceptance stopped: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
