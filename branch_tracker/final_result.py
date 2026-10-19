# trigger: refresh provisional 5-session branch summaries
#!/usr/bin/env python3
"""Build user-facing final branch-tracking conclusions from persisted evidence.

This module never places orders. It summarizes the fixed 163-stock universe,
retrospective 120-session branch behavior, and current accumulation candidates.
"""
from __future__ import annotations
import csv
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, date
import calendar
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = Path(__file__).resolve().parent / "data"
DB = DATA / "branches.sqlite"
TARGET_SESSIONS = 120  # compatibility field; analysis uses six calendar months
HISTORY_MONTHS = 6
from branch_tracker.backfill import append_dated_raw_evidence

def history_start(day):
    d = date.fromisoformat(day)
    month_index = d.year * 12 + d.month - 1 - HISTORY_MONTHS
    year, month = divmod(month_index, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1])).isoformat()

MIN_CONSECUTIVE_BUY_DAYS = 3
MAX_CONSECUTIVE_BUY_DAYS = 15
MIN_3D_NET_AMOUNT = 10_000_000
HORIZONS = (5, 10, 20)


def pct(a, b):
    if a in (None, 0) or b is None:
        return None
    return (b / a - 1) * 100


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def safe_mean(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return statistics.mean(xs) if xs else None


def safe_median(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return statistics.median(xs) if xs else None


def grade(score, samples):
    if samples < 5:
        return "資料不足"
    if score >= 80 and samples >= 10:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "觀察"


def watch_key(code, branch, broker):
    return f"{code}|{branch}|{broker}"


def load_watch_pool():
    path = DATA / "watch_pool.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = raw.get("entries", []) if isinstance(raw, dict) else []
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = watch_key(str(row.get("code") or ""), str(row.get("branch") or ""), str(row.get("broker") or ""))
        if key != "||":
            out[key] = row
    return out


def main():
    if not DB.exists():
        raise RuntimeError("branches.sqlite not found")

    with sqlite3.connect(DB) as db:
        latest_day = db.execute("SELECT MAX(day) FROM official_prices").fetchone()[0]
        if not latest_day:
            raise RuntimeError("official_prices empty")

        prices = {(d, c): (cl, vol) for d, c, cl, vol in db.execute(
            "SELECT day,code,close,volume FROM official_prices WHERE day<=?", (latest_day,)
        )}
        sessions = sorted({d for d, c in prices})
        session_pos = {d: i for i, d in enumerate(sessions)}
        latest_idx = session_pos[latest_day]
        window_start = history_start(latest_day)
        history_sessions = [d for d in sessions if window_start <= d <= latest_day]
        available_sessions = {code: [d for d in history_sessions if (d, code) in prices] for code in {c for _, c in prices}}

        stock_names = {}
        try:
            from stock_universe import STOCKS_TO_TRACK
            stock_names = {k.split(".")[0]: v for k, v in STOCKS_TO_TRACK.items()}
        except Exception:
            pass

        # Merge Ghost manual-add supplemental universe.  This does not alter the
        # fixed radar universe; it only lets branch tracking retain/display those
        # extra names once Ghost has requested them.
        extra_path = DATA / "extra_universe.json"
        try:
            raw_extra = json.loads(extra_path.read_text(encoding="utf-8")) if extra_path.exists() else {}
        except (OSError, ValueError):
            raw_extra = {}
        if isinstance(raw_extra, dict) and isinstance(raw_extra.get("stocks"), dict):
            raw_extra = raw_extra.get("stocks") or {}
        if isinstance(raw_extra, dict):
            for k, v in raw_extra.items():
                code = str(k).split(".")[0]
                if code.isdigit():
                    stock_names[code] = str(v or code)

        returned_path = DATA / "monitor_return_pool.json"
        returned = json.loads(returned_path.read_text(encoding="utf-8")).get("stocks", {}) if returned_path.exists() else {}
        for code, row in returned.items():
            if str(code).isdigit() and isinstance(row,dict):
                stock_names[str(code)] = str(row.get("name") or code)

        # Retrospective history. A branch-stock pair may have fewer than 120 rows
        # while the source backfill is still accumulating; never pad missing days.
        hist = defaultdict(dict)
        meta = {}
        for d, code, branch, broker, buy, sell, total, net in db.execute(
            "SELECT day,code,branch,broker,buy,sell,total,net FROM history ORDER BY day"
        ):
            hist[(code, branch, broker)][d] = {
                "buy": buy, "sell": sell, "total": total, "net": net
            }
        for code, branch, broker, name in db.execute(
            "SELECT code,branch,broker,MAX(name) FROM queue GROUP BY code,branch,broker"
        ):
            meta[(code, branch, broker)] = name

        influence_rows = []
        for key, by_day in hist.items():
            code, branch, broker = key
            valid_days = [d for d in history_sessions
                          if d in by_day and (d, code) in prices]
            buy_days = [d for d in valid_days if by_day[d]["net"] > 0]
            outcome_rows = []
            for d in buy_days:
                i = session_pos[d]
                base = prices[(d, code)][0]
                if not base or base <= 0:
                    continue
                item = {"day": d, "net": by_day[d]["net"]}
                for h in HORIZONS:
                    if i + h <= latest_idx and (sessions[i+h], code) in prices:
                        end = prices[(sessions[i+h], code)][0]
                        item[f"r{h}"] = pct(base, end)
                    else:
                        item[f"r{h}"] = None
                outcome_rows.append(item)

            sample10 = [r["r10"] for r in outcome_rows if r["r10"] is not None]
            sample20 = [r["r20"] for r in outcome_rows if r["r20"] is not None]
            sample5 = [r["r5"] for r in outcome_rows if r["r5"] is not None]
            n = len(sample10)
            if n:
                win10 = sum(r > 0 for r in sample10) / n * 100
                avg10 = safe_mean(sample10) or 0.0
                med10 = safe_median(sample10) or 0.0
                # Conservative descriptive score: sample adequacy + win rate + return.
                sample_score = min(30.0, n / 10 * 30.0)
                win_score = clamp((win10 - 40) / 40 * 40, 0, 40)
                ret_score = clamp((avg10 + 2) / 12 * 30, 0, 30)
                score = sample_score + win_score + ret_score
            else:
                win10 = avg10 = med10 = 0.0
                score = 0.0

            influence_rows.append({
                "code": code,
                "name": stock_names.get(code, ""),
                "branch": branch,
                "broker": broker,
                "branch_name": meta.get(key, ""),
                "history_days": len(valid_days),
                "history_window_start": window_start,
                "available_price_sessions": len(available_sessions.get(code, [])),
                "available_history_start": min(valid_days) if valid_days else None,
                "available_history_end": max(valid_days) if valid_days else None,
                "history_basis": "最近六個月；不足以現有期間分析",
                "buy_days": len(buy_days),
                "d5_samples": len(sample5),
                "d10_samples": len(sample10),
                "d20_samples": len(sample20),
                "d10_win_pct": round(win10, 2) if n else None,
                "d10_avg_return_pct": round(avg10, 2) if n else None,
                "d10_median_return_pct": round(med10, 2) if n else None,
                "influence_score": round(score, 1),
                "grade": grade(score, n),
            })

        # Current branch direction:
        # 1) Active 3-15 day net-buy streaks stay as candidates.
        # 2) If a previously valid buy streak has just turned into net selling,
        #    keep the pair visible so Ghost can warn that the branch is reducing.
        #
        # Direction labels are deliberately compact for the monitor:
        #   🟢加碼 = still buying at a healthy pace
        #   🟡降溫 = still net buying, but latest buy is <50% of prior streak-day average
        #   🟠減碼 = 1-2 recent sell days and <25% of the prior buy streak has been sold back
        #   🔴轉賣 = >=3 sell days or >=25% of the prior buy streak has been sold back
        recent5 = sessions[max(0, latest_idx-4):latest_idx+1]
        recent20 = sessions[max(0, latest_idx-19):latest_idx+1]
        influence_map = {(r["code"], r["branch"], r["broker"]): r for r in influence_rows}
        candidates = []
        for key, by_day in hist.items():
            code, branch, broker = key

            def prior_buy_streak(end_idx):
                days = []
                for i in range(end_idx, max(-1, end_idx - MAX_CONSECUTIVE_BUY_DAYS), -1):
                    d = sessions[i]
                    row = by_day.get(d)
                    if not row or row["net"] <= 0:
                        break
                    days.append(d)
                days.reverse()
                return days

            # First determine whether the pair is still actively buying.
            buy_streak_days = prior_buy_streak(latest_idx)
            sell_days = []
            direction = None
            status = None

            if len(buy_streak_days) >= MIN_CONSECUTIVE_BUY_DAYS:
                latest_net = by_day[buy_streak_days[-1]]["net"]
                prior_nets = [by_day[d]["net"] for d in buy_streak_days[:-1]]
                prior_avg = safe_mean(prior_nets) if prior_nets else None
                direction = "🟡降溫" if prior_avg and latest_net < prior_avg * 0.5 else "🟢加碼"
                status = "buying"
            else:
                # No active buy streak.  Check whether the newest sessions are
                # consecutive net-sell days immediately following a valid buy streak.
                for i in range(latest_idx, max(-1, latest_idx - 5), -1):
                    d = sessions[i]
                    row = by_day.get(d)
                    if not row or row["net"] >= 0:
                        break
                    sell_days.append(d)
                sell_days.reverse()
                before_sell_idx = latest_idx - len(sell_days)
                buy_streak_days = prior_buy_streak(before_sell_idx) if sell_days and before_sell_idx >= 0 else []
                if len(buy_streak_days) < MIN_CONSECUTIVE_BUY_DAYS:
                    continue
                status = "selling"

            amount = 0.0
            buy_nets = []
            usable = True
            for d in buy_streak_days:
                net = by_day[d]["net"]
                p = prices.get((d, code), (None, None))[0]
                if not p:
                    usable = False
                    break
                buy_nets.append(net)
                amount += net * 1000 * p
            if not usable or amount < MIN_3D_NET_AMOUNT:
                continue

            buy_lots = sum(buy_nets)
            sell_lots = abs(sum(by_day[d]["net"] for d in sell_days)) if sell_days else 0.0
            sellback_pct = (sell_lots / buy_lots * 100) if buy_lots > 0 else 0.0
            if status == "selling":
                direction = "🔴轉賣" if len(sell_days) >= 3 or sellback_pct >= 25.0 else "🟠減碼"

            latest_state_day = sessions[latest_idx]
            p3 = prices.get((latest_state_day, code), (None, None))[0]
            p5start = prices.get((recent5[0], code), (None, None))[0] if recent5 else None
            p20start = prices.get((recent20[0], code), (None, None))[0] if recent20 else None
            r5 = pct(p5start, p3)
            r20 = pct(p20start, p3)

            inf = influence_map.get(key, {})
            inf_score = inf.get("influence_score") or 0.0
            not_extended = (r5 is None or r5 <= 5.0) and (r20 is None or r20 <= 12.0)
            amount_score = clamp(math.log10(max(amount, 1) / MIN_3D_NET_AMOUNT + 1) * 25, 0, 25)
            continuity_score = clamp(20.0 + (len(buy_streak_days) - 3) * 2.5, 20.0, 50.0)
            history_score = min(25.0, inf_score * 0.25)
            extension_score = 15.0 if not_extended else 3.0
            stealth_score = continuity_score + amount_score + history_score + extension_score

            candidates.append({
                "code": code,
                "name": stock_names.get(code, ""),
                "branch_name": meta.get(key, ""),
                "branch": branch,
                "broker": broker,
                "latest_day": latest_state_day,
                "consecutive_buy_days": len(buy_streak_days),
                "consecutive_sell_days": len(sell_days),
                "streak_net_lots": round(buy_lots, 2),
                "streak_est_net_amount": round(amount),
                "recent_sell_lots": round(sell_lots, 2),
                "sellback_pct": round(sellback_pct, 2),
                "status": status,
                "direction": direction,
                "price_5d_pct": round(r5, 2) if r5 is not None else None,
                "price_20d_pct": round(r20, 2) if r20 is not None else None,
                "not_extended": not_extended,
                "historical_grade": inf.get("grade", "資料不足"),
                "historical_d10_samples": inf.get("d10_samples", 0),
                "historical_d10_win_pct": inf.get("d10_win_pct"),
                "historical_d10_avg_return_pct": inf.get("d10_avg_return_pct"),
                "stealth_score": round(stealth_score, 1),
            })

        candidates.sort(key=lambda r: (
            r["status"] == "buying",
            r["not_extended"],
            r["historical_grade"] == "A",
            r["stealth_score"],
            r["streak_est_net_amount"],
        ), reverse=True)
        influence_rows.sort(key=lambda r: (r["grade"] == "A", r["influence_score"], r["d10_samples"]), reverse=True)

        # Per-stock recent-buy branch snapshot for the Ghost radar table.
        # This is deliberately NOT a formal buy signal.  Before 120-session
        # validation is mature, Ghost still needs useful provisional context
        # instead of a blank cell.  Therefore use the latest 5 ACTUAL trading
        # sessions, rank branches by recurrence first and cumulative net-buy lots
        # second, and keep up to 5 branches per stock.
        # Formal current_candidates rules remain unchanged and still override this
        # provisional summary inside Ghost.
        stock_branch_summary = []
        recent_summary_days = sessions[max(0, latest_idx - 4):latest_idx + 1]
        if recent_summary_days:
            placeholders = ",".join("?" for _ in recent_summary_days)
            recent_buy_rows = db.execute(
                f"""SELECT r.day,r.code,r.branch,r.broker,r.name,r.net,r.rank
                    FROM ranking r
                    WHERE r.day IN ({placeholders}) AND r.side='buy'
                    ORDER BY r.code,r.day,r.rank""",
                tuple(recent_summary_days)
            ).fetchall()
        else:
            recent_buy_rows = []

        per_stock_branch = defaultdict(lambda: {
            "days": set(), "net": 0.0, "latest_day": "", "latest_rank": 999999,
            "branch_name": ""
        })
        for day, code, branch, broker, branch_name, net, rank in recent_buy_rows:
            key = (code, branch, broker)
            item = per_stock_branch[key]
            item["days"].add(day)
            item["net"] += float(net or 0)
            if day >= item["latest_day"]:
                item["latest_day"] = day
                item["latest_rank"] = int(rank or 999999)
                item["branch_name"] = branch_name or item["branch_name"]

        recent_by_code = defaultdict(list)
        for (code, branch, broker), item in per_stock_branch.items():
            inf = influence_map.get((code, branch, broker), {})
            recent_by_code[code].append({
                "code": code,
                "name": stock_names.get(code, ""),
                "branch_name": item["branch_name"] or "",
                "branch": branch,
                "broker": broker,
                "historical_grade": inf.get("grade") or "觀察",
                "status": "buying",
                "signal_class": "buying",
                "layout_active": False,
                "provisional": True,
                "latest_day": item["latest_day"] or latest_day,
                "consecutive_buy_days": len(item["days"]),
                "net_buy_lots": round(item["net"], 2),
                "recent_buy_days": len(item["days"]),
                "recent_window_sessions": len(recent_summary_days),
                "latest_rank": item["latest_rank"],
            })

        for code in sorted(stock_names):
            branches = recent_by_code.get(code, [])
            branches.sort(key=lambda r: (
                -int(r.get("recent_buy_days") or 0),
                -float(r.get("net_buy_lots") or 0),
                int(r.get("latest_rank") or 999999),
                str(r.get("broker") or ""),
            ))
            branches = branches[:5]

            if not branches:
                stock_branch_summary.append({
                    "code": code,
                    "name": stock_names.get(code, ""),
                    "status": "none",
                    "signal_class": "none",
                    "display": "近期5交易日暫無買盤摘要",
                    "latest_day": latest_day,
                    "provisional": True,
                })
                continue

            primary = dict(branches[0])
            primary["branches"] = branches
            primary["branch_count"] = len(branches)
            primary["other_branch_count"] = max(0, len(branches) - 1)
            primary["total_net_lots"] = round(sum(float(x.get("net_buy_lots") or 0) for x in branches), 2)

            label = primary.get("branch_name") or primary.get("broker") or "分點"
            days = int(primary.get("recent_buy_days") or 0)
            lots = float(primary.get("net_buy_lots") or 0)
            more = primary["other_branch_count"]
            parts = [
                "暫定",
                f"近5日出現{days}日",
                label,
                f"累計買超{lots:,.0f}張",
            ]
            if more:
                parts.append(f"＋{more}")
            primary["display"] = "｜".join(parts)
            stock_branch_summary.append(primary)

        # Persistent branch observation pool.
        # Purpose: once evidence is sufficient for approximate judgment, keep the
        # pair permanently in the pool and continue updating it. Short-term
        # cooling/selling does NOT delete historical observation membership.
        previous_watch = load_watch_pool()
        watch = dict(previous_watch)
        formal_map = {
            watch_key(r["code"], r["branch"], r["broker"]): r
            for r in candidates
        }
        inf_by_key = {
            watch_key(r["code"], r["branch"], r["broker"]): r
            for r in influence_rows
        }

        current_recent = {}
        for code, branches in recent_by_code.items():
            for row in branches:
                current_recent[watch_key(code, row["branch"], row["broker"])] = row

        all_keys = set(watch) | set(current_recent) | set(formal_map)
        now_iso = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
        for key in sorted(all_keys):
            recent = current_recent.get(key)
            formal = formal_map.get(key)
            inf = inf_by_key.get(key, {})
            old = watch.get(key)

            # Admission threshold = enough evidence for approximate judgment:
            # formal 3-15 day signal, OR at least 2 appearances in latest 5
            # trading sessions, OR at least 5 matured historical D+10 samples.
            recent_days = int((recent or {}).get("recent_buy_days") or 0)
            hist_samples = int(inf.get("d10_samples") or 0)
            admit = bool(formal) or recent_days >= 2 or hist_samples >= 5
            if old is None and not admit:
                continue

            code = str((formal or recent or old or {}).get("code") or inf.get("code") or "")
            branch = str((formal or recent or old or {}).get("branch") or inf.get("branch") or "")
            broker = str((formal or recent or old or {}).get("broker") or inf.get("broker") or "")
            name = str((formal or recent or old or {}).get("name") or inf.get("name") or stock_names.get(code, ""))
            branch_name = str((formal or recent or old or {}).get("branch_name") or inf.get("branch_name") or "")

            if formal:
                live_status = formal.get("direction") or formal.get("status") or "正式訊號"
                signal_level = "確認"
            elif recent:
                live_status = "持續觀察"
                signal_level = "觀察"
            else:
                live_status = "暫無新增買盤"
                signal_level = "歷史觀察"

            first_admitted = (old or {}).get("first_admitted_at") or now_iso
            entry = {
                "code": code,
                "name": name,
                "branch_name": branch_name,
                "branch": branch,
                "broker": broker,
                "first_admitted_at": first_admitted,
                "last_updated_at": now_iso,
                "data_through": latest_day,
                "pool_member": True,
                "never_auto_remove": True,
                "signal_level": signal_level,
                "live_status": live_status,
                "formal_key_branch": bool(formal),
                "consecutive_buy_days": int((formal or {}).get("consecutive_buy_days") or 0),
                "consecutive_sell_days": int((formal or {}).get("consecutive_sell_days") or 0),
                "recent_buy_days_5": recent_days,
                "streak_net_lots": (formal or {}).get("streak_net_lots"),
                "streak_est_net_amount": (formal or {}).get("streak_est_net_amount"),
                "recent_sell_lots": (formal or {}).get("recent_sell_lots"),
                "sellback_pct": (formal or {}).get("sellback_pct"),
                "historical_grade": inf.get("grade") or (old or {}).get("historical_grade") or "資料不足",
                "historical_d10_samples": hist_samples,
                "historical_d10_win_pct": inf.get("d10_win_pct"),
                "historical_d10_avg_return_pct": inf.get("d10_avg_return_pct"),
                "influence_score": inf.get("influence_score"),
                "last_recent_buy_day": (recent or {}).get("latest_day") or (old or {}).get("last_recent_buy_day"),
                "admission_reason": (
                    "正式連買訊號" if formal else
                    "近5日重複買超>=2日" if recent_days >= 2 else
                    "歷史D+10樣本>=5"
                ) if old is None else (old or {}).get("admission_reason"),
            }
            watch[key] = entry

        watch_entries = list(watch.values())
        watch_entries.sort(key=lambda r: (
            bool(r.get("formal_key_branch")),
            r.get("signal_level") == "觀察",
            float(r.get("influence_score") or 0),
            int(r.get("recent_buy_days_5") or 0),
            str(r.get("code") or ""),
        ), reverse=True)

        monitor_by_stock = defaultdict(list)
        for row in watch_entries:
            monitor_by_stock[row["code"]].append(row)

        monitor_feed = []
        for code, rows in sorted(monitor_by_stock.items()):
            rows.sort(key=lambda r: (
                bool(r.get("formal_key_branch")),
                int(r.get("recent_buy_days_5") or 0),
                float(r.get("influence_score") or 0),
            ), reverse=True)
            formal_count = sum(bool(r.get("formal_key_branch")) for r in rows)
            active_count = sum(r.get("live_status") != "暫無新增買盤" for r in rows)
            monitor_feed.append({
                "code": code,
                "name": stock_names.get(code, rows[0].get("name", "")),
                "data_through": latest_day,
                "watch_branch_count": len(rows),
                "active_branch_count": active_count,
                "formal_key_branch_count": formal_count,
                "branch_signal": (
                    "關鍵分點確認" if formal_count else
                    "分點持續觀察" if active_count else
                    "歷史觀察"
                ),
                "branches": rows[:10],
            })

        append_dated_raw_evidence(db, monitor_feed, stock_names, latest_day)

        complete_pairs = sum(r["history_days"] >= r["available_price_sessions"] and r["available_price_sessions"] > 0 for r in influence_rows)
        result = {
            "generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
            "data_through": latest_day,
            "universe_size": 163,
            "history_target_sessions": len(history_sessions),
            "history_target_months": HISTORY_MONTHS,
            "history_window_start": window_start,
            "short_history_is_failure": False,
            "branch_stock_pairs": len(influence_rows),
            "pairs_with_120_sessions": complete_pairs,
            "current_candidates": candidates[:20],
            "watch_pool": watch_entries,
            "monitor_branch_feed": monitor_feed,
            "stock_branch_summary": stock_branch_summary,
            "history_backfill_pending": [r for r in influence_rows if r["history_days"] < r["available_price_sessions"]][:200],
            "top_influence_pairs": [r for r in influence_rows if r["grade"] in ("A", "B")][:50],
            "rules": {
                "watch_pool": "資料累積到可大約判斷即進觀察池；進池後永久保留並持續更新，不因短期降溫或轉賣刪除",
                "monitor_feed": "觀察池同步輸出給即時監控；連買、偷偷買、減碼、轉賣與歷史影響力持續更新，作為籌碼情報",
                "current_signal": "同股同分點連買3～15日且累計>1,000萬元；成立後若轉為淨賣仍持續追蹤減碼/轉賣",
                "not_extended_aid": "5日漲幅<=5%且20日漲幅<=12%時優先；僅代表價格尚未過度延伸，不代表內線或主力身分",
                "history_validation": "以已儲存歷史配對D+5/D+10/D+20收盤；回填樣本存在事後選樣偏差，分數僅作排序",
            },
            "disclaimer": "券商分點不等於特定自然人或主力。歷史分數是描述性統計，不保證因果或未來報酬。",
        }

    (DATA / "final_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 關鍵分點最終結果｜{latest_day}",
        "",
        f"- 股票池：163檔",
        f"- 歷史基準：最近六個月；不足六個月以現有期間分析",
        f"- 股票×分點組合：{len(influence_rows):,}",
        f"- 已涵蓋可用股價交易日的組合：{complete_pairs:,}",
        f"- 今日符合「連買3～15日＋累計>1,000萬」：{len(candidates)}組",
        "",
        "## 疑似提前布局",
    ]
    if not candidates:
        lines.append("目前沒有符合正式門檻的組合。")
    else:
        lines.append("|排名|股票|分點|連買日數|連買段淨買超張|估算金額|5日股價|20日股價|歷史等級|D+10樣本/勝率|分數|")
        lines.append("|---:|---|---|---:|---:|---:|---:|---:|---|---|---:|")
        for i, r in enumerate(candidates[:10], 1):
            win = "—" if r["historical_d10_win_pct"] is None else f'{r["historical_d10_win_pct"]:.1f}%'
            p5 = "—" if r["price_5d_pct"] is None else f'{r["price_5d_pct"]:+.1f}%'
            p20 = "—" if r["price_20d_pct"] is None else f'{r["price_20d_pct"]:+.1f}%'
            lines.append(
                f'|{i}|{r["code"]} {r["name"]}|{r["branch_name"]}|{r["consecutive_buy_days"]}|{r["streak_net_lots"]:,.0f}|'
                f'{r["streak_est_net_amount"]/10000:,.0f}萬|{p5}|{p20}|{r["historical_grade"]}|'
                f'{r["historical_d10_samples"]}/{win}|{r["stealth_score"]:.1f}|'
            )
    lines += [
        "",
        "## 使用方式",
        "只看上表即可；未達門檻不列。歷史等級在樣本不足時會顯示「資料不足」，不會硬判定關鍵分點。",
        "",
        "> 券商分點只能代表交易通路，不能證明背後是同一個人或所謂主力。結果是研究訊號，不是保證獲利。",
        "",
    ]
    (DATA / "FINAL_RESULT.md").write_text("\n".join(lines), encoding="utf-8")

    (DATA / "watch_pool.json").write_text(
        json.dumps({
            "generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
            "data_through": latest_day,
            "policy": "persistent_observation_pool",
            "entries": watch_entries,
            "stocks": returned,
            "stock_policy": "monitor returns are permanent stock members; broker evidence still requires independent verification",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (DATA / "monitor_branch_feed.json").write_text(
        json.dumps({
            "generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
            "data_through": latest_day,
            "purpose": "feed branch intelligence to Ghost realtime monitor; never trigger orders alone",
            "stocks": monitor_feed,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with (DATA / "current_candidates.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = list(candidates[0].keys()) if candidates else [
            "code","name","branch_name","latest_day","consecutive_buy_days",
            "streak_net_lots","streak_est_net_amount","stealth_score"
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(candidates)

    print(json.dumps({
        "data_through": latest_day,
        "pairs": len(influence_rows),
        "pairs_with_120_sessions": complete_pairs,
        "current_candidates": len(candidates),
        "watch_pool_entries": len(watch_entries),
        "monitor_feed_stocks": len(monitor_feed),
        "top_candidate": candidates[0] if candidates else None,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
