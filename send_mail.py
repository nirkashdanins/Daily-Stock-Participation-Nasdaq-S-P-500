"""
שולח את הדשבורד היומי במייל: שורת סיכום + שתי תמונות (QQQ, SPY) + קישור לדף המלא.

משתני סביבה (GitHub Secrets):
  GMAIL_USER          כתובת ה-Gmail השולחת
  GMAIL_APP_PASSWORD  סיסמת אפליקציה של Gmail (16 תווים)
  MAIL_TO             נמען/ים, מופרדים בפסיק (ברירת מחדל: GMAIL_USER)
  PAGES_URL           קישור לדשבורד המלא (לא חובה)

  python send_mail.py            # שליחה
  python send_mail.py --dry-run  # בונה את המייל ושומר out/mail.eml בלי לשלוח
"""
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
M = "−"


def sg(v, d=0):
    s = f"{abs(v):.{d}f}"
    return ("+" if v > 0 else M if v < 0 else "") + s


def latest():
    files = sorted((ROOT / "docs" / "data").glob("????-??-??.json"))
    return json.loads(files[-1].read_text(encoding="utf-8"))


def line(k, D):
    return (f"{k}: {sg(D['total'])} נ״ב (מדד בפועל {sg(D['actual'], 2)}%) · "
            f"עלו/ירדו {D['up']}/{D['down']} · שבבים {sg(D['semis'])} נ״ב")


def build():
    R = latest()
    d = R["date"]
    dd = f"{d[8:10]}.{d[5:7]}.{d[2:4]}"
    q, s = R["QQQ"], R["SPY"]
    subject = f"מי הזיז את המדד · {dd} · QQQ {sg(q['total'])} · SPY {sg(s['total'])} נ״ב"
    url = os.environ.get("PAGES_URL", "")

    msg = EmailMessage()
    msg["Subject"] = subject
    user = os.environ.get("GMAIL_USER", "")
    msg["From"] = f"Index Agent <{user}>"
    msg["To"] = os.environ.get("MAIL_TO") or user

    text = "\n".join([f"סגירה {dd}", line("QQQ", q), line("SPY", s)] + ([f"דשבורד מלא: {url}"] if url else []))
    msg.set_content(text)

    cids = {k: make_msgid(domain="index-agent")[1:-1] for k in ("QQQ", "SPY")}
    li = lambda k, D: (f'<div style="margin:2px 0"><b>{k}</b>: '
                       f'<b style="color:{"#d93a26" if D["total"] < 0 else "#2e9e3a"}">{sg(D["total"])} נ״ב</b>'
                       f' (מדד בפועל {sg(D["actual"], 2)}%) · עלו/ירדו {D["up"]}/{D["down"]}'
                       f' · שבבים {sg(D["semis"])} נ״ב</div>')
    link = (f'<p style="margin:14px 0"><a href="{url}" style="color:#1a73e8">לדשבורד המלא באתר ←</a></p>'
            if url else "")
    html = f"""<!doctype html><html dir="rtl" lang="he"><body style="margin:0;padding:0;background:#f4f4f5">
<div dir="rtl" style="max-width:640px;margin:0 auto;padding:14px;font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#111;text-align:right">
<div style="font-size:20px;font-weight:bold;margin-bottom:6px">מי הזיז את המדד · סגירה {dd}</div>
{li("QQQ", q)}{li("SPY", s)}
{link}
<img src="cid:{cids['QQQ']}" alt="QQQ" style="width:100%;max-width:620px;display:block;margin:10px 0;border-radius:10px">
<img src="cid:{cids['SPY']}" alt="SPY" style="width:100%;max-width:620px;display:block;margin:10px 0;border-radius:10px">
{link}
</div></body></html>"""
    msg.add_alternative(html, subtype="html")
    part = msg.get_payload()[1]
    for k in ("QQQ", "SPY"):
        img = ROOT / "out" / f"{k}.png"
        part.add_related(img.read_bytes(), "image", "png", cid=f"<{cids[k]}>", filename=f"{k}_{d}.png")
    return msg


def main():
    msg = build()
    if "--dry-run" in sys.argv:
        (ROOT / "out").mkdir(exist_ok=True)
        (ROOT / "out" / "mail.eml").write_bytes(bytes(msg))
        print("נבנה out/mail.eml:", msg["Subject"])
        return
    user, pwd = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and pwd):
        print("::warning::לא הוגדרו GMAIL_USER / GMAIL_APP_PASSWORD ב-Secrets, המייל לא נשלח")
        return
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pwd.replace(" ", ""))
        s.send_message(msg)
    print("נשלח מייל:", msg["Subject"], "→", msg["To"])


if __name__ == "__main__":
    main()
