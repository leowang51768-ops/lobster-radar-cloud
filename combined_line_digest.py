#!/usr/bin/env python3
"""One daily LINE stock digest, at most ten DISTINCT tickers across 3 strategies.

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
MAX_STOCKS = 10
DIAGNOSTICS = BASE / "line_scan_diagnostics.csv"
DIAG_FIELDS = ["date", "code", "name", "route", "stage", "close", "entry_lower", "entry_upper", "entry_excess_pct", "stop_price", "stop_distance_pct", "risk_ok", "within_entry", "line_eligible", "selected", "exclusion_reasons"]


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



# Entry requires a valid original structural stop, but no maximum stop-distance.
# Keep the actual distance visible so users can judge position risk.


def apply_structure_risk(candidate):
    close = number(candidate.get("close"))
    stop = number(candidate.get("stop"))
    if close <= 0 or stop <= 0 or stop >= close:
        candidate["risk_pct"] = None
        candidate["risk_ok"] = False
        candidate["within"] = False
        candidate["status"] = "結構停損無效／無法估算風險，僅觀察"
        return candidate
    risk_pct = (close-stop)/close*100
    candidate["risk_pct"] = round(risk_pct, 2)
    candidate["risk_ok"] = True  # Valid structural stop; no percentage cap.
    return candidate


def collect(day):
    candidates = []
    # A break-bottom right-A buy is not itself a neckline breakout.
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
            stage="頸線當日突破" if breakout else "右A（買點）／結構買點",
            day=day if breakout else "", close=number(r.get("close")),
            pivot=number(r.get("neckline")) if breakout else number(r.get("trigger_level")),
            volume_ratio=number(r.get("volume_ratio")),
            rr=rr, quality=number(r.get("close_location")),
            stop=number(r.get("failure_level") or r.get("stop_price")),
            status="正式買點" if formal else "僅觀察",
            breakout=breakout, within=bool(formal),
            entry_lower=number(r.get("trigger_level")) if formal else None,
            entry_upper=None,
            source_fail_reasons="" if formal else "破底翻：僅突破頸線，尚未符合正式結構買點",
            left_a_lower=number(r.get("a_point_lower") or r.get("support_lower")),
            left_a_upper=number(r.get("a_point_upper") or r.get("support_upper")),
            b_low=number(r.get("b_point")),
            right_a_buy=(number(r.get("trigger_level"))
                         if "早期試單" in r.get("entry_stage", "") else None),
        ))
    for r in rows("vcp_candidates.csv", day):
        stage=r.get("vcp_stage", "")
        breakout=stage == "當日突破"
        if stage not in ("當日突破", "突破後回踩"):
            continue  # Pre-breakout observations stay in their scanner CSV.
        candidates.append(dict(
            code=r["code"], name=r["name"], route="VCP", stage=stage,
            day=day if breakout else "",close=number(r.get("close")),
            pivot=number(r.get("pivot")), volume_ratio=number(r.get("volume_ratio")),
            rr=number(r.get("reward_risk_ratio"), -1),
            quality=number(r.get("quality_score"))/100,
            stop=number(r.get("stop_price")), status="僅觀察",
            breakout=breakout, within=number(r.get("distance_to_pivot_pct"),999)<=3 and r.get("line_eligible") == "1",
            entry_lower=None, entry_upper=number(r.get("pivot"))*1.03,
            source_fail_reasons=("VCP：上方壓力目標風報比低於1.5" if number(r.get("reward_risk_ratio"),-1)>=0 and number(r.get("reward_risk_ratio"),-1)<1.5 else ""),
        ))
    for r in rows("n_bottom_watch.csv", day):
        if not r.get("stage", "").startswith("突破B點"):
            continue  # No C-point / near-neckline rows in top-ten breakout digest.
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
            entry_lower=number(r.get("entry_lower")), entry_upper=number(r.get("entry_upper")),
            source_fail_reasons=r.get("entry_fail_reasons", ""),
            zone_lower=r.get("structure_zone_lower", ""),
            zone_upper=r.get("structure_zone_upper", ""),
            a_low=number(r.get("a_low")), c_low=number(r.get("c_low")),
        ))
    return [apply_structure_risk(candidate) for candidate in candidates]


def populate_historical_zones(day, candidates):
    """Estimate a descriptive, recurring historical price band for every route.

    Use only closing prices strictly preceding the signal day. Do not infer
    support from a single price print or change the original entry/stop logic.
    """
    with sqlite3.connect(BASE / "lobster_tw_6m_prices.sqlite") as con:
        prices = {}
        for code in {r["code"] for r in candidates}:
            prices[code] = [
                float(item[0]) for item in con.execute(
                    "SELECT close FROM prices WHERE stock_id=? AND date<? "
                    "ORDER BY date DESC LIMIT 60", (code, day)
                ) if item[0] is not None and float(item[0]) > 0
            ]
    for r in candidates:
        pivot = number(r.get("pivot"))
        r["zone_lower"], r["zone_upper"], r["zone_source"] = None, None, ""
        if pivot <= 0:
            continue
        # Repeated closing-price bands 1.5%-8% below the pattern pivot.
        # A floor requires two distinct historical sessions in a narrow band;
        # this is a heuristic, not chart-confirmed support or an entry trigger.
        levels = sorted(
            p for p in prices[r["code"]]
            if pivot * 0.92 <= p <= pivot * 0.985
        )
        groups = []
        for p in levels:
            matching = next((g for g in groups if abs(p / g["center"] - 1) <= 0.01), None)
            if matching is None:
                groups.append({"values": [p], "center": p})
            else:
                matching["values"].append(p)
                matching["center"] = sorted(matching["values"])[len(matching["values"]) // 2]
        repeated = [g for g in groups if len(g["values"]) >= 2]
        if not repeated:
            continue
        # Prefer the most repeatedly occupied historical price band, then the
        # one nearest the current pivot. Round only for display, not for stops.
        winner = max(repeated, key=lambda g: (len(g["values"]), g["center"]))
        lower = winner["center"]
        if lower < pivot:
            r["zone_lower"] = lower
            r["zone_upper"] = pivot
            r["zone_source"] = "近60日重複收盤價區間（演算法估算）"


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
    """Only actionable price-zone candidates with valid structural stops use LINE slots.

    Every observation, out-of-range entry and over-cap signal stays in its
    originating scanner CSV, rather than displacing qualified candidates.
    """
    selected=[]
    seen=set()
    qualified=(r for r in candidates if r.get("risk_ok") is True
               and r.get("within") is True)
    for r in sorted(qualified,key=rank):
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        selected.append(r)
        if len(selected)==limit:
            break
    return selected



def candidate_diagnosis(candidate):
    """Explain disqualification without changing which stocks are selected."""
    reasons = []
    close = number(candidate.get("close"))
    lower = candidate.get("entry_lower")
    upper = candidate.get("entry_upper")
    risk = candidate.get("risk_pct")
    if not candidate.get("risk_ok"):
        if risk is None:
            reasons.append("結構停損無效或價格資料不足")
        else:
            reasons.append("結構停損無效")
    if upper is not None and number(upper)>0 and close>number(upper):
        excess = (close/number(upper)-1)*100
        reasons.append(f"超出試單區上緣{excess:.2f}%（上緣{number(upper):g}）")
    elif lower is not None and number(lower)>0 and close<number(lower):
        reasons.append(f"低於試單區下緣{(1-close/number(lower))*100:.2f}%（下緣{number(lower):g}）")
    source = candidate.get("source_fail_reasons","")
    if source:
        for item in source.split("；"):
            if item and not (item == "收盤超出試單區上緣" and any("超出試單區" in x for x in reasons)) and not (item == "收盤未達試單區下緣" and any("低於試單區" in x for x in reasons)) and not (item == "停損距離超過8%" and not candidate.get("risk_ok")):
                reasons.append(item)
    if not candidate.get("within") and not reasons:
        reasons.append("其他條件未通過（掃描器未提供細項）")
    return reasons


def write_diagnostics(day, candidates, selected):
    selected_codes = {r["code"] for r in selected}
    rows_out = []
    for r in candidates:
        upper = r.get("entry_upper")
        close = number(r.get("close"))
        excess = round((close/number(upper)-1)*100, 4) if upper is not None and number(upper)>0 and close>number(upper) else ""
        selected_flag = r["code"] in selected_codes and r in selected
        reasons = [] if selected_flag else candidate_diagnosis(r)
        if not selected_flag and r.get("risk_ok") and r.get("within"):
            reasons = ["當日同股去重或超出LINE前十檔名額"]
        rows_out.append(dict(date=day, code=r["code"],name=r["name"],route=r["route"],
                             stage=r["stage"],close=r["close"],
                             entry_lower=r.get("entry_lower") if r.get("entry_lower") is not None else "",
                             entry_upper=upper if upper is not None else "",
                             entry_excess_pct=excess,stop_price=r["stop"],
                             stop_distance_pct=r.get("risk_pct") if r.get("risk_pct") is not None else "",
                             risk_ok=int(bool(r.get("risk_ok"))),within_entry=int(bool(r.get("within"))),
                             line_eligible=int(bool(r.get("risk_ok") and r.get("within"))),
                             selected=int(selected_flag),exclusion_reasons="；".join(reasons)))
    with DIAGNOSTICS.open("w",encoding="utf-8-sig",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=DIAG_FIELDS)
        writer.writeheader()
        writer.writerows(rows_out)
    print("LINE_SCAN_DIAGNOSTICS "+json.dumps({"date":day,"candidates":len(rows_out),
           "selected":len(selected),"excluded":len(rows_out)-len(selected),
           "file":DIAGNOSTICS.name},ensure_ascii=False))
    for r in rows_out:
        if not r["selected"]:
            print("LINE_EXCLUDED "+json.dumps({"code":r["code"],"name":r["name"],
                  "route":r["route"],"reasons":r["exclusion_reasons"]},ensure_ascii=False))
    return rows_out

def format_message(day, selected, count, diagnostic_rows=None):
    lines=[f"🦞 龍蝦雷達｜三策略合併精選｜{day}",
           f"先篩策略買點區與有效結構停損，再依突破品質排序｜最多{MAX_STOCKS}檔｜掃描候選{count}筆",
           "破底翻／VCP用原結構停損；N字底用B點下方2%。停損距離僅顯示、不設8%入選上限；不符試單區者保留CSV。"]
    if diagnostic_rows is not None:
        excluded=[r for r in diagnostic_rows if not r["selected"]]
        risk_count=sum("結構停損無效" in r["exclusion_reasons"] for r in excluded)
        range_count=sum("超出試單區" in r["exclusion_reasons"] or "低於試單區" in r["exclusion_reasons"] for r in excluded)
        other_count=sum(not ("結構停損無效" in r["exclusion_reasons"] or "超出試單區" in r["exclusion_reasons"] or "低於試單區" in r["exclusion_reasons"]) for r in excluded)
        lines.append(f"排除診斷：{len(excluded)}筆未入選｜停損無效{risk_count}｜試單區外{range_count}｜其他{other_count}（原因可重疊；逐檔詳見line_scan_diagnostics.csv）")
    if not selected:
        lines.append("當日無符合策略買點區及有效結構停損的股票；其他候選保留在CSV。")
    for i,r in enumerate(selected,1):
        date_label=(f"🚀 突破日：{r['day']}｜當日收盤確認" if r["breakout"]
                    else f"觀察日：{day}｜非當日突破")
        rr_label=f"{r['rr']:.2f}" if r["rr"] >= 0 else "未驗證"
        stop_label = (f"B點下方2%停損{r['stop']:g}" if r["route"] == "N字底"
                      else f"結構停損{r['stop']:g}")
        risk_label = (
            f"停損距離{r['risk_pct']:.2f}%（無8%入選上限）"
            if r.get("risk_pct") is not None else "停損距離無法計算"
        )
        lo, hi = number(r.get("zone_lower")), number(r.get("zone_upper"))
        pivot_label = "近期突破價（B點）" if r["route"] == "N字底" else "近期突破／觸發價"
        zone_line = (f"\n歷史價位參考區（演算法估算）{lo:g}～{hi:g}｜{pivot_label}{r['pivot']:g}"
                     if lo > 0 and hi == r["pivot"] and lo < hi
                     else f"\n歷史價位參考區未確認｜{pivot_label}{r['pivot']:g}")
        abc_line = (f"\nA底{r['a_low']:g} → B頸線{r['pivot']:g} → C底{r['c_low']:g} → 突破B點"
                    if r["route"] == "N字底" else "")
        if r["route"] == "破底翻":
            left_lo, left_hi = r["left_a_lower"], r["left_a_upper"]
            abc_line = (f"\n左A支撐區{left_lo:g}～{left_hi:g} → B點低點{r['b_low']:g}"
                        + (f" → 右A（買點）{r['right_a_buy']:g}"
                           if r["right_a_buy"] is not None else "｜右A（買點）非本次頸線突破訊號"))
        lines.append(
            f"\n{i}. {r['code']} {r['name']}｜{r['route']}｜{r['stage']}"
            f"\n{date_label}"
            f"\n收盤{r['close']:g}｜" 
            f"{'突破支撐（原壓力價）' if r['breakout'] else '關鍵觸發價'}{r['pivot']:g}"
            f"{abc_line}"
            f"{zone_line}"
            f"｜量比{r['volume_ratio']:.2f}x（破底翻20日基準；VCP／N字底5日基準）"
            f"\n{stop_label}｜{risk_label}"
            f"\n結構風報比{rr_label}｜{r['status']}"
        )
    message="\n".join(lines)
    # Long messages are split into multiple LINE text messages by main().
    return message


def main():
    day=market_date()
    candidates=collect(day)
    selected=choose(candidates)
    populate_historical_zones(day,candidates)
    diagnostic_rows=write_diagnostics(day,candidates,selected)
    print(json.dumps({"date":day,"candidate_signals":len(candidates),
                      "qualified_unique_selected":len(selected),
                      "excluded_observations":len(candidates)-len([r for r in candidates if r.get("risk_ok") is True and r.get("within") is True]),
                      "selected":[r["code"] for r in selected]},ensure_ascii=False))
    message=format_message(day,selected,len(candidates),diagnostic_rows)
    token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN","").strip()
    if not token:
        print("Combined LINE token missing; preview only")
        print(message)
        return 0
    if not selected and os.getenv("COMBINED_FORCE_NOTIFY")!="1":
        print("No qualified entries; LINE skipped")
        return 0
    chunks=[]
    for line in message.split("\n"):
        if len(line)>4900:
            raise RuntimeError("Single LINE line exceeds 4900 characters")
        if chunks and len(chunks[-1])+1+len(line)>4900:
            chunks.append(line)
        elif chunks:
            chunks[-1]+="\n"+line
        else:
            chunks.append(line)
    payload=json.dumps({"messages":[{"type":"text","text":chunk} for chunk in chunks]},ensure_ascii=False).encode("utf-8")
    request=urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=payload,method="POST",
        headers={"Authorization":f"Bearer {token}",
                 "Content-Type":"application/json; charset=UTF-8"})
    with urllib.request.urlopen(request,timeout=30) as response:
        print("Combined LINE status:",response.status)
    # Only the stocks in the successfully accepted daily LINE message become
    # immutable tracking samples; scans and observations do not count.
    from line_pick_tracking import store_notification
    store_notification(day, selected)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
