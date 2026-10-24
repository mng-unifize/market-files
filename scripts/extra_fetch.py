#!/usr/bin/env python3
"""Extra market data for the Stocks plan research (Python standard library only).

Saves, under extra/ in this repository:
  delivery/YYYY-MM-DD.csv.gz      NSE delivery file (share of traded shares taken into demat accounts)
  bulk/YYYY-MM-DD.csv.gz          NSE bulk deals, snapshot of the day
  block/YYYY-MM-DD.csv.gz         NSE block deals, snapshot of the day
  announcements/YYYY-MM-DD.json.gz  company announcements (order wins, results, fund raising)
  results/YYYY-MM.json.gz         quarterly financial results filed in that month
  shareholding/YYYY-MM.json.gz    shareholding patterns filed in that month
  insider/YYYY-MM.json.gz         insider and promoter trades (SEBI PIT disclosures)
  actions/YYYY-MM.json.gz         corporate actions (splits, bonus, dividends, demergers)
  bulkhist/YYYY-MM.json.gz, blockhist/YYYY-MM.json.gz   month history of bulk and block deals
  pledge/YYYY-MM-DD.json.gz       promoter pledge snapshot
  prices_old/YYYY-MM-DD.csv.zip   NSE daily prices before 8 Jul 2024 (old file format)
  index.json                      what was fetched, what is missing, what failed

Usage:  python scripts/extra_fetch.py [--from YYYY-MM-DD] [--only name,name] [--dry-run]
  names: delivery announcements results shareholding insider actions bulkhist blockhist prices_old
Without --from it refreshes the last 7 days. With --from it backfills in time-limited slices;
run it again and it resumes (finished items are skipped).
Endpoints are NSE public pages. They are not an official data feed and can change or block cloud servers.
"""
import os, sys, io, json, gzip, time, argparse, datetime as dt
import urllib.request, urllib.error, http.cookiejar

OUT = os.environ.get("EXTRA_DIR", "extra")
NSE = os.environ.get("NSE_BASE", "https://www.nseindia.com")
ARCH = os.environ.get("ARCH_BASE", "https://nsearchives.nseindia.com")
BUDGET = float(os.environ.get("BUDGET_MIN", "300")) * 60
PAUSE = float(os.environ.get("PAUSE_SEC", "1.0"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
T0 = time.time()


class Blocked(Exception):
    pass


BLOCKED = {}   # "api" or "arch" -> True once NSE keeps refusing us


def kind_of(url):
    return "api" if "/api/" in url else "arch"


class Sess:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.n = 0; self.bad = {}

    def warm(self):
        for p in ("/", "/companies-listing/corporate-filings-financial-results"):
            try: self._get(NSE + p, html=True, tries=1, rewarm=False)
            except Exception: pass

    def _get(self, url, html=False, tries=4, rewarm=True):
        last = None; kind = kind_of(url)
        if BLOCKED.get(kind): raise Blocked(f"{kind} is blocked")
        for k in range(tries):
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "text/html,*/*" if html else "application/json,text/csv,*/*",
                "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip", "Referer": NSE + "/"})
            try:
                with self.op.open(req, timeout=60) as r:
                    b = r.read()
                    if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
                    self.bad[kind] = 0; return b
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 404: raise
                if e.code in (401, 403, 429) and rewarm:   # warm-up pages never count as a block
                    self.bad[kind] = self.bad.get(kind, 0) + 1
                    if self.bad[kind] >= 6:
                        BLOCKED[kind] = True; raise Blocked(f"HTTP {e.code} repeatedly for {kind} pages ({url[:60]})")
                    if rewarm: self.warm()
            except Exception as e:
                last = e
            time.sleep(2 * (k + 1))
        raise last

    def get(self, url, **kw):
        if BLOCKED.get(kind_of(url)): raise Blocked(f"{kind_of(url)} is blocked")
        self.n += 1
        if self.n % 120 == 0: self.warm()
        time.sleep(PAUSE)
        return self._get(url, **kw)


S = Sess()
IDX_PATH = os.path.join(OUT, "index.json")
IDX = {"updated": None, "sets": {}, "issues": []}
if os.path.exists(IDX_PATH):
    try: IDX = json.load(open(IDX_PATH))
    except Exception: pass
IDX.setdefault("sets", {}); IDX["issues"] = []


