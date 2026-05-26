"""
FPE / FPEI ETF Holdings Tracker
Fetches current holdings, saves a daily snapshot, and diffs against the prior day.
"""

import html as _html
import json
import sys
import webbrowser
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent / "data"
DOCS_DIR = Path(__file__).parent / "docs"

TICKERS = {
    "FPE":  "https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker=FPE",
    "FPEI": "https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker=FPEI",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  FETCH / SNAPSHOT
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_holdings(url: str) -> tuple[str, list[dict]]:
    """Fetch and parse holdings table. Returns (as_of_date_str, list_of_holdings)."""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    as_of = ""
    for tag in soup.find_all(string=True):
        txt = tag.strip()
        if "Holdings as of" in txt or "As of" in txt:
            as_of = txt
            break

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
        name = record.get("Security Name", "")
        if not name or name == "Security Name":
            continue
        holdings.append(record)

    return as_of.strip(), holdings


def snapshot_path(for_date: date, ticker: str) -> Path:
    return DATA_DIR / f"{ticker.lower()}_{for_date.isoformat()}.json"


def save_snapshot(holdings: list[dict], as_of: str, for_date: date, ticker: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(for_date, ticker)
    payload = {"as_of": as_of, "fetch_date": for_date.isoformat(), "ticker": ticker, "holdings": holdings}
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_snapshot(for_date: date, ticker: str) -> list[dict] | None:
    path = snapshot_path(for_date, ticker)
    if not path.exists():
        return None
    return json.loads(path.read_text())["holdings"]


def load_snapshot_full(for_date: date, ticker: str) -> dict | None:
    path = snapshot_path(for_date, ticker)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def find_prior_snapshot(before_date: date, ticker: str) -> tuple[date, list[dict]] | tuple[None, None]:
    """Walk backwards up to 10 days to find the most recent saved snapshot."""
    for i in range(1, 11):
        d = before_date - timedelta(days=i)
        snap = load_snapshot(d, ticker)
        if snap is not None:
            return d, snap
    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
#  DIFF
# ═══════════════════════════════════════════════════════════════════════════════

def _key(holding: dict) -> str:
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

    added   = [cur_map[k] for k in cur_map if k not in pri_map]
    removed = [pri_map[k] for k in pri_map if k not in cur_map]

    changed = []
    for k in cur_map:
        if k not in pri_map:
            continue
        c, p = cur_map[k], pri_map[k]
        ds = _shares(c) - _shares(p)
        if abs(ds) > 0:
            changed.append({
                "key": k,
                "name": c.get("Security Name", k),
                "shares_prior":   _shares(p),
                "shares_current": _shares(c),
                "shares_delta":   ds,
            })

    changed.sort(key=lambda x: abs(x["shares_delta"]), reverse=True)
    return {"added": added, "removed": removed, "changed": changed}


# ═══════════════════════════════════════════════════════════════════════════════
#  TEXT REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def print_report(diff: dict, today: date, prior_date: date,
                 today_count: int, prior_count: int, ticker: str = "FPE"):
    print("=" * 70)
    print(f"  {ticker} ETF Holdings Change Report")
    print(f"  Comparing: {prior_date} ({prior_count} holdings)  ->  {today} ({today_count} holdings)")
    print("=" * 70)

    added, removed, changed = diff["added"], diff["removed"], diff["changed"]

    if not added and not removed and not changed:
        print("\n  No changes detected between the two snapshots.\n")
        return

    if added:
        print(f"\n  NEW POSITIONS ({len(added)}):")
        print(f"  {'Security':<50}  {'CUSIP':<15}  {'Shares':>15}")
        print(f"  {'-'*50}  {'-'*15}  {'-'*15}")
        for h in sorted(added, key=_shares, reverse=True):
            name  = h.get("Security Name", "")[:50]
            cusip = h.get("CUSIP", h.get("Identifier", ""))[:15]
            print(f"  {name:<50}  {cusip:<15}  {_shares(h):>15,.0f}")

    if removed:
        print(f"\n  REMOVED POSITIONS ({len(removed)}):")
        print(f"  {'Security':<50}  {'CUSIP':<15}  {'Shares':>15}")
        print(f"  {'-'*50}  {'-'*15}  {'-'*15}")
        for h in sorted(removed, key=_shares, reverse=True):
            name  = h.get("Security Name", "")[:50]
            cusip = h.get("CUSIP", h.get("Identifier", ""))[:15]
            print(f"  {name:<50}  {cusip:<15}  {_shares(h):>15,.0f}")

    if changed:
        print(f"\n  CHANGED POSITIONS ({len(changed)}) - sorted by |shares delta|:")
        print(f"  {'Security':<50}  {'CUSIP':<15}  {'Prior':>15}  {'Current':>15}  {'Delta':>15}")
        print(f"  {'-'*50}  {'-'*15}  {'-'*15}  {'-'*15}  {'-'*15}")
        for c in changed:
            ds_s = "+" if c["shares_delta"] > 0 else ""
            print(
                f"  {c['name'][:50]:<50}  "
                f"{c['key'][:15]:<15}  "
                f"{c['shares_prior']:>15,.0f}  "
                f"{c['shares_current']:>15,.0f}  "
                f"{ds_s}{c['shares_delta']:>15,.0f}"
            )

    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  HTML REPORT
# ═══════════════════════════════════════════════════════════════════════════════

_TABLE_CTR = 0

_SORT_JS = """<script>
function sortTable(id,col){
  var t=document.getElementById(id),tb=t.querySelector('tbody');
  var rows=Array.from(tb.querySelectorAll('tr'));
  var ths=t.querySelectorAll('th'),th=ths[col];
  var asc=th.getAttribute('data-dir')!=='asc';
  ths.forEach(function(h){
    h.setAttribute('data-dir','');
    h.textContent=h.textContent.replace(/ [▲▼]$/,'');
  });
  th.setAttribute('data-dir',asc?'asc':'desc');
  th.textContent+=(asc?' ▲':' ▼');
  rows.sort(function(a,b){
    var av=a.cells[col].getAttribute('data-sort')||a.cells[col].textContent.trim();
    var bv=b.cells[col].getAttribute('data-sort')||b.cells[col].textContent.trim();
    var an=parseFloat(av.replace(/[^0-9.\\-+]/g,'')),bn=parseFloat(bv.replace(/[^0-9.\\-+]/g,''));
    if(!isNaN(an)&&!isNaN(bn)) return asc?an-bn:bn-an;
    return asc?av.localeCompare(bv):bv.localeCompare(av);
  });
  rows.forEach(function(r){tb.appendChild(r);});
}
</script>"""

_PAGE_CSS = """<style>
*,*::before,*::after{box-sizing:border-box}
body{margin:0;padding:24px 36px;background:#0d0d0d;color:#e0e0e0;
  font-family:"Courier New",Courier,monospace;font-size:13px;}
h1{color:#f7a700;margin:0 0 4px 0;font-size:22px;letter-spacing:.5px;}
h2{color:#f7a700;margin:24px 0 4px 0;font-size:18px;letter-spacing:.5px;
  border-top:1px solid #333;padding-top:20px;}
h2:first-of-type{border-top:none;padding-top:0;}
.sub{color:#888;font-size:13px;margin:0 0 20px 0;}
.stat-bar{display:flex;gap:24px;flex-wrap:wrap;padding:12px 16px;
  background:#111827;border:1px solid #333;border-radius:6px;margin:0 0 20px 0;}
.stat{font-size:12px}
.stat-lbl{color:#888}
.stat-val{font-weight:bold;margin-left:4px}
.s-new .stat-val{color:#2ecc71}
.s-rem .stat-val{color:#e74c3c}
.s-chg .stat-val{color:#f7a700}
.s-tot .stat-val{color:#e0e0e0}
details{margin:8px 0;border:1px solid #444;border-radius:6px;}
summary{cursor:pointer;background:#111827;color:#f7a700;font-size:14px;
  font-weight:bold;padding:10px 14px;user-select:none;list-style:none;border-radius:6px;}
summary::-webkit-details-marker{display:none}
summary::before{content:'▶ ';font-size:10px}
details[open]>summary::before{content:'▼ '}
.sum-sub{color:#aaa;font-weight:normal;font-size:12px}
.sec-body{padding:12px 14px 14px}
.tbl-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12px;
  font-family:"Courier New",Courier,monospace;color:#e0e0e0;}
thead th{text-align:left;padding:6px 10px;border-bottom:2px solid #f7a700;
  color:#f7a700;font-size:11px;text-transform:uppercase;
  letter-spacing:.5px;white-space:nowrap;cursor:pointer;user-select:none;}
tbody tr:hover{background:#1a1a2e}
tbody td{padding:6px 10px;border-bottom:1px solid #252525;white-space:nowrap;}
.nm{white-space:normal;word-wrap:break-word;max-width:340px;}
.pos{color:#2ecc71;font-weight:bold}
.neg{color:#e74c3c;font-weight:bold}
.muted{color:#555}
.empty{color:#888;font-size:12px;padding:8px 0}
</style>"""


def _tid() -> str:
    global _TABLE_CTR
    _TABLE_CTR += 1
    return f"tbl_{_TABLE_CTR}"


def _e(v: object) -> str:
    return _html.escape(str(v))


def _td(content: str, sort: str = "", cls: str = "") -> str:
    sa = f' data-sort="{_e(sort)}"' if sort else ""
    ca = f' class="{cls}"' if cls else ""
    return f"<td{sa}{ca}>{content}</td>"


def _ss(v: float) -> tuple[str, str]:
    if v == 0:
        return '<span class="muted">—</span>', "0"
    sign = "+" if v > 0 else ""
    css  = "pos" if v > 0 else "neg"
    return f'<span class="{css}">{sign}{v:,.0f}</span>', str(v)


def _build_table(headers: list[str], rows: list[str]) -> str:
    tid = _tid()
    ths = "".join(
        f'<th onclick="sortTable(\'{tid}\',{i})">{_e(h)}</th>'
        for i, h in enumerate(headers)
    )
    return (
        f'<div class="tbl-wrap"><table id="{tid}">'
        f"<thead><tr>{ths}</tr></thead>"
        f'<tbody>{"".join(rows)}</tbody>'
        f"</table></div>"
    )


def _holding_row_html(h: dict) -> str:
    name  = h.get("Security Name", "")
    ident = h.get("Identifier", "") or ""
    cusip = h.get("CUSIP", "") or ""
    s     = _shares(h)
    id_html    = _e(ident) if ident else (_e(cusip) if cusip else '<span class="muted">—</span>')
    cusip_html = _e(cusip) if cusip else '<span class="muted">—</span>'
    return (
        "<tr>"
        + _td(_e(name), sort=name, cls="nm")
        + _td(id_html, sort=ident or cusip)
        + _td(cusip_html, sort=cusip)
        + _td(f"{s:,.0f}", sort=str(s))
        + "</tr>"
    )


def _changed_row_html(c: dict) -> str:
    name     = c["name"]
    key      = c["key"]
    key_html = _e(key) if key != name else '<span class="muted">—</span>'
    ds_h, ds_s = _ss(c["shares_delta"])
    return (
        "<tr>"
        + _td(_e(name), sort=name, cls="nm")
        + _td(key_html, sort=key if key != name else "")
        + _td(f"{c['shares_prior']:,.0f}", sort=str(c["shares_prior"]))
        + _td(f"{c['shares_current']:,.0f}", sort=str(c["shares_current"]))
        + _td(ds_h, sort=ds_s)
        + "</tr>"
    )


def _section_html(title: str, subtitle: str, body: str, open_: bool = True) -> str:
    open_a = " open" if open_ else ""
    return (
        f"<details{open_a}>"
        f"<summary>{_e(title)} "
        f'<span class="sum-sub">— {subtitle}</span>'
        f"</summary>"
        f'<div class="sec-body">{body}</div>'
        f"</details>"
    )


def _build_etf_body(
    ticker: str,
    diff: dict,
    today: date,
    prior_date: date,
    today_count: int,
    prior_count: int,
    as_of: str = "",
) -> str:
    """Build the inner HTML for one ETF section (no <html>/<head> wrapper)."""
    added, removed, changed = diff["added"], diff["removed"], diff["changed"]

    as_of_stat = (
        f'<div class="stat s-tot"><span class="stat-lbl">Data as of:</span>'
        f'<span class="stat-val">{_e(as_of)}</span></div>'
        if as_of else ""
    )
    stat_bar = (
        f'<div class="stat-bar">'
        f'<div class="stat s-tot"><span class="stat-lbl">Prior holdings:</span>'
        f'<span class="stat-val">{prior_count:,}</span></div>'
        f'<div class="stat s-tot"><span class="stat-lbl">Current holdings:</span>'
        f'<span class="stat-val">{today_count:,}</span></div>'
        f'<div class="stat s-new"><span class="stat-lbl">New:</span>'
        f'<span class="stat-val">{len(added)}</span></div>'
        f'<div class="stat s-rem"><span class="stat-lbl">Removed:</span>'
        f'<span class="stat-val">{len(removed)}</span></div>'
        f'<div class="stat s-chg"><span class="stat-lbl">Changed:</span>'
        f'<span class="stat-val">{len(changed)}</span></div>'
        f"{as_of_stat}"
        f"</div>"
    )

    hdrs_h = ["Name", "Identifier", "CUSIP", "Shares"]
    hdrs_c = ["Name", "CUSIP / ID", "Prior Shares", "Current Shares", "Delta"]

    if added:
        rows    = [_holding_row_html(h) for h in sorted(added, key=_shares, reverse=True)]
        new_sec = _section_html("New Positions", f"{len(added)} added", _build_table(hdrs_h, rows))
    else:
        new_sec = _section_html("New Positions", "0 added",
                                '<p class="empty">No new positions.</p>', open_=False)

    if removed:
        rows    = [_holding_row_html(h) for h in sorted(removed, key=_shares, reverse=True)]
        rem_sec = _section_html("Removed Positions", f"{len(removed)} removed", _build_table(hdrs_h, rows))
    else:
        rem_sec = _section_html("Removed Positions", "0 removed",
                                '<p class="empty">No removed positions.</p>', open_=False)

    if changed:
        rows    = [_changed_row_html(c) for c in changed]
        chg_sec = _section_html(
            "Changed Positions",
            f"{len(changed)} changed — sorted by |shares delta|",
            _build_table(hdrs_c, rows),
        )
    else:
        chg_sec = _section_html("Changed Positions", "0 changed",
                                '<p class="empty">No changed positions.</p>', open_=False)

    if not added and not removed and not changed:
        content = '<p style="color:#888;padding:20px 0;">No changes detected between snapshots.</p>'
    else:
        content = f"{new_sec}\n{rem_sec}\n{chg_sec}"

    return (
        f'<h2>{_e(ticker)} ETF Holdings Change Report</h2>\n'
        f'<p class="sub">'
        f'Comparing: <strong>{prior_date}</strong> ({prior_count:,} holdings) '
        f'&rarr; <strong>{today}</strong> ({today_count:,} holdings)'
        f'</p>\n'
        f'{stat_bar}\n'
        f'{content}\n'
    )


def _wrap_html(title: str, body: str) -> str:
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        f'<title>{_e(title)}</title>\n'
        f'{_PAGE_CSS}\n'
        '</head>\n<body>\n'
        f'{_SORT_JS}\n'
        f'{body}'
        '</body>\n</html>'
    )


