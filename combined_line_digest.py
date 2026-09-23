#!/usr/bin/env python3
"""One daily LINE stock digest, at most five DISTINCT tickers across 3 strategies.

Reads completed scanner CSVs; does not alter strategy eligibility or performance
records. Today's verified breakout quality ranks before reward/risk. Observe-only
signals never become formal buys by being included in this digest.
"""
from __future__ import annotations
import csv
import json
import math
import os
import sqlite3
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
MAX_STOCKS = 5


def rows(filename, day):
    path = BASE / filename
    if not path.exists():
        raise FileNotFoundError(f"Required scanner output missing: {filename}")
    with path.open(encoding="utf-8-sig", newline="") as file:
        return [r for r in csv.DictReader(file) if r.get("date") == day]


def number(value, default=0.0):
    try:
        n = float(value)
        return n if math.isfinite(n) else default
    except (ValueError, TypeError):
        return default


def market_date():
    with sqlite3.connect(BASE / "lobster_tw_6m_prices.sqlite") as con:
        value = con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    if not value:
        raise RuntimeError("No dated price records")
    return str(value)[:10]



def tick(price):
    """Tick step for the ordinary TW equity price bands."""
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


def attach_ma20_stops(day, selected):
    """Use completed trade-day MA20 and the immediately lower valid price tick.

    Only changes LINE display, not the immutable structural stop used for
    existing formal recommendation records and their performance history.
    """
    if not selected:
        return
    with sqlite3.connect(BASE / "lobster_tw_6m_prices.sqlite") as con:
        for item in selected:
            closes = con.execute(
                "SELECT close FROM prices WHERE stock_id=? AND date<=? "
                "ORDER BY date DESC LIMIT 20", (item["code"], day)
            ).fetchall()
            if len(closes) < 20:
                item["ma20"] = None
                item["stop20"] = None
                item["ma20_note"] = "20日收盤資料不足"
                continue
            ma20 = sum(float(r[0]) for r in closes) / 20
            if not math.isfinite(ma20) or ma20 <= 0:
                item["ma20"] = None
                item["stop20"] = None
                item["ma20_note"] = "20MA資料異常"
                continue
            # Prices below 20MA are rounded DOWN to the legal equity tick,
            # never rounded to 20MA itself or to an above-MA quote.
            unit = tick(ma20)
            lower = math.floor(round(ma20 / unit, 10)) * unit
            if lower >= ma20 - 1e-8:
                lower -= unit
            # Re-evaluate tick around a band boundary, conservatively downward.
            lower = round(lower, 2)
            while lower > 0 and (lower >= ma20 or
                    abs(round(lower/tick(lower))*tick(lower)-lower)>1e-7):
                lower = round(lower - tick(lower), 2)
            item["ma20"] = round(ma20, 2)
            item["stop20"] = lower if lower > 0 else None
            item["ma20_note"] = (
                "收盤未站上20MA，無有效20MA下方多單停損參考"
                if item["close"] <= ma20 else ""
            )


def collect(day):
    candidates = []
    # A break-bottom C-point buy is not itself a neckline breakout.
    # Only date-verified neckline breaks receive the breakout priority.
    for r in rows("candidate_status.csv", day):
        if r.get("pattern") != "破底翻":
            continue
        formal = r.get("status") == "正式買點"
        breakout = r.get("neckline_status") == "當日收盤突破壓力頸線（量比≥1.2）"
        if not (formal or breakout):
            continue
        rr = number(r.get("risk_reward"), -1)
        candidates.append(dict(
            code=r["code"], name=r["name"], route="破底翻",
            stage="頸線當日突破" if breakout else "C點／結構買點",
            day=day if breakout else "", close=number(r.get("close")),
            pivot=number(r.get("neckline")) if breakout else number(r.get("trigger_level")),
            volume_ratio=number(r.get("volume_ratio")),
            rr=rr, quality=number(r.get("close_location")),
            stop=number(r.get("failure_level") or r.get("stop_price")),
            status="正式買點" if formal else "僅觀察",
            breakout=breakout, within=bool(formal),
        ))
    for r in rows("vcp_candidates.csv", day):
        stage=r.get("vcp_stage", "")
        breakout=stage == "當日突破"
        if stage not in ("當日突破", "突破後回踩"):
            continue  # Do not crowd five slots with pre-breakout watch candidates.
        candidates.append(dict(
            code=r["code"], name=r["name"], route="VCP", stage=stage,
            day=day if breakout else "",close=number(r.get("close")),
            pivot=number(r.get("pivot")), volume_ratio=number(r.get("volume_ratio")),
            rr=number(r.get("reward_risk_ratio"), -1),
            quality=number(r.get("quality_score"))/100,
            stop=number(r.get("stop_price")), status="僅觀察",
            breakout=breakout, within=number(r.get("distance_to_pivot_pct"),999)<=3,
        ))
    for r in rows("n_bottom_watch.csv", day):
        if not r.get("stage", "").startswith("突破B點"):
            continue  # No C-point / near-neckline rows in top-five breakout digest.
        close=number(r.get("close"))
        pivot=number(r.get("b_neckline"))
        stop=number(r.get("stop_price"))
        # N-bottom has no validated historical target: keep RR unknown rather
        # than fabricate a numeric reward from an arbitrary target.
        candidates.append(dict(
            code=r["code"],name=r["name"],route="N字底",stage="B點當日突破",
            day=day, close=close,pivot=pivot,
            volume_ratio=number(r.get("volume_ratio")),rr=-1,
            quality=1.0 if close>pivot and close<=number(r.get("entry_upper")) else 0.0,
            stop=stop,status="試單區內（待風險驗證）" if r.get("entry_status")=="正式試單" else "僅觀察／已超試單區",
            breakout=True, within=r.get("entry_status")=="正式試單",
        ))
    return candidates


