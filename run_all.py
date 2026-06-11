import subprocess
import sys
import os

BASE = os.path.dirname(os.path.abspath(__file__))
PY   = sys.executable

BOTS = [
    ('forex_v2.py',    'Forex 환율봇'),
    ('pendle_alarm.py','Pendle APY봇'),
]

procs = []
for script, label in BOTS:
    p = subprocess.Popen(
        [PY, '-u', os.path.join(BASE, script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        encoding='utf-8',
        errors='replace',
    )
    procs.append((p, label))
    print(f'[시작] {label} (PID {p.pid})')

print('[대기] 두 봇 모두 실행 중... Ctrl+C로 전체 종료\n')

import threading

def stream(proc, label):
    for line in proc.stdout:
        print(f'[{label}] {line}', end='')

threads = [threading.Thread(target=stream, args=(p, lbl), daemon=True) for p, lbl in procs]
for t in threads:
    t.start()

try:
    for p, _ in procs:
        p.wait()
except KeyboardInterrupt:
    print('\n[종료] 모든 봇을 종료합니다.')
    for p, _ in procs:
        p.terminate()
