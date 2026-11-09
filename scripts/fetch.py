"""
Penny stocks plan: daily NSE/BSE end-of-day file downloader (runs in GitHub Actions).

Saves into data/<YYYY-MM-DD>/ with a status.json per day, and rebuilds index.json
so the dashboard builder can find everything over plain HTTPS.

Usage:
    python scripts/fetch.py                       # the day that is due now (IST; after midnight it is still the day that just closed)
    python scripts/fetch.py --date 2026-10-01
    python scripts/fetch.py --since 2026-04-20    # every weekday from that date to today, skips days already complete
"""
import argparse, gzip, http.cookiejar, io, json, sys, time, zipfile
import urllib.request, urllib.error
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IST = ZoneInfo("Asia/Kolkata")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NSE_BHAV = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
BSE_BHAV = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.CSV"
FLAGS_API = ("https://www.nseindia.com/api/reports?archives=%5B%7B%22name%22%3A%22Surveillance"
             "%20Indicator%22%2C%22type%22%3A%22daily-reports%22%2C%22category%22%3A%22capital-market"
             "%22%2C%22section%22%3A%22equities%22%7D%5D&date={dmon}&type=equities&mode=single")


FLAGS_ARCHIVE = "https://nsearchives.nseindia.com/archives/equities/mkt/REG_IND{dmy2}.csv"


def opener():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA), ("Accept", "*/*"), ("Accept-Language", "en-US,en;q=0.9")]
    for warm in ("https://www.nseindia.com/", "https://www.nseindia.com/all-reports", "https://www.bseindia.com/"):
        try:
            op.open(warm, timeout=20).read()
        except Exception:
            pass
    return op


def get(op, url, ref, tries=3, timeout=60):
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Referer": ref})
            return op.open(req, timeout=timeout).read(), ""
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}" + (" (not published yet, or a holiday)" if e.code == 404 else "")
            if e.code == 404:
                break
        except Exception as e:
            last = str(e)[:150]
        time.sleep(10 * (i + 1))
    return None, last


def is_data(b):
    return b is not None and len(b) > 200 and b"<html" not in b[:300].lower()


def run_day(op, d, want_flags=True):
    out = DATA / d.isoformat()
    st_path = out / "status.json"
    old = json.loads(st_path.read_text()) if st_path.exists() else {}
    files = dict(old.get("files", {}))
    issues = []
    ymd, dmy2 = d.strftime("%Y%m%d"), d.strftime("%d%m%y")
    out.mkdir(parents=True, exist_ok=True)

    nse = out / f"BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
    if files.get("nse_bhavcopy") != "ok":
        b, why = get(op, NSE_BHAV.format(ymd=ymd), "https://www.nseindia.com/")
        ok = False
        if is_data(b):
            try:
                ok = zipfile.ZipFile(io.BytesIO(b)).testzip() is None
            except Exception:
                ok = False
        if ok:
            nse.write_bytes(b); files["nse_bhavcopy"] = "ok"
        else:
            files["nse_bhavcopy"] = "missing"; issues.append(f"nse_bhavcopy (required): {why or 'not a data file'}")

    if want_flags and files.get("nse_flags") != "ok":
        # Try the archive server first (the same host that serves the bhavcopy, which works from GitHub), then NSE's
        # reports API, which often times out from GitHub's servers. Short timeouts, so a start never stalls for minutes.
        b, why = get(op, FLAGS_ARCHIVE.format(dmy2=dmy2), "https://www.nseindia.com/all-reports", tries=1, timeout=20)
        if not (b and (b[:2] == b"PK" or b"ScripCode" in b[:300])):
            b, why2 = get(op, FLAGS_API.format(dmon=d.strftime("%d-%b-%Y")), "https://www.nseindia.com/all-reports", tries=2, timeout=30)
            why = why2 or why
        if b and b[:2] == b"PK":
            try:
                z = zipfile.ZipFile(io.BytesIO(b))
                inner = [n for n in z.namelist() if n.lower().endswith(".csv")]
                b = z.read(inner[0]) if inner else b""
            except Exception:
                b = b""
        if b and b"ScripCode" in b[:300]:
            (out / f"REG_IND{dmy2}.csv").write_bytes(b); files["nse_flags"] = "ok"
        else:
            files["nse_flags"] = "missing"; issues.append(f"nse_flags: {why or 'NSE returned something that is not the flags file'}")

    if files.get("bse_bhavcopy") != "ok":
        b, why = get(op, BSE_BHAV.format(ymd=ymd), "https://www.bseindia.com/")
        if is_data(b):
            with gzip.open(out / f"BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.csv.gz", "wb") as g:
                g.write(b)
            files["bse_bhavcopy"] = "ok"
        else:
            files["bse_bhavcopy"] = "missing"; issues.append(f"bse_bhavcopy: {why or 'not a data file'}")

    if files.get("nse_bhavcopy") != "ok" and files.get("bse_bhavcopy") != "ok":
        issues.insert(0, "No bhavcopy from either exchange: likely a market holiday or files not published yet.")
    status = {"date": d.isoformat(), "checked_at": datetime.now(IST).isoformat(timespec="seconds"),
              "files": files, "issues": issues,
              # ready = the main files (NSE and BSE bhavcopy) are in. The surveillance flags file is best effort and has its own flag.
              "ready": files.get("nse_bhavcopy") == "ok" and files.get("bse_bhavcopy") == "ok",
              "flags_ready": files.get("nse_flags") == "ok" or not want_flags}
    st_path.write_text(json.dumps(status, indent=2))
    return status


