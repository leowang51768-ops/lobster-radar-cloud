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
from chip_analysis import load_institutional
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
MAX_STOCKS = 5
DIAGNOSTICS = BASE / "line_scan_diagnostics.csv"
SEND_STATE = BASE / "line_daily_send_state.json"
DIAG_FIELDS = ["date", "code", "name", "route", "stage", "close", "entry_lower", "entry_upper", "entry_excess_pct", "stop_price", "stop_distance_pct", "risk_ok", "within_entry", "line_eligible", "breakout_score", "volume_score", "pattern_score", "institutional_score", "composite_score", "foreign_buy", "trust_buy", "institutional_total", "trust_consecutive_days", "selected", "exclusion_reasons", "institutional_available", "chip_data_date", "chip_summary", "chip_missing_reason", "foreign_consecutive_days"]



def load_send_state():
    if not SEND_STATE.exists():
        return {}
    try:
        data = json.loads(SEND_STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print("LINE_SEND_STATE_WARNING " + json.dumps({"error": str(exc)}, ensure_ascii=False))
        return {}


def save_send_state(state):
    # Keep the file small while preserving enough audit history.
    keys = sorted(state.keys())
    if len(keys) > 120:
        for key in keys[:-120]:
            state.pop(key, None)
    SEND_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

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


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))



def pattern_completeness(candidate):
    route = candidate.get("route")
    if route == "破底翻":
        # Evidence count is produced by the break-bottom scanner; six or more
        # independent structure/evidence checks is treated as fully complete.
        return clamp(number(candidate.get("evidence_count")) / 6.0)
    if route == "VCP":
        # VCP scanner quality_score already measures contraction continuity,
        # pivot convergence and final-leg volume drying.
        return clamp(number(candidate.get("vcp_quality")) / 100.0)
    if route == "N字底":
        a, b, c = number(candidate.get("a_low")), number(candidate.get("pivot")), number(candidate.get("c_low"))
        return 1.0 if a > 0 and b > 0 and c > a and c < b and candidate.get("breakout") else 0.5
    return 0.0


