# מי הזיז את המדד — סוכן יומי

כל בוקר לפני פתיחת המסחר בארה"ב, הסוכן מחשב לנאסד"ק 100 (QQQ) ול-S&P 500 (SPY):
- תרומת כל מניה למדד, בנקודות בסיס ובאחוזים, ואת משקלה במדד
- 10 התומכות ו-10 המושכות למטה
- כמות ושיעור המניות שעלו וירדו
- תרומת השבבים
- בדיקת איכות: סכום התרומות מול תשואת המדד בפועל

הפלט: דשבורד `docs/index.html` (+ ארכיון `docs/YYYY-MM-DD.html` ונתונים גולמיים `docs/data/YYYY-MM-DD.json`).

## מקורות
| נתון | מקור |
|---|---|
| אחזקות QQQ (כמות מניות) | Invesco |
| אחזקות SPY (כמות מניות) | State Street (SSGA), קובץ xlsx יומי |
| מחירי סגירה | Yahoo Finance |

משקל בפתיחת היום = כמות מניות × סגירה של אתמול. תרומה = משקל × תשואה × 10,000.

## הקמה ב-GitHub (פעם אחת)
1. צור Repository חדש (למשל `index-agent`), ציבורי או פרטי.
2. העלה את כל הקבצים מהתיקייה הזו (כולל התיקייה `.github`).
3. **Settings → Actions → General → Workflow permissions** → סמן *Read and write permissions* → Save.
4. **Actions** → `daily-index-contribution` → **Run workflow** (הרצה ידנית ראשונה; אפשר להזין תאריך).
5. לצפייה בטלפון: **Settings → Pages** → Source: *Deploy from a branch* → Branch `main`, תיקייה `/docs` → Save.
   הדשבורד יהיה בכתובת `https://<user>.github.io/<repo>/`.
   (Pages ב-Repository פרטי דורש מנוי GitHub בתשלום. בלי זה, אפשר להוריד את `dashboard` מכל הרצה תחת Actions.)

## תזמון
ימים ב'–ו' ב-04:17 UTC (‎07:17 שעון ישראל בקיץ, 06:17 בחורף). לשינוי: השורה `cron` ב-`.github/workflows/daily.yml`.
GitHub עלול לעכב הרצות מתוזמנות בכמה דקות עד שעה.

## הרצה מקומית
```bash
pip install -r requirements.txt
python agent.py                      # יום המסחר האחרון
python agent.py --date 2026-10-22    # יום מסוים
python agent.py --offline fixtures/2026-10-22 --date 2026-10-22   # בדיקה בלי רשת
```

## תחזוקה
- רשימת מניות השבבים: `SEMIS` ב-`agent.py`.
- אם בלוג מופיעה אזהרה על פער של יותר מ-15 נ"ב בין סכום התרומות למדד, משהו במקורות השתנה וכדאי לבדוק.
