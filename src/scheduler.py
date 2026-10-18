# Ежедневное обновление данных

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Almaty"))
UPDATE_AT = os.getenv("UPDATE_AT", "03:00")
STEPS = [
    ("загрузка изменений из OWS", "src/ingest.py", False),
    ("витрины", "src/transform.py", True),
    ("справедливая цена", "src/fair_price.py", True),
]


def log(message):
    print(f"[{datetime.now(TZ):%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def run_pipeline():
    """Ошибка загрузки по части БИН не останавливает пересчёт: витрины строятся по тому, что загружено.
    Ошибка в витринах останавливает: справедливая цена без них не считается."""
    started = time.monotonic()
    ok = True
    for title, script, required in STEPS:
        log(f"шаг: {title}")
        code = subprocess.run([sys.executable, script], cwd=ROOT).returncode
        if code != 0:
            ok = False
            log(f"шаг «{title}» завершился с ошибкой (код {code})")
            if required:
                break
    log(f"обновление {'завершено' if ok else 'завершено с ошибками'} за {(time.monotonic() - started) / 60:.1f} мин")
    return ok


def seconds_until_next_run(now=None):
    now = now or datetime.now(TZ)
    hour, minute = map(int, UPDATE_AT.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds(), target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="обновить один раз и выйти")
    args = parser.parse_args()

    if args.once:
        sys.exit(0 if run_pipeline() else 1)
    run_pipeline()
    while True:
        wait, target = seconds_until_next_run()
        log(f"следующее обновление: {target:%Y-%m-%d %H:%M}")
        time.sleep(wait)
        run_pipeline()


if __name__ == "__main__":
    main()