def build_html_report(
    diff: dict,
    today: date,
    prior_date: date,
    today_count: int,
    prior_count: int,
    as_of: str = "",
    ticker: str = "FPE",
) -> str:
    global _TABLE_CTR
    _TABLE_CTR = 0
    body = _build_etf_body(ticker, diff, today, prior_date, today_count, prior_count, as_of)
    return _wrap_html(f"{ticker} Holdings — {today}", body)


def write_html_report(
    diff: dict,
    today: date,
    prior_date: date,
    today_count: int,
    prior_count: int,
    as_of: str = "",
    ticker: str = "FPE",
) -> Path:
    content = build_html_report(diff, today, prior_date, today_count, prior_count, as_of, ticker)
    path = DATA_DIR / f"{ticker.lower()}_report_{prior_date}_to_{today}.html"
    path.write_text(content, encoding="utf-8")
    return path


def write_combined_html(ticker_results: list[dict], today: date) -> Path:
    """Generate docs/index.html combining all tickers."""
    global _TABLE_CTR
    _TABLE_CTR = 0

    sections = []
    for r in ticker_results:
        sections.append(
            _build_etf_body(
                r["ticker"], r["diff"], today, r["prior_date"],
                r["today_count"], r["prior_count"], r.get("as_of", ""),
            )
        )

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    path = DOCS_DIR / "index.html"
    path.write_text(
        _wrap_html(
            f"ETF Holdings — {today}",
            f'<h1>ETF Holdings Change Reports — {_e(str(today))}</h1>\n' + "\n".join(sections),
        ),
        encoding="utf-8",
    )
    return path


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def _holdings_identical(a: list[dict], b: list[dict]) -> bool:
    return sorted(json.dumps(h, sort_keys=True) for h in a) == \
           sorted(json.dumps(h, sort_keys=True) for h in b)


