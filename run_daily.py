"""
ההרצה היומית האוטומטית (נקראת מ-GitHub Actions).

למה לא שעה קבועה: GitHub מעכב הרצות מתוזמנות ב-4–8 שעות. לכן הסוכן "מתעורר" כבר בערב,
ממתין בעצמו לסגירה בארה"ב, ומחזיק את עצמו בחיים (מפעיל ריצת המשך לפני מגבלת 6 השעות של GitHub)
עד שהדוח נמסר. הפעלות ידניות (workflow_dispatch) לא מתעכבות כמו הרצות מתוזמנות.

כללי מסירה (שעון ישראל):
  • האתר:  מתעדכן ברגע שהנתונים מוכנים, חוץ מ-23:45–05:00 (אז ממתין ל-05:00)
  • המייל: נשלח ברגע שהנתונים מוכנים, חוץ מ-23:45–07:00 (אז ממתין ל-07:00)
  • בימים בלי מסחר האתר נשאר עם הדוח של יום המסחר האחרון.

מצב נשמר ב-state/delivered.txt (אתר) ו-state/emailed.txt (מייל) — אין כפילויות.

  python run_daily.py              # מצב אוטומטי (schedule / ריצת המשך)
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
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import agent

ROOT = Path(__file__).resolve().parent
IL = ZoneInfo("Asia/Jerusalem")
ET = ZoneInfo("America/New_York")
SITE_STATE = ROOT / "state" / "delivered.txt"
MAIL_STATE = ROOT / "state" / "emailed.txt"
STAGE = ROOT / "staging" / "latest.json"

QUIET_START = (23, 45)     # מ-23:45 שקט
SITE_FROM = (5, 0)         # האתר מתעדכן החל מ-05:00
EMAIL_FROM = (7, 0)        # המייל נשלח החל מ-07:00
READY_AFTER_ET = (16, 30)  # מחירי סגירה נחשבים סופיים מ-16:30 שעון ניו יורק
GIVE_UP_HOURS = 9.5        # אם אין נתונים עד 9.5 שעות אחרי הסגירה (~08:30 בישראל) — יום ללא מסחר
POLL_MIN = 10              # בדיקה חוזרת כל 10 דק' בשעתיים הראשונות אחרי הסגירה
POLL_SLOW_MIN = 30         # ואחר כך כל 30 דק'
EARLY_EXIT_H = 7           # אם הכול נמסר והסגירה הבאה בעוד 7+ שעות — יוצאים (הרצת הערב תטפל)
RELAY_AFTER_MIN = 320      # לפני מגבלת 360 הדק' של GitHub: מפעילים ריצת המשך ויוצאים

START = time.monotonic()
GH = bool(os.environ.get("GITHUB_ACTIONS"))


def log(kind: str, msg: str) -> None:
    print(msg, flush=True)
    if GH:
        print(f"::{kind}::{msg}", flush=True)


def il_now() -> datetime:
    return datetime.now(IL)


def elapsed_min() -> float:
    return (time.monotonic() - START) / 60


# ───────────────────────── git ─────────────────────────
def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, check=check, text=True, capture_output=True)


def sync() -> None:
    if GH:
        git("pull", "--rebase", "-q", check=False)


def commit_push(paths: list[str], msg: str) -> None:
    if not GH:
        return
    git("add", *paths)
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return
    git("commit", "-m", msg)
    for _ in range(5):
        if git("push", check=False).returncode == 0:
            return
        git("pull", "--rebase", "-q", check=False)
        time.sleep(3)
    raise RuntimeError("git push נכשל")


def read_state(p: Path) -> str:
    return p.read_text().strip() if p.exists() else ""


def write_state(p: Path, d: str) -> None:
    p.parent.mkdir(exist_ok=True)
    p.write_text(d + "\n")


# ───────────────────────── זמנים ─────────────────────────
def in_window(t: datetime, start: tuple[int, int]) -> bool:
    """True אם מותר לפעול עכשיו: מ-start ועד 23:45."""
    hm = (t.hour, t.minute)
    return start <= hm < QUIET_START


def next_open(t: datetime, start: tuple[int, int]) -> datetime:
    if in_window(t, start):
        return t
    o = t.replace(hour=start[0], minute=start[1], second=0, microsecond=0)
    return o if o > t else o + timedelta(days=1)


def expected_day(now_il: datetime) -> date:
    """יום המסחר שהסוכן מחכה לו: היום בניו יורק (אם יום חול), אחרת יום ו' האחרון."""
    d = now_il.astimezone(ET).date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def prev_weekday(d: str) -> str:
    x = date.fromisoformat(d) - timedelta(days=1)
    while x.weekday() >= 5:
        x -= timedelta(days=1)
    return x.isoformat()


