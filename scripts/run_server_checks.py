"""Run all server tests with a fresh private temporary data directory.

Legacy preflight modules generate reports during discovery. Never let them use
WORKSHIFTS_DATA from a real deployment. This runner always replaces that setting
before importing the application in a separate Python process.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix='workshift-server-tests-') as folder:
        env = os.environ.copy()
        env['WORKSHIFTS_DATA'] = folder
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        # Tests use their own fictional authorization, not deployment secrets.
        for key in list(env):
            if key.startswith(('TELEGRAM_', 'MINIAPP_')):
                env.pop(key)
        print('ISOLATED_SERVER_TESTS: fresh temporary data, no production path', flush=True)
        return subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-q'],
                              cwd=root, env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