def run_ticker(ticker: str, url: str, today: date, force_refetch: bool) -> dict | None:
    """Snapshot + diff for one ticker. Returns result dict or None if no prior snapshot."""
    as_of = ""
    todays_holdings = None if force_refetch else load_snapshot(today, ticker)
    if todays_holdings is None:
        print(f"Fetching {ticker} holdings for {today}...")
        as_of, todays_holdings = fetch_holdings(url)

        # Don't save if data is unchanged from prior snapshot (e.g. holiday/market closed)
        prior_date_check, prior_check = find_prior_snapshot(today, ticker)
        if prior_check is not None and _holdings_identical(todays_holdings, prior_check):
            print(f"  Holdings unchanged from {prior_date_check} (likely holiday) — skipping save.")
            return None

        path = save_snapshot(todays_holdings, as_of, today, ticker)
        print(f"  Saved {len(todays_holdings)} holdings -> {path.name}")
    else:
        snap = load_snapshot_full(today, ticker)
        if snap:
            as_of = snap.get("as_of", "")
        print(f"Loaded cached snapshot for {ticker} {today} ({len(todays_holdings)} holdings).")

    prior_date, prior_holdings = find_prior_snapshot(today, ticker)

    if prior_holdings is None:
        print(f"  No prior snapshot found for {ticker} (checked last 10 days). Run again tomorrow to see changes.\n")
        return None

    diff = compare(todays_holdings, prior_holdings)
    print_report(diff, today, prior_date, len(todays_holdings), len(prior_holdings), ticker)

    diff_path = DATA_DIR / f"{ticker.lower()}_diff_{prior_date}_to_{today}.json"
    diff_path.write_text(json.dumps(diff, indent=2))
    print(f"  Diff saved -> {diff_path.name}")

    return {
        "ticker":      ticker,
        "diff":        diff,
        "prior_date":  prior_date,
        "today_count": len(todays_holdings),
        "prior_count": len(prior_holdings),
        "as_of":       as_of,
    }


def main():
    today = date.today()

    if "--date" in sys.argv:
        idx   = sys.argv.index("--date")
        today = date.fromisoformat(sys.argv[idx + 1])

    force_refetch = "--force" in sys.argv

    ticker_results = []
    for ticker, url in TICKERS.items():
        result = run_ticker(ticker, url, today, force_refetch)
        if result:
            ticker_results.append(result)

    if not ticker_results:
        return

    if "--no-html" not in sys.argv:
        for r in ticker_results:
            html_path = write_html_report(
                r["diff"], today, r["prior_date"],
                r["today_count"], r["prior_count"], r["as_of"], r["ticker"],
            )
            print(f"  HTML report -> {html_path.name}")

        combined_path = write_combined_html(ticker_results, today)
        print(f"  Combined report -> {combined_path}")

        if "--no-browser" not in sys.argv:
            webbrowser.open(combined_path.as_uri())


if __name__ == "__main__":
    main()
