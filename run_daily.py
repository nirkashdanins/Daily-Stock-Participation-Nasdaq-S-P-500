"""
ההרצה היומית האוטומטית (נקראת מ-GitHub Actions).

לוח זמנים (שעון ישראל, ג'–שבת, אחרי סגירת המסחר בארה"ב של יום ב'–ו'):
  00:50  בודק מוכנות, מחשב ושומר בשקט (staging/latest.json)
  01:20  מחשב (או משתמש בשמירה של 00:50), ממתין עד 07:00, מפרסם לאתר ושולח מייל
  04:10  גיבוי: אם כבר נמסר — יוצא; אחרת ממתין ל-07:00 ומוסר

חלון שקט: 23:45–07:00 שעון ישראל — אין עדכון אתר ואין מייל.
GitHub מריץ את ההרצות בזו אחר זו (concurrency), ו-state/delivered.txt מונע מסירה כפולה.

  python run_daily.py              # מצב אוטומטי (schedule)
  python run_daily.py --now        # הרצה ידנית: מוסר מיד, בלי חלון שקט
  python run_daily.py --now --date 2026-10-30 --no-email
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import agent

ROOT = Path(__file__).resolve().parent
IL = ZoneInfo("Asia/Jerusalem")
STATE = ROOT / "state" / "delivered.txt"
STAGE = ROOT / "staging" / "latest.json"

QUIET_START = (23, 45)          # מ-23:45
QUIET_END = (7, 0)              # עד 07:00
JOB_LIMIT_MIN = 350             # מגבלת GitHub היא 360 דק' לג'וב; משאירים מרווח
DELIVERY_MARGIN_MIN = 8         # זמן לפרסום + צילום + מייל אחרי ההמתנה

START = time.monotonic()


def log(kind: str, msg: str) -> None:
    """kind: notice / warning / error — מופיע גם בעמוד ההרצה ב-GitHub."""
    print(msg, flush=True)
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{kind}::{msg}", flush=True)


# ───────────────────────── git ─────────────────────────
def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, check=check, text=True, capture_output=True)


def commit_push(paths: list[str], msg: str) -> bool:
    git("add", *paths)
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    git("commit", "-m", msg)
    for _ in range(4):
        if git("push", check=False).returncode == 0:
            return True
        git("pull", "--rebase", "-q")
    raise RuntimeError("git push נכשל אחרי 4 ניסיונות")


def last_delivered() -> str:
    git("pull", "--rebase", "-q", check=False)
    return STATE.read_text().strip() if STATE.exists() else ""


# ───────────────────────── זמן ─────────────────────────
def il_now() -> datetime:
    return datetime.now(IL)


def in_quiet(t: datetime) -> bool:
    hm = (t.hour, t.minute)
    return hm >= QUIET_START or hm < QUIET_END


def next_morning(t: datetime) -> datetime:
    m = t.replace(hour=QUIET_END[0], minute=QUIET_END[1], second=0, microsecond=0)
    return m if m > t else m + timedelta(days=1)


def minutes_left_in_job() -> float:
    return JOB_LIMIT_MIN - (time.monotonic() - START) / 60


# ───────────────────────── שלבים ─────────────────────────
def stage(result: dict) -> None:
    STAGE.parent.mkdir(exist_ok=True)
    STAGE.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    if os.environ.get("GITHUB_ACTIONS"):
        commit_push(["staging"], f"staging {result['date']}")


def deliver(result: dict, email: bool, mark: bool = True) -> None:
    d = result["date"]
    agent.write_docs(result, update_index=mark)
    paths = ["docs"]
    if mark:
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(d + "\n")
        paths.append("state")
    if os.environ.get("GITHUB_ACTIONS"):
        commit_push(paths, f"dashboard {d}")
    subprocess.run([sys.executable, "snapshot.py"], cwd=ROOT, check=True)
    if email:
        subprocess.run([sys.executable, "send_mail.py"], cwd=ROOT, check=True)
    log("notice", f"נמסר {d}: האתר עודכן" + (" ונשלח מייל" if email else ""))


def get_result(date: str | None, offline: str | None) -> dict | None:
    """מחשב טרי; אם המקורות נכשלים — חוזר לשמירה מהלילה (אם היא של יום חדש)."""
    try:
        r = agent.build(date, offline)
        stage(r)
        return r
    except agent.NotReady as e:
        log("notice", f"הנתונים עוד לא מוכנים: {e}")
    except Exception as e:  # noqa: BLE001
        log("warning", f"שליפה/חישוב נכשלו: {type(e).__name__}: {e}")
    if STAGE.exists() and not date:
        r = json.loads(STAGE.read_text(encoding="utf-8"))
        log("notice", f"משתמש בשמירה מהלילה ({r['date']})")
        return r
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="הרצה ידנית: מוסר מיד, בלי חלון שקט")
    ap.add_argument("--date")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--offline")
    a = ap.parse_args()
    email = not a.no_email

    if a.now:
        r = agent.build(a.date, a.offline)
        deliver(r, email, mark=r["date"] >= last_delivered())
        return 0

    done = last_delivered()
    r = get_result(None, a.offline)
    if r is None:
        log("notice", "אין מה למסור כרגע; ההרצה הבאה תנסה שוב")
        return 0
    if r["date"] <= done:
        log("notice", f"{r['date']} כבר נמסר (או שאין יום מסחר חדש) — אין מה לעשות")
        return 0

    now = il_now()
    if not in_quiet(now):
        deliver(r, email)
        return 0

    wake = next_morning(now)
    wait_min = (wake - now).total_seconds() / 60
    if wait_min > minutes_left_in_job() - DELIVERY_MARGIN_MIN:
        log("notice", f"{r['date']} מוכן ונשמר. 07:00 רחוק מדי להמתנה בהרצה הזו "
                      f"({wait_min:.0f} דק'); הרצה מאוחרת יותר תמסור")
        return 0

    log("notice", f"{r['date']} מוכן. ממתין בשקט עד {wake:%H:%M} שעון ישראל ({wait_min:.0f} דק')")
    while (left := (wake - il_now()).total_seconds()) > 0:
        time.sleep(min(left, 1800))
        print(f"  … עוד {max(left - 1800, 0) / 60:.0f} דק'", flush=True)

    if last_delivered() >= r["date"]:
        log("notice", f"{r['date']} נמסר בינתיים בהרצה אחרת")
        return 0
    if STAGE.exists():  # אולי הרצה אחרת שמרה בינתיים גרסה טרייה יותר
        s = json.loads(STAGE.read_text(encoding="utf-8"))
        if s["date"] == r["date"]:
            r = s
    deliver(r, email)
    return 0


if __name__ == "__main__":
    sys.exit(main())
