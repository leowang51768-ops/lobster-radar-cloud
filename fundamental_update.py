#!/usr/bin/env python3
"""Update fundamental_support.json from official TWSE/TPEx monthly revenue OpenAPI.

First-version hard gate for Layer 1:
  supported = latest monthly revenue YoY > 0 OR cumulative revenue YoY > 0

Industry trend, analyst views, conference-call evidence, institutions and broker
branches remain ranking/bonus evidence, not additional hard gates.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

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


def fetch_json(url):
    headers = {
        "User-Agent": "Mozilla/5.0 LobsterRadar/1.0",
        "Accept": "application/json,text/plain,*/*",
    }
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected JSON from {url}: {type(data).__name__}")
    return data


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
        }
    return out


def main() -> int:
    all_stocks = {}
    source_counts = {}
    errors = []

    for market, url in SOURCES:
        try:
            rows = fetch_json(url)
            parsed = parse_rows(market, rows)
            if not parsed:
                raise RuntimeError("parsed 0 four-digit stocks")
            all_stocks.update(parsed)
            source_counts[market] = len(parsed)
            print(f"{market}: raw={len(rows)}, parsed={len(parsed)}")
        except Exception as e:
            errors.append(f"{market}: {e!r}")
            print(f"ERROR {market}: {e!r}", file=sys.stderr)

    # Do not overwrite a good file with a partial/empty result.
    if errors or len(all_stocks) < 1000:
        raise SystemExit("Fundamental update aborted; " + " | ".join(errors or [f"only {len(all_stocks)} stocks parsed"]))

    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "updated_at_utc": now,
        "rule": "supported if latest monthly revenue YoY > 0 OR cumulative revenue YoY > 0",
        "source_counts": source_counts,
        "stocks": all_stocks,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    supported_count = sum(1 for v in all_stocks.values() if v.get("supported"))
    print(json.dumps({
        "stocks": len(all_stocks),
        "supported": supported_count,
        "unsupported": len(all_stocks) - supported_count,
        "source_counts": source_counts,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
