"""
סוכן "מי הזיז את המדד" — תרומת כל מניה לנאסד"ק 100 (QQQ) ול-S&P 500 (SPY)
ביום המסחר האחרון, בנקודות בסיס ובאחוזים, + כמות העולות/יורדות.

מקורות (רשמיים בלבד):
  • אחזקות QQQ  — Invesco (כמות מניות לכל אחזקה)
  • אחזקות SPY  — State Street / SSGA (קובץ xlsx יומי)
  • מחירי סגירה — Yahoo Finance (yfinance)

שיטה: משקל בפתיחת היום = כמות מניות × סגירה של היום הקודם / סך הקרן.
       תרומה (נ"ב) = משקל × תשואה יומית × 10,000.
       סכום כל התרומות = תשואת המדד (בדיקת איכות מול ^NDX / ^GSPC).

הרצה:
  python agent.py                       # יום המסחר האחרון שנסגר
  python agent.py --date 2026-10-22     # יום מסוים
  python agent.py --offline fixtures/   # בלי רשת, מקבצים שמורים (לבדיקות)
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
ET = ZoneInfo("America/New_York")
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "*/*",
}

INVESCO_URLS = [
    "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/holdings/fund?idType=ticker&productType=ETF",
    "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/holdings/fund?idType=ticker&interval=daily&productType=ETF",
]
SSGA_URL = ("https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/"
            "etfs/us/holdings-daily-us-en-spy.xlsx")
INDEX_SYMBOL = {"QQQ": "^NDX", "SPY": "^GSPC"}

# מניות שבבים וציוד שבבים (לכרטיס "שבבים"). לעדכן כשמניה נכנסת/יוצאת מהמדדים.
SEMIS = {
    "NVDA", "AVGO", "AMD", "MU", "INTC", "QCOM", "TXN", "ADI", "AMAT", "LRCX", "KLAC",
    "MRVL", "NXPI", "MCHP", "MPWR", "ON", "SWKS", "TER", "SMCI", "FSLR", "ASML", "ARM",
    "ALAB", "Q", "SNDK",
}


# ───────────────────────── שליפת אחזקות ─────────────────────────
def http_get(urls: list[str], referer: str, accept: str = "*/*") -> bytes:
    """GET עם טביעת דפדפן Chrome (curl_cffi), כמה כתובות חלופיות וניסיונות חוזרים."""
    import time
    headers = {"Referer": referer, "Origin": referer.rstrip("/"), "Accept": accept,
               "Accept-Language": "en-US,en;q=0.9"}
    errors = []
    for url in urls:
        for attempt in range(3):
            try:
                try:
                    from curl_cffi import requests as cr
                    r = cr.get(url, headers=headers, impersonate="chrome", timeout=60)
                except ImportError:
                    import requests
                    r = requests.get(url, headers={**UA, **headers}, timeout=60)
                if r.status_code == 200 and r.content:
                    return r.content
                errors.append(f"{url[:70]}… → HTTP {r.status_code}: {r.text[:120]!r}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url[:70]}… → {type(e).__name__}: {e}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("שליפה נכשלה:\n  " + "\n  ".join(errors[-6:]))


def fetch_qqq_holdings() -> list[tuple[str, float]]:
    raw = http_get(INVESCO_URLS, "https://www.invesco.com/", "application/json, text/plain, */*")
    j = json.loads(raw)
    keep = {"COM", "ADR", "DRNY", "REIT"}
    out = [(h["ticker"], float(h["units"])) for h in j["holdings"]
           if h.get("ticker") and h.get("securityTypeCode") in keep and h.get("units")]
    print(f"QQQ: {len(out)} אחזקות (Invesco, נכון ל-{j.get('effectiveDate')})")
    return out


def fetch_spy_holdings() -> list[tuple[str, float]]:
    import pandas as pd
    content = http_get([SSGA_URL], "https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy")
    raw = pd.read_excel(io.BytesIO(content), header=None)
    hdr = raw.index[raw.iloc[:, 1].astype(str).str.strip().eq("Ticker")][0]
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr]]
    df = df[pd.to_numeric(df["Shares Held"], errors="coerce").notna()]
    df = df[pd.to_numeric(df["Weight"], errors="coerce").fillna(0) > 0]
    df = df[df["Ticker"].astype(str).str.match(r"^[A-Z][A-Z.]*$")]
    asof = str(raw.iloc[2, 1]) if raw.shape[0] > 2 else "?"
    out = [(t.strip(), float(s)) for t, s in zip(df["Ticker"], df["Shares Held"])]
    print(f"SPY: {len(out)} אחזקות (SSGA, {asof})")
    return out


# ───────────────────────── מחירים ─────────────────────────
def yahoo(t: str) -> str:
    return t.replace(".", "-")


def fetch_prices(symbols: list[str]) -> dict[str, dict[str, float]]:
    """{symbol: {YYYY-MM-DD: close}} — 10 ימי מסחר אחרונים, סגירה לא מתואמת לדיבידנד."""
    import yfinance as yf
    ys = sorted({yahoo(s) for s in symbols})
    df = yf.download(ys, period="10d", interval="1d", auto_adjust=False,
                     group_by="ticker", threads=True, progress=False)
    out: dict[str, dict[str, float]] = {}
    for s in symbols:
        y = yahoo(s)
        try:
            col = df[y]["Close"].dropna()
        except KeyError:
            continue
        out[s] = {d.strftime("%Y-%m-%d"): float(v) for d, v in col.items()}
    missing = [s for s in symbols if s not in out or not out[s]]
    if missing:  # ניסיון חוזר בודד למה שלא חזר
        df2 = yf.download([yahoo(s) for s in missing], period="10d", interval="1d",
                          auto_adjust=False, group_by="ticker", progress=False)
        for s in missing:
            try:
                col = (df2[yahoo(s)] if len(missing) > 1 else df2)["Close"].dropna()
                if hasattr(col, "columns"):
                    col = col.iloc[:, 0]
                out[s] = {d.strftime("%Y-%m-%d"): float(v) for d, v in col.items()}
            except Exception:
                pass
    return out


def pick_day(px: dict, want: str | None) -> tuple[str, str]:
    """מחזיר (יום הקודם, יום היעד). מתעלם מנר של היום אם השוק עדיין פתוח."""
    days = sorted(px["SPY"].keys())
    if want:
        i = days.index(want)
        return days[i - 1], days[i]
    now = datetime.now(ET)
    if days[-1] == now.strftime("%Y-%m-%d") and (now.hour, now.minute) < (16, 20):
        days = days[:-1]
    return days[-2], days[-1]


# ───────────────────────── חישוב ─────────────────────────
def compute(holdings, px, d0, d1, index_sym):
    rows, skipped = [], []
    for t, units in holdings:
        p = px.get(t, {})
        if d0 not in p or d1 not in p:
            skipped.append(t)
            continue
        rows.append({"t": t.replace(".", ""), "mv": units * p[d0], "r": p[d1] / p[d0] - 1})
    tot_mv = sum(r["mv"] for r in rows)
    for r in rows:
        r["w"] = r["mv"] / tot_mv
        r["c"] = r["w"] * r["r"] * 1e4
    rows.sort(key=lambda r: r["c"])
    bot, top = rows[:10], rows[-10:][::-1]
    fmt = lambda r: [r["t"], round(r["c"], 1), round(r["r"] * 100, 2), round(r["w"] * 100, 2)]
    ix = px.get(index_sym, {})
    actual = round((ix[d1] / ix[d0] - 1) * 100, 2) if d0 in ix and d1 in ix else None
    semis = [r for r in rows if r["t"] in SEMIS]
    res = {
        "total": round(sum(r["c"] for r in rows), 1),
        "bottom": [fmt(r) for r in bot], "top": [fmt(r) for r in top],
        "bot_sum": round(sum(r["c"] for r in bot), 1), "top_sum": round(sum(r["c"] for r in top), 1),
        "up": sum(r["r"] > 0 for r in rows), "down": sum(r["r"] < 0 for r in rows),
        "flat": sum(r["r"] == 0 for r in rows), "n": len(rows),
        "semis": round(sum(r["c"] for r in semis), 1), "semis_n": len(semis),
        "actual": actual, "skipped": skipped,
        "all": [fmt(r) for r in rows],
    }
    return res


def quality(name, res):
    msg = f"{name}: סכום {res['total']:+.1f} נ\"ב | מדד בפועל {res['actual']}% | עלו/ירדו {res['up']}/{res['down']}"
    if res["skipped"]:
        msg += f" | חסרים מחירים: {', '.join(res['skipped'])}"
    print(msg)
    if res["actual"] is not None and abs(res["total"] / 100 - res["actual"]) > 0.15:
        print(f"  ⚠ אזהרה: פער של יותר מ-15 נ\"ב בין הסכום למדד ב-{name}")


# ───────────────────────── פלט ─────────────────────────
def render(result: dict) -> str:
    tpl = (ROOT / "template.html").read_text(encoding="utf-8")
    slim = {"date": result["date"],
            **{k: {kk: vv for kk, vv in v.items() if kk not in ("all", "skipped")}
               for k, v in result.items() if k in ("QQQ", "SPY")}}
    body = tpl.replace("/*DATA*/null", json.dumps(slim, ensure_ascii=False))
    return '<!doctype html>\n<html lang="he">\n' + body + "\n</html>\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD; ברירת מחדל: יום המסחר האחרון שנסגר")
    ap.add_argument("--offline", help="תיקייה עם prices.json, qqq_holdings.json, spy_holdings.json")
    a = ap.parse_args()

    if a.offline:
        f = Path(a.offline)
        qqq = json.loads((f / "qqq_holdings.json").read_text())
        spy = json.loads((f / "spy_holdings.json").read_text())
        px = json.loads((f / "prices.json").read_text())
    else:
        qqq, spy = fetch_qqq_holdings(), fetch_spy_holdings()
        syms = sorted({t for t, _ in qqq} | {t for t, _ in spy} | {"SPY", "QQQ", "^NDX", "^GSPC"})
        px = fetch_prices(syms)

    d0, d1 = pick_day(px, a.date)
    print(f"יום יעד: {d1} (מול סגירה של {d0})")
    result = {"date": d1, "prev": d0,
              "QQQ": compute(qqq, px, d0, d1, "^NDX"),
              "SPY": compute(spy, px, d0, d1, "^GSPC")}
    for k in ("QQQ", "SPY"):
        quality(k, result[k])

    (DOCS / "data").mkdir(parents=True, exist_ok=True)
    (DOCS / "data" / f"{d1}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    html = render(result)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    (DOCS / f"{d1}.html").write_text(html, encoding="utf-8")
    print(f"נכתב: docs/index.html, docs/{d1}.html, docs/data/{d1}.json")


if __name__ == "__main__":
    sys.exit(main())