def last_trading_before(d: str) -> str:
    days = [x for x in agent.fetch_chart("^GSPC") if x < d]
    return max(days) if days else prev_weekday(d)


def index_has(d: str) -> bool:
    """בדיקה זולה: האם כבר יש סגירה של שני המדדים ליום d."""
    return all(d in agent.fetch_chart(ix) for ix in ("^GSPC", "^NDX"))


def ready_time(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, *READY_AFTER_ET, tzinfo=ET).astimezone(IL)


# ───────────────────────── מסירה ─────────────────────────
def publish_site(r: dict, mark: bool = True) -> None:
    agent.write_docs(r, update_index=mark)
    paths = ["docs"]
    if mark:
        write_state(SITE_STATE, r["date"])
        paths.append("state")
    commit_push(paths, f"dashboard {r['date']}")
    log("notice", f"האתר עודכן: {r['date']}")


def send_email(r: dict, mark: bool = True) -> None:
    agent.write_docs(r, update_index=False)  # docs/<date>.html + נתונים, לצילום
    subprocess.run([sys.executable, "snapshot.py", "--date", r["date"]], cwd=ROOT, check=True)
    for i in range(3):
        p = subprocess.run([sys.executable, "send_mail.py", "--date", r["date"]], cwd=ROOT)
        if p.returncode == 0:
            break
        time.sleep(30 * (i + 1))
    else:
        raise RuntimeError("שליחת המייל נכשלה 3 פעמים")
    if mark:
        write_state(MAIL_STATE, r["date"])
        commit_push(["state"], f"emailed {r['date']}")


def stage(r: dict) -> None:
    STAGE.parent.mkdir(exist_ok=True)
    STAGE.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    commit_push(["staging"], f"staging {r['date']}")


def staged() -> dict | None:
    return json.loads(STAGE.read_text(encoding="utf-8")) if STAGE.exists() else None


