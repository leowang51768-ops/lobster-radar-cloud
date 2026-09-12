#!/usr/bin/env python3
"""Update fundamental_support.json from official TWSE/TPEx monthly revenue OpenAPI.

Layer-1 fundamental hard gate:
  supported = latest monthly revenue YoY > 0 OR cumulative revenue YoY > 0

Industry trend, analyst views, conference-call evidence, institutions and broker
branches remain ranking/bonus evidence, not additional hard gates.

Operational resilience:
- Retry transient HTTP/stream failures.
- TPEx may require an SSL-verification fallback on GitHub-hosted runners.
- If a market source is still unavailable, reuse the last good cached rows for that
  market from fundamental_support.json instead of stopping the whole buy-signal run.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.exceptions import SSLError
import urllib3

BASE = Path(__file__).resolve().parent
OUT = BASE / "fundamental_support.json"

SOURCES = [
    ("上市", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"),
    ("上櫃", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"),
]


def clean_number(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("％", "").replace("%", "")
    if s in {"", "-", "--", "---", "N/A", "null", "None"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def first(row, names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def calc_yoy(current, prior):
    a, b = clean_number(current), clean_number(prior)
    if a is None or b in (None, 0):
        return None
    return (a / b - 1.0) * 100.0


def _request_json(url, verify=True):
    headers = {
        "User-Agent": "Mozilla/5.0 LobsterRadar/1.0",
        "Accept": "application/json,text/plain,*/*",
        "Connection": "close",
    }
    r = requests.get(url, headers=headers, timeout=60, verify=verify)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected JSON from {url}: {type(data).__name__}")
    return data


def fetch_json(url, attempts=3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return _request_json(url, verify=True)
        except SSLError as e:
            last_error = e
            if "tpex.org.tw" not in url:
                break
            print(
                f"WARNING: TPEx SSL verification failed on attempt {attempt}; retrying official public endpoint with verification disabled.",
                file=sys.stderr,
            )
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            try:
                return _request_json(url, verify=False)
            except Exception as fallback_error:
                last_error = fallback_error
        except Exception as e:
            last_error = e

        if attempt < attempts:
            time.sleep(attempt * 2)

    raise last_error if last_error else RuntimeError(f"Unable to fetch {url}")


def parse_rows(market, rows):
    out = {}
    for row in rows:
        code = str(first(row, ["公司代號", "公司代碼", "代號", "SecuritiesCompanyCode", "公司代號 "]) or "").strip()
        name = str(first(row, ["公司名稱", "名稱", "CompanyName", "公司簡稱"]) or "").strip()
        if not re.fullmatch(r"\d{4}", code):
            continue

        yoy = clean_number(first(row, [
            "營業收入-去年同月增減(%)",
            "營業收入-去年同月增減％",
            "去年同月增減(%)",
            "去年同月增減百分比",
        ]))
        if yoy is None:
            yoy = calc_yoy(
                first(row, ["營業收入-當月營收", "當月營收"]),
                first(row, ["營業收入-去年當月營收", "去年當月營收", "去年同月營收"]),
            )

        cumulative_yoy = clean_number(first(row, [
            "累計營業收入-前期比較增減(%)",
            "累計營業收入-去年同期增減(%)",
            "累計營收-前期比較增減(%)",
            "累計去年同期增減(%)",
        ]))
        if cumulative_yoy is None:
            cumulative_yoy = calc_yoy(
                first(row, ["累計營業收入-當月累計營收", "當月累計營收", "累計營收"]),
                first(row, ["累計營業收入-去年累計營收", "去年累計營收", "去年同期累計營收"]),
            )

        revenue_month = first(row, ["資料年月", "年月", "RevenueYearMonth"])
        supported = bool((yoy is not None and yoy > 0) or (cumulative_yoy is not None and cumulative_yoy > 0))

        reason_bits = []
        if yoy is not None:
            reason_bits.append(f"月營收YoY {yoy:.2f}%")
        if cumulative_yoy is not None:
            reason_bits.append(f"累計營收YoY {cumulative_yoy:.2f}%")
        if not reason_bits:
            reason_bits.append("官方月營收資料可讀，但YoY欄位無法計算")

        out[code] = {
            "supported": supported,
            "name": name,
            "market": market,
            "monthly_revenue_yoy_pct": round(yoy, 2) if yoy is not None else None,
            "cumulative_revenue_yoy_pct": round(cumulative_yoy, 2) if cumulative_yoy is not None else None,
            "revenue_month": str(revenue_month).strip() if revenue_month is not None else None,
            "reason": "；".join(reason_bits),
            "source": "official monthly revenue OpenAPI",
            "stale": False,
        }
    return out


def load_cache():
    if not OUT.exists():
        return {"stocks": {}}
    try:
        raw = json.loads(OUT.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"stocks": {}}
    except Exception:
        return {"stocks": {}}


def cached_market_rows(cache, market):
    rows = {}
    for code, item in (cache.get("stocks") or {}).items():
        if isinstance(item, dict) and item.get("market") == market:
            cloned = dict(item)
            cloned["stale"] = True
            cloned["source"] = str(cloned.get("source") or "official monthly revenue OpenAPI") + " (cached last good)"
            rows[str(code)] = cloned
    return rows


def main() -> int:
    cache = load_cache()
    all_stocks = {}
    source_counts = {}
    source_status = {}

    for market, url in SOURCES:
        try:
            rows = fetch_json(url)
            parsed = parse_rows(market, rows)
            if not parsed:
                raise RuntimeError("parsed 0 four-digit stocks")
            all_stocks.update(parsed)
            source_counts[market] = len(parsed)
            source_status[market] = "fresh"
            print(f"{market}: raw={len(rows)}, parsed={len(parsed)}")
        except Exception as e:
            cached = cached_market_rows(cache, market)
            if cached:
                all_stocks.update(cached)
                source_counts[market] = len(cached)
                source_status[market] = "cached"
                print(f"WARNING {market}: {e!r}; using cached last-good rows={len(cached)}", file=sys.stderr)
            else:
                raise SystemExit(f"Fundamental update aborted; {market} unavailable and no cached data: {e!r}")

    # Require broad whole-market coverage even when one source is cached.
    if len(all_stocks) < 1500:
        raise SystemExit(f"Fundamental update aborted; only {len(all_stocks)} stocks available")

    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "updated_at_utc": now,
        "rule": "supported if latest monthly revenue YoY > 0 OR cumulative revenue YoY > 0",
        "source_counts": source_counts,
        "source_status": source_status,
        "stocks": all_stocks,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    supported_count = sum(1 for v in all_stocks.values() if v.get("supported"))
    stale_count = sum(1 for v in all_stocks.values() if v.get("stale"))
    print(json.dumps({
        "stocks": len(all_stocks),
        "supported": supported_count,
        "unsupported": len(all_stocks) - supported_count,
        "stale_rows": stale_count,
        "source_counts": source_counts,
        "source_status": source_status,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
