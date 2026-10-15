"""
History backfill: downloads old-format NSE end-of-day bhavcopy files (before NSE moved to the
new UDiFF format on 8 July 2024) into history/nse/<YYYY>/ and rebuilds history/index.json.

Save this file in the market-files repository as scripts/backfill.py.

Usage:
    python scripts/backfill.py --since 2015-01-01 --until 2015-12-31
Days already saved are skipped, so it is safe to run again.
"""
import argparse, io, json, time, zipfile, http.cookiejar, urllib.request, urllib.error
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "history" / "nse"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
URLS = [
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/{yyyy}/{MON}/cm{dd}{MON}{yyyy}bhav.csv.zip",
    "https://archives.nseindia.com/content/historical/EQUITIES/{yyyy}/{MON}/cm{dd}{MON}{yyyy}bhav.csv.zip",
]
LAST_OLD_FORMAT_DAY = date(2024, 7, 5)


def opener():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA), ("Accept", "*/*"), ("Accept-Language", "en-US,en;q=0.9")]
    for warm in ("https://www.nseindia.com/", "https://www.nseindia.com/all-reports"):
        try:
            op.open(warm, timeout=20).read()
        except Exception:
            pass
    return op


def fetch_day(op, d):
    out = HIST / str(d.year) / f"cm{d:%d}{d.strftime('%b').upper()}{d.year}bhav.csv.zip"
    if out.exists():
        return "have"
    why = ""
    for u in URLS:
        url = u.format(yyyy=d.year, MON=d.strftime("%b").upper(), dd=f"{d:%d}")
        for i in range(3):
            try:
                b = op.open(urllib.request.Request(url, headers={"Referer": "https://www.nseindia.com/"}), timeout=60).read()
                zipfile.ZipFile(io.BytesIO(b)).testzip()
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b)
                return "ok"
            except urllib.error.HTTPError as e:
                why = f"HTTP {e.code}"
                if e.code == 404:
                    break
            except Exception as e:
                why = str(e)[:120]
            time.sleep(5 * (i + 1))
    return "missing " + why


def rebuild_index():
    idx = {}
    for p in sorted(HIST.glob("*/cm*bhav.csv.zip")):
        n = p.name[2:11]  # DDMONYYYY
        try:
            from datetime import datetime
            d = datetime.strptime(n, "%d%b%Y").date().isoformat()
        except ValueError:
            continue
        idx[d] = f"history/nse/{p.parent.name}/{p.name}"
    (ROOT / "history" / "index.json").write_text(json.dumps({"format": "old NSE bhavcopy (cm...bhav.csv.zip)", "dates": idx}, indent=0))
    return len(idx)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--since", required=True)
    a.add_argument("--until", default=LAST_OLD_FORMAT_DAY.isoformat())
    a = a.parse_args()
    d = date.fromisoformat(a.since); end = min(date.fromisoformat(a.until), LAST_OLD_FORMAT_DAY)
    op = opener(); n = {"ok": 0, "have": 0, "missing": 0}
    while d <= end:
        if d.weekday() < 5:
            s = fetch_day(op, d)
            n[s.split()[0]] += 1
            if s.startswith("missing") and "404" not in s:
                print(d, s)
            if s == "ok":
                time.sleep(0.7)
        d += timedelta(days=1)
    print("saved", n["ok"], "already had", n["have"], "missing (holidays show up here too)", n["missing"])
    print("history days in index:", rebuild_index())


if __name__ == "__main__":
    main()