def save_idx():
    IDX["updated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(OUT, exist_ok=True)
    json.dump(IDX, open(IDX_PATH, "w"), indent=1, sort_keys=True)


def write_gz(rel, data):
    p = os.path.join(OUT, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    with gzip.open(p, "wb") as f: f.write(data)


def write_raw(rel, data):
    p = os.path.join(OUT, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f: f.write(data)


def mark(ds, key, **kw):
    IDX["sets"].setdefault(ds, {})[key] = kw


def note_blocked(e):
    msg = "BLOCKED: " + str(e)
    if msg not in IDX["issues"]: IDX["issues"].append(msg)


def done(ds, key, refetch):
    v = IDX["sets"].get(ds, {}).get(key)
    empty = bool(v) and v.get("rows", 1) == 0 and not v.get("missing") and ds in ("results", "insider", "shareholding", "actions")
    return bool(v) and v.get("ok") and not refetch and not empty   # empty months are tried again


def out_of_time():
    return time.time() - T0 > BUDGET


def fmt(d): return d.strftime("%d-%m-%Y")


def json_rows(b):
    j = json.loads(b.decode("utf-8", "replace"))
    if isinstance(j, dict):
        for k in ("data", "Data", "results"):
            if isinstance(j.get(k), list): return j, len(j[k])
        return j, 0 if not j else 1
    return j, len(j) if isinstance(j, list) else 0


def fetch_json(ds, key, rel, url, refetch):
    if done(ds, key, refetch): return
    try:
        b = S.get(url)
        j, n = json_rows(b)
        write_gz(rel, json.dumps(j, separators=(",", ":")).encode())
        mark(ds, key, ok=True, rows=n)
    except Blocked as e: note_blocked(e)
    except Exception as e:
        mark(ds, key, ok=False, err=str(e)[:120]); IDX["issues"].append(f"{ds} {key}: {str(e)[:100]}")


def fetch_csv(ds, key, rel, url, refetch, must_start=None, raw_zip=False):
    if done(ds, key, refetch): return
    try:
        b = S.get(url)
        head = b[:300].decode("utf-8", "replace").lstrip("﻿").strip()
        if must_start and not head.upper().startswith(must_start): raise ValueError("unexpected content: " + head[:60])
        write_gz(rel, b); mark(ds, key, ok=True, rows=max(0, b.count(b"\n") - 1))
    except urllib.error.HTTPError as e:
        if e.code == 404: mark(ds, key, ok=True, rows=0, missing=True)   # holiday or not published
        else: mark(ds, key, ok=False, err=f"HTTP {e.code}"); IDX["issues"].append(f"{ds} {key}: HTTP {e.code}")
    except Blocked as e: note_blocked(e)
    except Exception as e:
        mark(ds, key, ok=False, err=str(e)[:120]); IDX["issues"].append(f"{ds} {key}: {str(e)[:100]}")


def months(a, b):
    d = a.replace(day=1)
    while d <= b:
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield d, min(nxt - dt.timedelta(days=1), b)
        d = nxt


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--from", dest="frm", default=""); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--only", default="")
    a = ap.parse_args()
    today = dt.date.today()
    only = set(x for x in a.only.split(",") if x)
    want = lambda name: not only or name in only
    start = dt.date.fromisoformat(a.frm) if a.frm else today - dt.timedelta(days=7)
    days = [start + dt.timedelta(n) for n in range((today - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    recent = lambda d: (today - d).days <= 5
    mths = list(months(start, today))
    plan = ([("delivery", d) for d in days] + [("announcements", d) for d in days] +
            [(n, m) for n in ("results", "shareholding", "insider", "actions", "bulkhist", "blockhist") for m in mths])
    if a.dry_run:
        print("days", len(days), "months", len(mths), "tasks", len(plan)); return 0
    S.warm()
    api = {
        "results": "/api/corporates-financial-results?index=equities&period=Quarterly&from_date={a}&to_date={b}",
        "shareholding": "/api/corporate-share-holdings-master?index=equities&from_date={a}&to_date={b}",
        "insider": "/api/corporates-pit?index=equities&from_date={a}&to_date={b}",
        "actions": "/api/corporates-corporateActions?index=equities&from_date={a}&to_date={b}",
        "bulkhist": "/api/historical/bulk-deals?from={a}&to={b}",
        "blockhist": "/api/historical/block-deals?from={a}&to={b}",
    }
    code = 0
    try:
        # 1. Today's snapshots first (they only exist for the latest day, so never skip them)
        if start >= today - dt.timedelta(days=7) and not only:
            for nm, path in (("bulk", "/content/equities/bulk.csv"), ("block", "/content/equities/block.csv")):
                k = today.isoformat()
                fetch_csv(nm, k, f"{nm}/{k}.csv.gz", ARCH + path, True)
            fetch_json("pledge", today.isoformat(), f"pledge/{today.isoformat()}.json.gz", NSE + "/api/corporate-pledgedata?index=equities", True)
        # 2. Months, newest first
        for nm in api:
            if not want(nm): continue
            for m0, m1 in reversed(mths):
                if out_of_time(): raise TimeoutError
                if BLOCKED.get("api"): break
                key = m0.strftime("%Y-%m")
                fetch_json(nm, key, f"{nm}/{key}.json.gz", NSE + api[nm].format(a=fmt(m0), b=fmt(m1)), m1 >= today - dt.timedelta(days=40))
                if S.n % 25 == 0: save_idx()
        # 3. Days, newest first
        for d in reversed(days):
            if out_of_time(): raise TimeoutError
            if BLOCKED.get("api") and BLOCKED.get("arch"): break
            k = d.isoformat()
            if want("delivery"):
                fetch_csv("delivery", k, f"delivery/{k}.csv.gz", ARCH + f"/products/content/sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv", recent(d), must_start="SYMBOL")
            if want("announcements"):
                fetch_json("announcements", k, f"announcements/{k}.json.gz", NSE + f"/api/corporate-announcements?index=equities&from_date={fmt(d)}&to_date={fmt(d)}", recent(d))
            if want("prices_old") and d < dt.date(2024, 7, 8):   # NSE daily price file in its old format (the repository starts at 8 Jul 2024)
                mon = d.strftime("%b").upper()
                fetch_csv("prices_old", k, f"prices_old/{k}.csv.zip", ARCH + f"/content/historical/EQUITIES/{d.year}/{mon}/cm{d.strftime('%d')}{mon}{d.year}bhav.csv.zip", False, raw_zip=True)
            if S.n % 25 == 0: save_idx()
    except TimeoutError:
        IDX["issues"].append("time budget reached, run again to resume")
    if BLOCKED: code = 3
    save_idx()
    ok = sum(1 for s in IDX["sets"].values() for v in s.values() if v.get("ok")); bad = sum(1 for s in IDX["sets"].values() for v in s.values() if not v.get("ok"))
    print("requests", S.n, "ok items", ok, "failed items", bad, "issues", len(IDX["issues"]))
    for i in IDX["issues"][:15]: print(" -", i)
    return code


if __name__ == "__main__":
    sys.exit(main())
