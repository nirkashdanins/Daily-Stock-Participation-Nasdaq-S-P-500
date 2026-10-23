"""
מצלם את הדשבורד (docs/index.html) לשתי תמונות בפריסת נייד: out/QQQ.png, out/SPY.png
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"


def main():
    OUT.mkdir(exist_ok=True)
    url = (ROOT / "docs" / "index.html").resolve().as_uri()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 620, "height": 900}, device_scale_factor=2)
        pg.goto(url, wait_until="networkidle")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(800)
        for k in ("QQQ", "SPY"):
            pg.click(f'button[data-k="{k}"]')
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(OUT / f"{k}.png"), full_page=True)
            print(f"צולם: out/{k}.png")
        b.close()


if __name__ == "__main__":
    main()
