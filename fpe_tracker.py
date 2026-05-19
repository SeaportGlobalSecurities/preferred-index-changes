"""
FPE ETF Holdings Tracker
Fetches current holdings, saves a daily snapshot, and diffs against the prior day.
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent / "data"
URL = "https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker=FPE"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_holdings() -> tuple[str, list[dict]]:
    """Fetch and parse holdings table. Returns (as_of_date_str, list_of_holdings)."""
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Extract as-of date from the page
    as_of = ""
    for tag in soup.find_all(string=True):
        txt = tag.strip()
        if "Holdings as of" in txt or "As of" in txt:
            as_of = txt
            break

    # Find the tr whose first cell is "Security Name" - this is the header row
    header_row = None
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if cells and cells[0].get_text(strip=True) == "Security Name":
            header_row = row
            break

    if header_row is None:
        raise RuntimeError("Could not locate holdings table on the page.")

    parent_table = header_row.find_parent("table")
    col_headers = [c.get_text(strip=True) for c in header_row.find_all(["td", "th"])]

    holdings = []
    skip_header = True
    for row in parent_table.find_all("tr", recursive=False):
        cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
        if not cells or len(cells) < 2:
            continue
        if skip_header and cells[0] == "Security Name":
            skip_header = False
            continue
        record = dict(zip(col_headers, cells))
        # Skip footer/blank rows
        name = record.get("Security Name", "")
        if not name or name == "Security Name":
            continue
        holdings.append(record)

    return as_of.strip(), holdings


def snapshot_path(for_date: date) -> Path:
    return DATA_DIR / f"fpe_{for_date.isoformat()}.json"


def save_snapshot(holdings: list[dict], as_of: str, for_date: date) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(for_date)
    payload = {"as_of": as_of, "fetch_date": for_date.isoformat(), "holdings": holdings}
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_snapshot(for_date: date) -> list[dict] | None:
    path = snapshot_path(for_date)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data["holdings"]


def find_prior_snapshot(before_date: date) -> tuple[date, list[dict]] | tuple[None, None]:
    """Walk backwards up to 10 days to find the most recent saved snapshot."""
    for i in range(1, 11):
        d = before_date - timedelta(days=i)
        snap = load_snapshot(d)
        if snap is not None:
            return d, snap
    return None, None


def _key(holding: dict) -> str:
    """Unique key for a holding: prefer CUSIP/Identifier, fall back to name."""
    for col in ("CUSIP", "Identifier", "Security Name"):
        val = holding.get(col, "").strip()
        if val:
            return val
    return str(holding)


def _weight(holding: dict) -> float:
    raw = holding.get("Weighting", "0%").replace("%", "").replace(",", "").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _shares(holding: dict) -> float:
    raw = holding.get("Shares / Quantity", "0").replace(",", "").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _mktval(holding: dict) -> float:
    raw = holding.get("Market Value", "0").replace("$", "").replace(",", "").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.0


def compare(current: list[dict], prior: list[dict]) -> dict:
    cur_map = {_key(h): h for h in current}
    pri_map = {_key(h): h for h in prior}

    added = [cur_map[k] for k in cur_map if k not in pri_map]
    removed = [pri_map[k] for k in pri_map if k not in cur_map]

    changed = []
    for k in cur_map:
        if k not in pri_map:
            continue
        c, p = cur_map[k], pri_map[k]
        dw = round(_weight(c) - _weight(p), 4)
        ds = _shares(c) - _shares(p)
        dm = round(_mktval(c) - _mktval(p), 2)
        if abs(dw) > 0.0001 or abs(ds) > 0 or abs(dm) > 0.01:
            changed.append({
                "key": k,
                "name": c.get("Security Name", k),
                "weight_prior": _weight(p),
                "weight_current": _weight(c),
                "weight_delta": dw,
                "shares_prior": _shares(p),
                "shares_current": _shares(c),
                "shares_delta": ds,
                "mktval_prior": _mktval(p),
                "mktval_current": _mktval(c),
                "mktval_delta": dm,
            })

    changed.sort(key=lambda x: abs(x["weight_delta"]), reverse=True)
    return {"added": added, "removed": removed, "changed": changed}


def fmt_weight(w: float) -> str:
    return f"{w:.4f}%"


def fmt_shares(s: float) -> str:
    return f"{s:,.0f}"


def fmt_dollar(d: float) -> str:
    return f"${d:,.2f}"


def print_report(diff: dict, today: date, prior_date: date, today_count: int, prior_count: int):
    print("=" * 70)
    print(f"  FPE ETF Holdings Change Report")
    print(f"  Comparing: {prior_date} ({prior_count} holdings)  ->  {today} ({today_count} holdings)")
    print("=" * 70)

    added = diff["added"]
    removed = diff["removed"]
    changed = diff["changed"]

    if not added and not removed and not changed:
        print("\n  No changes detected between the two snapshots.\n")
        return

    if added:
        print(f"\n  NEW POSITIONS ({len(added)}):")
        print(f"  {'Security':<50}  {'CUSIP':<15}  {'Weight':>8}  {'Shares':>15}")
        print(f"  {'-'*50}  {'-'*15}  {'-'*8}  {'-'*15}")
        for h in sorted(added, key=_weight, reverse=True):
            name = h.get("Security Name", "")[:50]
            cusip = h.get("CUSIP", h.get("Identifier", ""))[:15]
            print(f"  {name:<50}  {cusip:<15}  {fmt_weight(_weight(h)):>8}  {fmt_shares(_shares(h)):>15}")

    if removed:
        print(f"\n  REMOVED POSITIONS ({len(removed)}):")
        print(f"  {'Security':<50}  {'CUSIP':<15}  {'Prior Wt':>8}  {'Prior Shares':>15}")
        print(f"  {'-'*50}  {'-'*15}  {'-'*8}  {'-'*15}")
        for h in sorted(removed, key=_weight, reverse=True):
            name = h.get("Security Name", "")[:50]
            cusip = h.get("CUSIP", h.get("Identifier", ""))[:15]
            print(f"  {name:<50}  {cusip:<15}  {fmt_weight(_weight(h)):>8}  {fmt_shares(_shares(h)):>15}")

    if changed:
        print(f"\n  CHANGED POSITIONS ({len(changed)}) - sorted by |weight delta|:")
        print(f"  {'Security':<45}  {'Wt Prior':>8}  {'Wt Now':>8}  {'Wt Delta':>8}  {'Shares Delta':>15}  {'MktVal Delta':>14}")
        print(f"  {'-'*45}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*15}  {'-'*14}")
        for c in changed:
            name = c["name"][:45]
            delta_sign = "+" if c["weight_delta"] > 0 else ""
            shares_sign = "+" if c["shares_delta"] > 0 else ""
            mktval_sign = "+" if c["mktval_delta"] > 0 else ""
            print(
                f"  {name:<45}  "
                f"{fmt_weight(c['weight_prior']):>8}  "
                f"{fmt_weight(c['weight_current']):>8}  "
                f"{delta_sign}{fmt_weight(c['weight_delta']):>8}  "
                f"{shares_sign}{fmt_shares(c['shares_delta']):>15}  "
                f"{mktval_sign}{fmt_dollar(abs(c['mktval_delta'])):>14}"
            )

    print()


def main():
    today = date.today()

    # Allow overriding today's date for testing: python fpe_tracker.py --date 2026-05-19
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        today = date.fromisoformat(sys.argv[idx + 1])

    force_refetch = "--force" in sys.argv

    # Load or fetch today's snapshot
    todays_holdings = None if force_refetch else load_snapshot(today)
    if todays_holdings is None:
        print(f"Fetching FPE holdings for {today}...")
        as_of, todays_holdings = fetch_holdings()
        path = save_snapshot(todays_holdings, as_of, today)
        print(f"Saved {len(todays_holdings)} holdings -> {path.name}")
    else:
        print(f"Loaded cached snapshot for {today} ({len(todays_holdings)} holdings).")

    # Find prior snapshot
    prior_date, prior_holdings = find_prior_snapshot(today)

    if prior_holdings is None:
        print(
            f"\nNo prior snapshot found (checked last 10 days).\n"
            f"Run this script again tomorrow to see changes.\n"
            f"Current snapshot saved for {today}."
        )
        return

    diff = compare(todays_holdings, prior_holdings)
    print_report(diff, today, prior_date, len(todays_holdings), len(prior_holdings))

    # Also save diff as JSON for downstream use
    diff_path = DATA_DIR / f"fpe_diff_{prior_date}_to_{today}.json"
    diff_path.write_text(json.dumps(diff, indent=2))
    print(f"  Diff saved -> {diff_path.name}")


if __name__ == "__main__":
    main()