def score_candidates(day, candidates):
    """40% breakout quality + 25% volume + 25% pattern + 10% institutions."""
    # Use the same objective daily close-location measure across all 3 routes.
    with sqlite3.connect(BASE / "lobster_tw_6m_prices.sqlite") as con:
        sessions = [row[0] for row in con.execute("SELECT DISTINCT date FROM prices WHERE date<=? ORDER BY date", (day,))]
        ohlc = {}
        for code in {r["code"] for r in candidates}:
            row = con.execute(
                "SELECT open,high,low,close FROM prices WHERE stock_id=? AND date=? LIMIT 1",
                (code, day),
            ).fetchone()
            if row:
                ohlc[code] = tuple(number(x) for x in row)

    inst_cache = load_institutional({r["code"] for r in candidates}, day, sessions)
    for r in candidates:
        op, hi, lo, cl = ohlc.get(r["code"], (0.0, 0.0, 0.0, number(r.get("close"))))
        close_location = clamp((cl - lo) / (hi - lo)) if hi > lo else 0.5
        # 75% of this component is where the stock closed in its daily range;
        # 25% rewards a verified same-day breakout over a non-breakout entry/retest.
        breakout_component = clamp(0.75 * close_location + 0.25 * int(bool(r.get("breakout"))))
        volume_component = clamp(number(r.get("volume_ratio")) / 2.0)
        pattern_component = pattern_completeness(r)

        inst = inst_cache[r["code"]]
        institutional_points = inst["score"]
        r["chip_data_date"] = inst["data_date"]
        r["chip_summary"] = inst["summary"]
        r["chip_missing_reason"] = inst["reason"]
        r["chip_history"] = inst["history"]
        r["foreign_consecutive_days"] = inst["foreign_days"]

        r["breakout_score"] = round(breakout_component * 40.0, 1)
        r["volume_score"] = round(volume_component * 25.0, 1)
        r["pattern_score"] = round(pattern_component * 25.0, 1)
        r["institutional_score"] = round(institutional_points, 1)
        r["composite_score"] = round(
            r["breakout_score"] + r["volume_score"] + r["pattern_score"] + r["institutional_score"], 1
        )
        r["foreign_buy"] = inst["foreign"]
        r["trust_buy"] = inst["trust"]
        r["institutional_total"] = inst["total"]
        r["trust_consecutive_days"] = inst["trust_days"]
        r["institutional_available"] = inst["available"]
    return candidates


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
            evidence_count=number(r.get("evidence_count")),
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
        if stage != "當日突破":
            continue  # First-stage LINE is same-day breakout candidates only. Retests use a separate second-stage digest.
        candidates.append(dict(
            code=r["code"], name=r["name"], route="VCP", stage=stage,
            day=day if breakout else "",close=number(r.get("close")),
            pivot=number(r.get("pivot")), volume_ratio=number(r.get("volume_ratio")),
            rr=number(r.get("reward_risk_ratio"), -1),
            quality=number(r.get("quality_score"))/100,
            vcp_quality=number(r.get("quality_score")),
            stop=number(r.get("stop_price")), status="僅觀察",
            breakout=breakout, within=number(r.get("distance_to_pivot_pct"),999)<=10 and r.get("line_eligible") == "1",
            entry_lower=None, entry_upper=number(r.get("pivot"))*1.10,
            source_fail_reasons=("VCP：上方壓力目標風報比低於1.5" if number(r.get("reward_risk_ratio"),-1)>=0 and number(r.get("reward_risk_ratio"),-1)<1.5 else ""),
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
    # Composite ranking only; strategy eligibility is still decided upstream.
    # Weights: breakout quality 40, volume 25, pattern completeness 25,
    # institutional evidence 10. Negative flow does not veto a stock; missing
    # current/history data postpones notification in choose().
    return (
        -number(candidate.get("composite_score")),
        -number(candidate.get("breakout_score")),
        -number(candidate.get("volume_score")),
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
               and r.get("within") is True
               and r.get("institutional_available") is True)
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
    if candidate.get("institutional_available") is not True:
        reasons.append("籌碼資料未齊：" + candidate.get("chip_missing_reason", "未取得"))
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
        if not selected_flag and r.get("risk_ok") and r.get("within") and r.get("institutional_available"):
            reasons = ["當日同股去重或超出LINE前五檔名額"]
        rows_out.append(dict(date=day, code=r["code"],name=r["name"],route=r["route"],
                             stage=r["stage"],close=r["close"],
                             entry_lower=r.get("entry_lower") if r.get("entry_lower") is not None else "",
                             entry_upper=upper if upper is not None else "",
                             entry_excess_pct=excess,stop_price=r["stop"],
                             stop_distance_pct=r.get("risk_pct") if r.get("risk_pct") is not None else "",
                             risk_ok=int(bool(r.get("risk_ok"))),within_entry=int(bool(r.get("within"))),
                             line_eligible=int(bool(r.get("risk_ok") and r.get("within") and r.get("institutional_available"))),
                             institutional_available=int(bool(r.get("institutional_available"))),
                             chip_data_date=r.get("chip_data_date", ""), chip_summary=r.get("chip_summary", ""),
                             chip_missing_reason=r.get("chip_missing_reason", ""),
                             foreign_consecutive_days=r.get("foreign_consecutive_days", 0),
                             breakout_score=r.get("breakout_score",""), volume_score=r.get("volume_score",""),
                             pattern_score=r.get("pattern_score",""), institutional_score=r.get("institutional_score",""),
                             composite_score=r.get("composite_score",""), foreign_buy=r.get("foreign_buy",""),
                             trust_buy=r.get("trust_buy",""), institutional_total=r.get("institutional_total",""),
                             trust_consecutive_days=r.get("trust_consecutive_days",""),
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
    lines=[f"🦞 龍蝦雷達｜當日突破候選｜{day}",
           f"今天剛出現突破／收復訊號，先列入觀察，不代表一定要買｜最多{MAX_STOCKS}檔｜掃描候選{count}筆",
           "排名權重：突破品質40%＋量比25%＋型態完整度25%＋法人籌碼10%（籌碼影響排序；資料未齊暫緩通知）",
           "破底翻用B點低點下方一檔；VCP用頸線下方2%；N字底用B點下方2%。停損距離僅顯示、不設8%入選上限；不符試單區者保留CSV。"]
    if diagnostic_rows is not None:
        excluded=[r for r in diagnostic_rows if not r["selected"]]
        risk_count=sum("結構停損無效" in r["exclusion_reasons"] for r in excluded)
        range_count=sum("超出試單區" in r["exclusion_reasons"] or "低於試單區" in r["exclusion_reasons"] for r in excluded)
        other_count=sum(not ("結構停損無效" in r["exclusion_reasons"] or "超出試單區" in r["exclusion_reasons"] or "低於試單區" in r["exclusion_reasons"]) for r in excluded)
        lines.append(f"排除診斷：{len(excluded)}筆未入選｜停損無效{risk_count}｜試單區外{range_count}｜其他{other_count}（原因可重疊；逐檔詳見line_scan_diagnostics.csv）")
    if not selected:
        lines.append("當日無同時符合策略買點區、有效結構停損及籌碼資料完整條件的股票；候選保留在CSV。")
    for i,r in enumerate(selected,1):
        date_label=(f"🚀 突破日：{r['day']}｜當日收盤確認" if r["breakout"]
                    else f"觀察日：{day}｜非當日突破")
        rr_label=f"{r['rr']:.2f}" if r["rr"] >= 0 else "未驗證"
        stop_label = (f"B點下方2%停損{r['stop']:g}" if r["route"] == "N字底"
                      else f"頸線下方2%停損{r['stop']:g}" if r["route"] == "VCP"
                      else f"結構停損{r['stop']:g}")
        risk_label = (
            f"停損距離{r['risk_pct']:.2f}%（無8%入選上限）"
            if r.get("risk_pct") is not None else "停損距離無法計算"
        )
        lo, hi = number(r.get("zone_lower")), number(r.get("zone_upper"))
        pivot_label = "近期突破價（B點）" if r["route"] == "N字底" else "近期突破／觸發價"
        zone_line = (f"\n買點價位參考區（演算法估算，非策略試單區）{lo:g}～{hi:g}｜{pivot_label}{r['pivot']:g}"
                     if lo > 0 and hi == r["pivot"] and lo < hi
                     else f"\n買點價位參考區未確認｜{pivot_label}{r['pivot']:g}")
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
            f"\n綜合分數{r.get('composite_score',0):.1f}｜突破{r.get('breakout_score',0):.1f}/40｜量比{r.get('volume_score',0):.1f}/25｜型態{r.get('pattern_score',0):.1f}/25｜法人{r.get('institutional_score',0):.1f}/10"
            f"\n🧩 籌碼：{r.get('chip_summary', '資料未齊，暫不判讀')}"
            f"\n結構風報比{rr_label}｜{r['status']}"
        )
    message="\n".join(lines)
    # Long messages are split into multiple LINE text messages by main().
    return message



TRACKING_PATH = BASE / "breakout_tracking.json"
WATCHLIST_PATH = BASE / "breakout_watchlist.json"


def load_breakout_tracking():
    if not TRACKING_PATH.exists():
        return {}
    try:
        data=json.loads(TRACKING_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data,dict) else {}
    except Exception as exc:
        print("BREAKOUT_TRACKING_WARNING "+json.dumps({"error":str(exc)},ensure_ascii=False))
        return {}


def save_breakout_tracking(state):
    TRACKING_PATH.write_text(
        json.dumps(state,ensure_ascii=False,indent=2),
        encoding="utf-8",
    )


def tracking_support(pick):
    if pick.get("route")=="破底翻":
        return number(pick.get("left_a_upper"))
    return number(pick.get("pivot"))


def nearest_upper_resistance(con, code, day, pivot, close, lookback=60):
    """Nearest repeated local-high cluster above current price, using only prior sessions."""
    rows_=con.execute(
        "SELECT date,high FROM prices WHERE stock_id=? AND date<? ORDER BY date DESC LIMIT ?",
        (code,day,lookback),
    ).fetchall()
    rows_=list(reversed(rows_))
    if len(rows_)<5:
        return None
    highs=[number(r[1]) for r in rows_]
    peaks=[]
    for pos in range(2,len(highs)-2):
        price=highs[pos]
        if price>=max(highs[pos-2:pos+3]):
            if not peaks or pos-peaks[-1][0]>=1:
                peaks.append((pos,price))
            elif price>peaks[-1][1]:
                peaks[-1]=(pos,price)

    clusters=[]
    for peak_i,price in peaks:
        matches=[c for c in clusters if abs(price/c["center"]-1.0)<=0.01]
        if matches:
            c=min(matches,key=lambda x:abs(price/x["center"]-1.0))
            c["touches"].append((peak_i,price))
            vals=sorted(p for _,p in c["touches"])
            n=len(vals)
            c["center"]=vals[n//2] if n%2 else (vals[n//2-1]+vals[n//2])/2
        else:
            clusters.append({"center":price,"touches":[(peak_i,price)]})

    floor=max(number(pivot)*1.01,number(close))
    valid=[
        (c["center"],len(c["touches"])) for c in clusters
        if len(c["touches"])>=3 and c["center"]>floor
    ]
    if not valid:
        return None
    center,touches=min(valid,key=lambda x:x[0])
    return {"price":center,"touches":touches}


def update_breakout_tracking(day, selected):
    """Track D1-D10 outcomes with the same taxonomy used by Wall Street Ghost V1.4.

    Internal age is zero-based (signal day=0 / visible D1).
    A true breakout remains active through D10 so a later retest of the new
    support (the true-breakout threshold) can be classified.
    """
    today=datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    live=(day==today)
    state=load_breakout_tracking()

    confirmed=[]
    invalidated=[]
    active_before=[
        v for v in state.values()
        if isinstance(v,dict)
        and v.get("status") in ("tracking","true_breakout")
        and not v.get("monitoring_complete")
    ]

    with sqlite3.connect(BASE / "lobster_tw_6m_prices.sqlite") as con:
        sessions=[row[0] for row in con.execute(
            "SELECT DISTINCT date FROM prices WHERE date<=? ORDER BY date",(day,)
        )]
        pos={d:i for i,d in enumerate(sessions)}

        for item in active_before:
            signal_day=item.get("signal_date","")
            if not signal_day or signal_day>=day:
                continue
            row=con.execute(
                "SELECT low,close,volume FROM prices WHERE stock_id=? AND date=? LIMIT 1",
                (item.get("code"),day),
            ).fetchone()
            if not row:
                continue
            low,close,vol=[number(x) for x in row]
            support=number(item.get("retest_support"))
            stop=number(item.get("stop_price"))
            breakout_close=number(item.get("breakout_close"))
            true_threshold=number(item.get("true_breakout_threshold"))
            if true_threshold<=0 and breakout_close>0:
                true_threshold=breakout_close*1.02
                item["true_breakout_threshold"]=round(true_threshold,4)

            item["last_checked_date"]=day
            item["last_low"]=low
            item["last_close"]=close
            item["last_volume"]=vol
            age=(pos.get(day,0)-pos.get(signal_day,0)) if signal_day in pos else 0
            item["tracking_day"]=age

            # Highest priority: original structural stop invalidates the pattern.
            if stop>0 and close<stop:
                item["status"]="invalid"
                item["status_label"]="⛔ 型態失效"
                item["resolved_date"]=day
                item["resolved_close"]=close
                invalidated.append(dict(item))
                continue

            # After true breakout, use the true-breakout threshold as the new support.
            if item.get("status")=="true_breakout":
                new_support=number(item.get("new_support")) or true_threshold
                if new_support>0:
                    item["new_support"]=round(new_support,4)
                if new_support>0 and close<new_support:
                    item["status"]="post_true_retest_failed"
                    item["status_label"]="🟣 真突破後回測失敗"
                    item["resolved_date"]=day
                    item["resolved_close"]=close
                    continue
                if new_support>0 and low<=new_support*1.01 and close>=new_support:
                    item["status"]="post_true_retest_success"
                    item["status_label"]="🟦 真突破後回測成功"
                    item["resolved_date"]=day
                    item["resolved_close"]=close
                    continue
                if age>=9:
                    item["monitoring_complete"]=True
                    item["status_label"]="🔵 真突破｜D10完成"
                    item["resolved_date"]=day
                    item["resolved_close"]=close
                continue

            # Before true breakout, judge the original breakout support.
            if support<=0:
                continue
            if close<support:
                if close>=support*0.99:
                    item["status"]="retest_failed"
                    item["status_label"]="🟤 回測失敗"
                else:
                    item["status"]="failed"
                    item["status_label"]="🔴 突破失敗"
                item["resolved_date"]=day
                item["resolved_close"]=close
                invalidated.append(dict(item))
                continue

            touched=low<=support*1.01
            if touched:
                item["status"]="confirmed"
                item["status_label"]="🟢 回測確認"
                item["resolved_date"]=day
                item["resolved_close"]=close
                breakout_vol=number(item.get("breakout_volume"))
                item["volume_vs_breakout"]=(vol/breakout_vol if breakout_vol>0 else None)
                confirmed.append(dict(item))
                continue

            gain_vs_breakout=((close/breakout_close)-1.0) if breakout_close>0 else 0.0
            item["gain_vs_breakout_pct"]=round(gain_vs_breakout*100,4)
            if age>=1 and true_threshold>0 and close>=true_threshold:
                item["status"]="true_breakout"
                item["status_label"]="🔵 真突破"
                item["true_breakout_date"]=day
                item["true_breakout_close"]=close
                item["new_support"]=round(true_threshold,4)
                upper_info=nearest_upper_resistance(
                    con,item.get("code"),day,item.get("pivot"),close
                )
                upper=(upper_info or {}).get("price") if upper_info else None
                touches=int((upper_info or {}).get("touches",0)) if upper_info else 0
                item["upper_resistance"]=round(upper,2) if upper is not None else None
                item["upper_resistance_touches"]=touches
                item["upper_resistance_strength"]=(
                    "強壓力區" if touches>=4 else "有效壓力" if touches>=3 else "未確認"
                )
                if upper is not None and upper>close:
                    item["upside_amount"]=round(upper-close,2)
                    item["upside_pct"]=round((upper/close-1.0)*100,2)
                else:
                    item["upside_amount"]=None
                    item["upside_pct"]=None
                continue

            if age>=1:
                item["status_label"]="🟡 突破後盤整"
            else:
                item["status_label"]="🟧 突破"

            if age>=9 and item.get("status")=="tracking":
                item["status"]="expired"
                item["status_label"]="⚪ D10 未表態"
                item["resolved_date"]=day
                item["resolved_close"]=close

        # Add today's newly-notified first-stage candidates after older rows.
        for pick in selected:
            key=f"{day}|{pick['code']}"
            if key in state:
                continue

            # One active signal per code: newest signal replaces older active rows.
            for old in state.values():
                if not isinstance(old,dict):
                    continue
                if (old.get("status") in ("tracking","true_breakout")
                        and not old.get("monitoring_complete")
                        and str(old.get("code") or "")==str(pick["code"])
                        and str(old.get("signal_date") or "")<day):
                    old["status"]="expired"
                    old["status_label"]="⚪ 新訊號取代舊追蹤"
                    old["resolved_date"]=day
                    old["resolved_close"]=number(pick.get("close"))

            support=tracking_support(pick)
            close0=number(pick.get("close"))
            volrow=con.execute(
                "SELECT volume FROM prices WHERE stock_id=? AND date=? LIMIT 1",
                (pick["code"],day),
            ).fetchone()
            state[key]={
                "tracking_key":key,
                "signal_date":day,
                "code":pick["code"],
                "name":pick["name"],
                "route":pick["route"],
                "stage":pick["stage"],
                "breakout_close":close0,
                "pivot":number(pick.get("pivot")),
                "retest_support":support,
                "stop_price":number(pick.get("stop")),
                "true_breakout_threshold":round(close0*1.02,4) if close0>0 else None,
                "volume_ratio":number(pick.get("volume_ratio")),
                "composite_score":number(pick.get("composite_score")),
                "breakout_volume":number(volrow[0]) if volrow else 0.0,
                "status":"tracking",
                "status_label":"🟧 突破",
                "created_date":day,
                "last_checked_date":day,
            }

    # Live pool includes unresolved tracking plus true-breakout rows waiting for
    # a possible retest of their new support.
    active_by_code={}
    for value in state.values():
        if not isinstance(value,dict):
            continue
        if value.get("status") not in ("tracking","true_breakout") or value.get("monitoring_complete"):
            continue
        code=str(value.get("code") or "")
        old=active_by_code.get(code)
        if old is None or (str(value.get("signal_date") or ""),str(value.get("tracking_key") or "")) >= (str(old.get("signal_date") or ""),str(old.get("tracking_key") or "")):
            active_by_code[code]=dict(value)
    active=list(active_by_code.values())
    active.sort(key=lambda x:(x.get("signal_date",""),x.get("code","")))

    if live:
        save_breakout_tracking(state)
        payload={
            "signal_type":"當日突破候選追蹤池",
            "generated_at":datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
            "count":len(active),
            "stocks":active,
        }
        WATCHLIST_PATH.write_text(
            json.dumps(payload,ensure_ascii=False,indent=2),
            encoding="utf-8",
        )
    else:
        print(f"Historical replay {day}; breakout tracking/watchlist unchanged")

    print("BREAKOUT_TRACKING "+json.dumps({
        "date":day,
        "active":len(active),
        "confirmed":[x["code"] for x in confirmed],
        "invalid":[x["code"] for x in invalidated],
        "new":[x["code"] for x in selected],
    },ensure_ascii=False))
    return confirmed, invalidated, active


def format_retest_message(day, picks):
    lines=[
        f"🦞 龍蝦雷達｜回測確認買點｜{day}",
        "前面已出現突破候選，之後回測支撐且收盤守住｜屬較穩定的第二階段訊號；買不買仍由盤中情況決定。",
    ]
    for i,r in enumerate(picks[:MAX_STOCKS],1):
        vr=r.get("volume_vs_breakout")
        vol_text=("資料不足" if vr is None else f"{vr:.2f}x"+("（量縮）" if vr<1 else ""))
        lines.append(
            f"\n{i}. {r['code']} {r['name']}｜{r['route']}"
            f"\n原突破日：{r['signal_date']}"
            f"\n今日低點{number(r.get('last_low')):g}｜收盤{number(r.get('last_close')):g}｜回測支撐{number(r.get('retest_support')):g}"
            f"\n今日量／突破日量：{vol_text}"
            f"\n✅ 真突破／回測確認"
        )
    return "\n".join(lines)


def send_line_text(token, message, label):
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
        print(f"{label} LINE status:",response.status)


def main():
    day=market_date()
    candidates=collect(day)
    score_candidates(day,candidates)
    selected=choose(candidates)
    populate_historical_zones(day,candidates)
    diagnostic_rows=write_diagnostics(day,candidates,selected)
    from chip_candidate_tracking import record_candidates, update_outcomes
    record_candidates(day, candidates, selected)
    update_outcomes()
    print(json.dumps({"date":day,"candidate_signals":len(candidates),
                      "qualified_unique_selected":len(selected),
                      "excluded_observations":len(candidates)-len([r for r in candidates if r.get("risk_ok") is True and r.get("within") is True]),
                      "selected":[r["code"] for r in selected]},ensure_ascii=False))

    breakout_message=format_message(day,selected,len(candidates),diagnostic_rows)
    confirmed, invalidated, active=update_breakout_tracking(day,selected)
    retest_message=format_retest_message(day,confirmed) if confirmed else ""

    token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN","").strip()
    if not token:
        print("Combined LINE token missing; preview only")
        print(breakout_message)
        if retest_message:
            print("\n--- SECOND STAGE ---\n"+retest_message)
        return 0

    send_state=load_send_state()
    day_state=send_state.setdefault(day,{})
    sent_breakout=False

    # Always send exactly one first-stage daily result, even when zero stocks qualify.
    # The second 16:45 schedule is a backup run, so persistent state prevents duplicates.
    if not day_state.get("daily_result_sent"):
        send_line_text(token, breakout_message, "Daily breakout result")
        day_state["daily_result_sent"]=datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
        day_state["selected_codes"]=[r["code"] for r in selected]
        save_send_state(send_state)
        sent_breakout=True
    else:
        print(f"Daily LINE result already sent for {day}; duplicate broadcast skipped")

    sent_retest=set(day_state.get("retest_codes") or [])
    fresh_confirmed=[r for r in confirmed if r.get("code") not in sent_retest]
    if fresh_confirmed:
        send_line_text(token, format_retest_message(day,fresh_confirmed), "Retest confirmation")
        sent_retest.update(r.get("code") for r in fresh_confirmed)
        day_state["retest_codes"]=sorted(x for x in sent_retest if x)
        save_send_state(send_state)
    elif confirmed:
        print("Retest confirmations already sent; duplicate second-stage LINE skipped")
    else:
        print("No second-stage retest confirmations; second LINE skipped")

    if invalidated:
        print("INVALID_BREAKOUTS "+json.dumps(
            [{"code":x["code"],"name":x["name"],"support":x["retest_support"],"close":x["last_close"]}
             for x in invalidated],ensure_ascii=False))

    if sent_breakout and selected:
        from line_pick_tracking import store_notification
        store_notification(day, selected)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