def rank(candidate):
    # Scheme B: current-day confirmed price/volume breakout first. Nonextended
    # position and volume quality follow; RR breaks ties, unknown RR ranks last.
    # Do not use nominal stock price as a ranking criterion.
    return (
        -int(candidate["breakout"]),
        -int(candidate["within"]),
        -min(max(candidate["volume_ratio"],0),3),
        -candidate["quality"],
        -candidate["rr"],
        candidate["code"],
    )


def choose(candidates, limit=MAX_STOCKS):
    selected=[]
    seen=set()
    for r in sorted(candidates,key=rank):
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        selected.append(r)
        if len(selected)==limit:
            break
    return selected


def format_message(day, selected, count):
    lines=[f"🦞 龍蝦雷達｜三策略合併精選｜{day}",
           f"突破品質優先｜最多{MAX_STOCKS}檔｜候選訊號{count}筆（同股去重）",
           "觀察與正式買點分開標示；非當日突破不得標示突破日。"]
    if not selected:
        lines.append("當日無符合突破／買點通知條件的股票。")
    for i,r in enumerate(selected,1):
        date_label=(f"🚀 突破日：{r['day']}｜當日收盤確認" if r["breakout"]
                    else f"觀察日：{day}｜非當日突破")
        rr_label=f"{r['rr']:.2f}" if r["rr"] >= 0 else "未驗證"
        stop_label = (
            f"當日20MA{r['ma20']:g}｜20MA下方一檔停損參考{r['stop20']:g}"
            if r.get("stop20") is not None
            else f"20MA停損無法計算：{r.get('ma20_note', '資料不足')}"
        )
        note = f"｜⚠️{r['ma20_note']}" if r.get("ma20_note") else ""
        lines.append(
            f"\n{i}. {r['code']} {r['name']}｜{r['route']}｜{r['stage']}"
            f"\n{date_label}"
            f"\n收盤{r['close']:g}｜突破樞紐／觸發價{r['pivot']:g}"
            f"｜量比{r['volume_ratio']:.2f}x（破底翻20日基準；VCP／N字底5日基準）"
            f"\n{stop_label}｜原結構停損{r['stop']:g}"
            f"\n原風報比{rr_label}（非20MA停損重算）｜{r['status']}{note}"
        )
    message="\n".join(lines)
    if len(message)>4900:
        raise RuntimeError("LINE digest exceeds safe 4900-character cap")
    return message


def main():
    day=market_date()
    candidates=collect(day)
    selected=choose(candidates)
    print(json.dumps({"date":day,"candidate_signals":len(candidates),
                      "unique_selected":len(selected),
                      "selected":[r["code"] for r in selected]},ensure_ascii=False))
    attach_ma20_stops(day, selected)
    message=format_message(day,selected,len(candidates))
    token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN","").strip()
    if not token:
        print("Combined LINE token missing; preview only")
        print(message)
        return 0
    if not selected and os.getenv("COMBINED_FORCE_NOTIFY")!="1":
        print("No qualified entries; LINE skipped")
        return 0
    payload=json.dumps({"messages":[{"type":"text","text":message}]},ensure_ascii=False).encode("utf-8")
    request=urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=payload,method="POST",
        headers={"Authorization":f"Bearer {token}",
                 "Content-Type":"application/json; charset=UTF-8"})
    with urllib.request.urlopen(request,timeout=30) as response:
        print("Combined LINE status:",response.status)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
