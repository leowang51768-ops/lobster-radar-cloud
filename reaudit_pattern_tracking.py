from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pandas as pd

import buy_signal
import support_retest_watch
import vcp_scan
from combined_line_digest import (
    true_breakout_threshold_info,
    nearest_upper_resistance,
)

BASE = Path(__file__).resolve().parent
DB = BASE / "lobster_tw_6m_prices.sqlite"
TRACKING = BASE / "breakout_tracking.json"
WATCHLIST = BASE / "breakout_watchlist.json"
AUDIT = BASE / "pattern_definition_reaudit.json"


def num(v, default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def tick(price):
    price=num(price)
    return .01 if price<10 else .05 if price<50 else .1 if price<100 else .5 if price<500 else 1 if price<1000 else 5


def floor_tick(price):
    price=max(0.0,num(price))
    u=tick(price)
    return max(0.0, math.floor((price+1e-9)/u)*u)


def load_market(con, code, through_day):
    q=("SELECT date,market,stock_id AS code,stock_name AS name,open,high,low,close,volume,turnover "
       "FROM prices WHERE stock_id=? AND date<=? ORDER BY date")
    g=pd.read_sql_query(q,con,params=(str(code),str(through_day)))
    if g.empty:
        return g
    g["date"]=pd.to_datetime(g["date"])
    for col in ("open","high","low","close","volume","turnover"):
        g[col]=pd.to_numeric(g[col],errors="coerce")
    g["volume_lots"]=g["volume"]/1000.0
    return g.dropna(subset=["open","high","low","close"])


def validate_n_bottom(g, signal_day):
    row=support_retest_watch.classify_latest(g.copy())
    if not row:
        return None
    if str(row.get("date")) != str(signal_day):
        return None
    if not str(row.get("stage","")).startswith("突破B點"):
        return None
    B=num(row.get("b_neckline"))
    C=num(row.get("c_low"))
    A=num(row.get("a_low"))
    if not (A>0 and B>A and C>A and C<B):
        return None
    return {
        "route":"N字底",
        "stage":"B點當日突破",
        "pivot":B,
        "support":B,
        "a_low":A,
        "b_point":B,
        "c_low":C,
        "stop":floor_tick(B*0.98),
        "volume_ratio":num(row.get("volume_ratio")),
    }


def validate_break_bottom(g, signal_day, code):
    x=buy_signal.prepare(g.copy())
    setup, signal=buy_signal.detect_false_break_reversal(str(code),x)
    if not setup or not signal:
        return None
    if str(signal.get("date")) != str(signal_day):
        return None
    B=num(setup.get("b_point"))
    if B<=0:
        return None
    return {
        "route":"破底翻",
        "stage":signal.get("entry_stage") or signal.get("signal_route") or "破底翻",
        "pivot":num(setup.get("neckline") or setup.get("trigger_level")),
        "support":num(setup.get("support_upper") or setup.get("support_lower")),
        "a_lower":num(setup.get("a_point_lower")),
        "a_upper":num(setup.get("a_point_upper")),
        "b_point":B,
        "stop":floor_tick(B*0.98),
        "volume_ratio":num(signal.get("volume_ratio")),
    }


def validate_vcp(g, signal_day):
    x=vcp_scan.prepare(g.copy())
    row=vcp_scan.classify_latest(g.copy())
    if not row:
        return None
    if str(row.get("date")) != str(signal_day):
        return None
    if str(row.get("vcp_stage")) != "當日突破":
        return None
    pivot=num(row.get("pivot"))
    if pivot<=0:
        return None
    return {
        "route":"VCP",
        "stage":"當日突破",
        "pivot":pivot,
        "support":pivot,
        "stop":floor_tick(pivot*0.98),
        "volume_ratio":num(row.get("volume_ratio")),
        "contraction_count":int(num(row.get("contraction_count"))),
        "contraction_depths_pct":row.get("contraction_depths_pct"),
    }


def validate_signal(con, old):
    day=str(old.get("signal_date") or old.get("created_date") or "")
    code=str(old.get("code") or "")
    if not day or not code:
        return None
    g=load_market(con,code,day)
    if g.empty:
        return None
    route=str(old.get("route") or "")
    if route=="N字底":
        return validate_n_bottom(g,day)
    if route=="破底翻":
        return validate_break_bottom(g,day,code)
    if route=="VCP":
        return validate_vcp(g,day)
    return None


def update_support_distance(item, close):
    if item.get("status")=="true_breakout":
        support=num(item.get("new_support") or item.get("candidate_new_support") or item.get("true_breakout_threshold"))
        status=item.get("new_support_status") or "候選新支撐"
    else:
        support=num(item.get("confirmed_support") or item.get("retest_support"))
        status=item.get("support_status") or "候選支撐"
    item["current_support"]=round(support,4) if support>0 else None
    item["current_support_status"]=status
    item["distance_to_support_pct"]=round((close/support-1.0)*100,4) if close>0 and support>0 else None


def replay(con, old, valid, latest_day):
    signal_day=str(old.get("signal_date") or old.get("created_date"))
    code=str(old["code"])
    g=load_market(con,code,latest_day)
    if g.empty:
        return None
    dates=[d.strftime("%Y-%m-%d") for d in g["date"]]
    if signal_day not in dates:
        return None
    sidx=dates.index(signal_day)
    signal_row=g.iloc[sidx]
    close0=num(signal_row.close)
    pivot=num(valid["pivot"])
    support=num(valid["support"])
    stop=num(valid["stop"])
    threshold_info=true_breakout_threshold_info(con,code,signal_day,pivot,close0)
    true_threshold=num(threshold_info.get("threshold"))

    item=dict(old)
    item.update({
        "tracking_key":f"{signal_day}|{code}",
        "signal_date":signal_day,
        "route":valid["route"],
        "stage":valid["stage"],
        "breakout_close":round(close0,4),
        "pivot":round(pivot,4),
        "retest_support":round(support,4),
        "stop_price":round(stop,4),
        "true_breakout_threshold":round(true_threshold,4) if true_threshold>0 else None,
        "true_breakout_threshold_source":threshold_info.get("source"),
        "price_discovery":bool(threshold_info.get("price_discovery")),
        "upper_resistance_at_signal":threshold_info.get("upper_resistance_at_signal"),
        "upper_resistance_touches_at_signal":threshold_info.get("upper_resistance_touches_at_signal"),
        "support_status":"候選支撐",
        "support_confirmed":False,
        "confirmed_support":None,
        "candidate_new_support":None,
        "new_support":None,
        "new_support_status":None,
        "status":"tracking",
        "status_label":"🟧 突破",
        "created_date":signal_day,
        "last_checked_date":signal_day,
        "tracking_day":0,
        "resolved_date":None,
        "resolved_close":None,
        "definition_reaudit":"2026-10-08-new-pattern-definitions",
    })
    for k,v in valid.items():
        if k not in ("route","stage","pivot","support","stop"):
            item[k]=v
    update_support_distance(item,close0)

    max_idx=min(len(g)-1,sidx+9)
    for idx in range(sidx+1,max_idx+1):
        row=g.iloc[idx]
        day=row.date.strftime("%Y-%m-%d")
        low,close,vol=num(row.low),num(row.close),num(row.volume)
        age=idx-sidx
        item["last_checked_date"]=day
        item["last_low"]=low
        item["last_close"]=close
        item["last_volume"]=vol
        item["tracking_day"]=age

        if stop>0 and close<stop:
            item["status"]="invalid"
            item["status_label"]="⛔ 型態失效"
            item["resolved_date"]=day
            item["resolved_close"]=close
            update_support_distance(item,close)
            break

        if item.get("status")=="true_breakout":
            ns=num(item.get("candidate_new_support") or true_threshold)
            if ns>0:
                item["candidate_new_support"]=round(ns,4)
                item["new_support_status"]=item.get("new_support_status") or "候選新支撐"
            if ns>0 and close<ns:
                item["status"]="post_true_retest_failed"
                item["status_label"]="🟣 真突破後回測失敗"
                item["new_support_status"]="候選新支撐失敗"
                item["resolved_date"]=day
                item["resolved_close"]=close
                update_support_distance(item,close)
                break
            if ns>0 and low<=ns*1.01 and close>=ns:
                item["new_support"]=round(ns,4)
                item["new_support_status"]="已確認新支撐"
                item["status"]="post_true_retest_success"
                item["status_label"]="🟦 真突破後回測成功"
                item["resolved_date"]=day
                item["resolved_close"]=close
                update_support_distance(item,close)
                break
            update_support_distance(item,close)
            if age>=9:
                item["monitoring_complete"]=True
                item["status_label"]="🔵 真突破｜D10完成"
                item["resolved_date"]=day
                item["resolved_close"]=close
            continue

        if support<=0:
            continue
        if close<support:
            item["status"]="retest_failed" if close>=support*0.99 else "failed"
            item["status_label"]="🟤 回測失敗" if item["status"]=="retest_failed" else "🔴 突破失敗"
            item["resolved_date"]=day
            item["resolved_close"]=close
            update_support_distance(item,close)
            break

        if low<=support*1.01:
            item["support_status"]="已確認支撐"
            item["support_confirmed"]=True
            item["confirmed_support"]=round(support,4)
            item["status"]="confirmed"
            item["status_label"]="🟢 回測確認"
            item["resolved_date"]=day
            item["resolved_close"]=close
            update_support_distance(item,close)
            break

        item["gain_vs_breakout_pct"]=round((close/close0-1.0)*100,4) if close0>0 else None
        if age>=1 and true_threshold>0 and close>=true_threshold:
            item["status"]="true_breakout"
            item["status_label"]="🔵 真突破"
            item["true_breakout_date"]=day
            item["true_breakout_close"]=close
            item["candidate_new_support"]=round(true_threshold,4)
            item["new_support_status"]="候選新支撐"
            upper_info=nearest_upper_resistance(con,code,day,pivot,close)
            upper=(upper_info or {}).get("price") if upper_info else None
            touches=int((upper_info or {}).get("touches",0)) if upper_info else 0
            item["upper_resistance"]=round(upper,2) if upper is not None else None
            item["upper_resistance_touches"]=touches
            item["upper_resistance_strength"]="強壓力區" if touches>=4 else "有效壓力" if touches>=3 else "未確認"
            update_support_distance(item,close)
            continue

        if true_threshold>0 and close>=true_threshold*0.98:
            item["status_label"]="🟨 接近真突破"
            item["near_true_breakout"]=True
        elif age>=1:
            item["status_label"]="🟡 突破後盤整"
        update_support_distance(item,close)

        if age>=9 and item.get("status")=="tracking":
            item["status"]="expired"
            item["status_label"]="⚪ D10 未表態"
            item["resolved_date"]=day
            item["resolved_close"]=close
            break

    return item


def main():
    old=json.loads(TRACKING.read_text(encoding="utf-8")) if TRACKING.exists() else {}
    audit={"run_date":"2026-10-08","before":len(old),"kept":[],"deleted":[]}
    new={}
    with sqlite3.connect(DB) as con:
        latest=con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
        for key,item in old.items():
            if not isinstance(item,dict):
                continue
            valid=validate_signal(con,item)
            if not valid:
                audit["deleted"].append({
                    "tracking_key":key,
                    "code":item.get("code"),
                    "name":item.get("name"),
                    "route":item.get("route"),
                    "signal_date":item.get("signal_date"),
                    "reason":"依訊號日以前資料重建後，不符合2026-10-08新版型態定義",
                })
                continue
            rebuilt=replay(con,item,valid,latest)
            if rebuilt is None:
                audit["deleted"].append({"tracking_key":key,"code":item.get("code"),"reason":"無法重建D1-D10"})
                continue
            new[rebuilt["tracking_key"]]=rebuilt
            audit["kept"].append({
                "tracking_key":rebuilt["tracking_key"],
                "code":rebuilt.get("code"),
                "name":rebuilt.get("name"),
                "route":rebuilt.get("route"),
                "signal_date":rebuilt.get("signal_date"),
                "status_label":rebuilt.get("status_label"),
                "pivot":rebuilt.get("pivot"),
                "stop_price":rebuilt.get("stop_price"),
                "current_support":rebuilt.get("current_support"),
                "distance_to_support_pct":rebuilt.get("distance_to_support_pct"),
            })

    # Same stock may have repeated historical signals; tracking keeps all valid history.
    # Live watchlist keeps only the newest unresolved signal per stock.
    active={}
    for item in new.values():
        if item.get("status") not in ("tracking","true_breakout") or item.get("monitoring_complete"):
            continue
        code=str(item.get("code"))
        prev=active.get(code)
        if prev is None or str(item.get("signal_date"))>str(prev.get("signal_date")):
            active[code]=item
    stocks=sorted(active.values(), key=lambda r:(str(r.get("signal_date")),str(r.get("code"))), reverse=True)

    audit["after"]=len(new)
    audit["kept_count"]=len(audit["kept"])
    audit["deleted_count"]=len(audit["deleted"])
    audit["latest_trade_date"]=latest
    TRACKING.write_text(json.dumps(new,ensure_ascii=False,indent=2),encoding="utf-8")
    WATCHLIST.write_text(json.dumps({
        "signal_type":"當日突破候選追蹤池｜新版型態定義重建",
        "generated_at":"2026-10-08T00:00:00+08:00",
        "count":len(stocks),
        "stocks":stocks,
    },ensure_ascii=False,indent=2),encoding="utf-8")
    AUDIT.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "before":audit["before"],
        "kept":audit["kept_count"],
        "deleted":audit["deleted_count"],
        "live_watchlist":len(stocks),
        "latest_trade_date":latest,
    },ensure_ascii=False))


if __name__=="__main__":
    main()