def relay() -> None:
    """מפעיל ריצת המשך (workflow_dispatch לא מתעכב כמו schedule) ויוצא."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not (GH and repo):
        log("notice", "(מקומי) כאן הייתה מופעלת ריצת המשך")
        return
    subprocess.run(["gh", "api", "-X", "POST",
                    f"repos/{repo}/actions/workflows/daily.yml/dispatches",
                    "-f", "ref=main", "-f", "inputs[mode]=auto"], check=True)
    log("notice", "הופעלה ריצת המשך")


# ───────────────────────── לולאה ─────────────────────────
def auto(email: bool, offline: str | None = None) -> int:
    while True:
        sync()
        now = il_now()
        exp = expected_day(now).isoformat()
        site_done, mail_done = read_state(SITE_STATE), read_state(MAIL_STATE)
        if not email:
            mail_done = max(mail_done, exp)

        if site_done >= exp and mail_done >= exp:
            log("notice", f"{exp} כבר נמסר (אתר + מייל). אין מה לעשות")
            return 0
        ready = ready_time(date.fromisoformat(exp))
        prev = prev_weekday(exp)
        if now < ready - timedelta(hours=EARLY_EXIT_H):
            caught = site_done >= prev and mail_done >= prev
            if not caught and not offline:   # אולי היום הקודם היה חג — בודקים מה יום המסחר האחרון בפועל
                lt = last_trading_before(exp)
                caught = site_done >= lt and mail_done >= lt
            if caught:
                log("notice", f"הכול נמסר עד יום המסחר האחרון. הסגירה הבאה ({exp}) רחוקה — הרצת הערב תטפל בה")
                return 0

        # 1) לחשב אם חסר: אחרי הסגירה — את היום; לפני הסגירה — רק אם חסר יום קודם
        r = staged()
        need = (r is None or r["date"] < exp) and (now >= ready or r is None or r["date"] < prev)
        if need and now < ready + timedelta(hours=GIVE_UP_HOURS):
            try:
                if now >= ready and not offline and not index_has(exp):
                    raise agent.NotReady(f"אין עדיין סגירה של המדדים ל-{exp}")
                fresh = agent.build(None, offline)
                if not r or fresh["date"] >= r["date"]:
                    r = fresh
                    stage(r)
            except agent.NotReady as e:
                log("notice", f"הנתונים עוד לא מוכנים: {e}")
            except Exception as e:  # noqa: BLE001
                log("warning", f"שליפה/חישוב נכשלו: {type(e).__name__}: {e}")

        # 2) מסירה לפי החלונות
        if r and r["date"] > site_done and in_window(now, SITE_FROM):
            publish_site(r)
            site_done = r["date"]
        delivered = False
        if r and email and r["date"] > mail_done and site_done >= r["date"] and in_window(now, EMAIL_FROM):
            send_email(r)
            mail_done = r["date"]
            delivered = True
        if site_done >= exp and mail_done >= exp:
            log("notice", f"הושלם: {exp}")
            return 0
        if delivered:
            continue  # לבדוק מחדש מה נשאר (בד"כ: כלום עד הערב)

        # 3) לחכות — למה?
        have_new = bool(r) and (r["date"] > site_done or (email and r["date"] > mail_done))
        has_today = bool(r) and r["date"] >= exp
        gave_up = now >= ready + timedelta(hours=GIVE_UP_HOURS)
        if not has_today and gave_up and not have_new:
            log("notice", f"אין נתוני מסחר ל-{exp} (חג/יום ללא מסחר?). האתר נשאר עם {site_done}")
            return 0
        targets = []
        if not has_today and not gave_up:
            step = POLL_MIN if now < ready + timedelta(hours=2) else POLL_SLOW_MIN
            targets.append(max(now + timedelta(minutes=step), ready))
        if have_new:
            if r["date"] > site_done:
                targets.append(next_open(now, SITE_FROM))
            elif email and r["date"] > mail_done:
                targets.append(next_open(now, EMAIL_FROM))
        wake = min(targets) if targets else now + timedelta(minutes=POLL_MIN)
        wait = max((wake - now).total_seconds() / 60, 0.5)

        if elapsed_min() + wait > RELAY_AFTER_MIN:
            # להמתין כמה שאפשר בריצה הזו, ואז ריצת המשך
            spare = RELAY_AFTER_MIN - elapsed_min()
            if spare > 1:
                print(f"ממתין {spare:.0f} דק' ואז ריצת המשך", flush=True)
                time.sleep(spare * 60)
            relay()
            return 0
        print(f"[{now:%H:%M}] ממתין עד {wake:%H:%M} ({wait:.0f} דק')", flush=True)
        time.sleep(wait * 60)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="הרצה ידנית: מוסר מיד, בלי חלון שקט")
    ap.add_argument("--date")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--offline")
    a = ap.parse_args()
    email = not a.no_email

    if a.now:
        sync()
        r = agent.build(a.date, a.offline)
        newest = r["date"] >= read_state(SITE_STATE)
        publish_site(r, mark=newest)
        if email:
            send_email(r, mark=r["date"] >= read_state(MAIL_STATE))
        return 0
    return auto(email, a.offline)


if __name__ == "__main__":
    sys.exit(main())