def due_day(now=None):
    """The trading day whose files are due right now. The day flips at 16:00 India time, so a run that starts after midnight
    (GitHub can start runs hours late) or before the close still belongs to the day that last closed, not the new calendar day."""
    now = now or datetime.now(IST)
    return (now - timedelta(hours=16)).date()


def rebuild_index():
    idx = {}
    for st in sorted(DATA.glob("*/status.json")):
        s = json.loads(st.read_text())
        idx[s["date"]] = {"ready": s.get("ready", False), "flags_ready": s.get("flags_ready", s.get("files", {}).get("nse_flags") == "ok"), "files": sorted(p.name for p in st.parent.iterdir() if p.name != "status.json"),
                          "issues": s.get("issues", [])}
    (ROOT / "index.json").write_text(json.dumps({"updated": datetime.now(IST).isoformat(timespec="seconds"), "dates": idx}, indent=1))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date")
    p.add_argument("--since")
    p.add_argument("--ready", action="store_true", help="exit 0 if the main files (NSE and BSE bhavcopy) are in (or it is a weekend), else 1; fetches nothing")
    p.add_argument("--complete", action="store_true", help="like --ready, but the surveillance flags file must be in too")
    a = p.parse_args()
    if a.ready or a.complete:
        d = date.fromisoformat(a.date) if a.date else due_day()
        st = DATA / d.isoformat() / "status.json"
        s = json.loads(st.read_text()) if st.exists() else {}
        flags_ok = s.get("flags_ready", s.get("files", {}).get("nse_flags") == "ok")
        ok = d.weekday() >= 5 or (s.get("ready", False) and (flags_ok or not a.complete))
        print(d, "complete" if (ok and a.complete) else "ready" if ok else "not ready")
        sys.exit(0 if ok else 1)
    op = opener()
    today = due_day()   # not the calendar date: a backfill started after midnight must not create the next day's folder
    if a.since:
        d = date.fromisoformat(a.since)
        while d <= today:
            if d.weekday() < 5:
                s = run_day(op, d, want_flags=(today - d).days <= 3)
                print(d, "ok" if s["ready"] else "incomplete", " | ".join(s["issues"]))
                time.sleep(1.5)
            d += timedelta(days=1)
    else:
        d = date.fromisoformat(a.date) if a.date else due_day()
        if d.weekday() >= 5:
            print(d, "weekend, nothing to fetch")
        else:
            s = run_day(op, d)
            print(d, "ok" if s["ready"] else "incomplete", " | ".join(s["issues"]))
    rebuild_index()


if __name__ == "__main__":
    main()
