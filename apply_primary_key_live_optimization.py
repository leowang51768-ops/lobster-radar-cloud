#!/usr/bin/env python3
from pathlib import Path

p = Path('buy_signal.py')
s = p.read_text(encoding='utf-8')

old = '''- The primary key is context only. It does NOT replace the existing formal entry key,
  so buy logic, volume gates, 8% anti-chase rule and support invalidation stay unchanged.
'''
new = '''- Signal priority after historical validation:
  1) 🟢 primary-structure key breakout = highest priority;
  2) 🟢 strong-20MA continuation = high-profit-potential route;
  3) 🟡 secondary 30MA pullback-key breakout = confirmation route;
  4) 🔵 key-candle formation = observation only.
- Existing volume gates, 8% anti-chase rules and support invalidation remain unchanged.
'''
assert old in s
s = s.replace(old, new, 1)

marker = '\n\ndef detect_layer2(code: str, x: pd.DataFrame, state: dict, allow_trigger: bool = True) -> tuple[dict, dict | None]:\n'
assert marker in s
primary_fn = '''\n\ndef detect_primary_breakout(code: str, x: pd.DataFrame, state: dict, allow_trigger: bool = True) -> tuple[dict, dict | None]:
    """Formal green-light signal from a confirmed primary structure key.

    Mirrors the validated primary-key backtest: first later close above the confirmed
    primary-key high while the standard four-point route is still valid, volume/liquidity
    pass and the close is no more than 8% above 30MA.
    """
    s = dict(state or {})
    if len(x) < 35 or s.get("primary_key_status") != "確認":
        return s, None
    key_date = str(s.get("primary_key_date") or "")
    if not key_date or s.get("primary_key_high") is None:
        return s, None

    t = x.iloc[-1]
    date = t.date.strftime("%Y-%m-%d")
    if date <= key_date:
        return s, None

    close = float(t.close)
    ma30 = float(t.ma30)
    key_high = float(s["primary_key_high"])
    key_low = float(s.get("primary_key_low", key_high))
    volume_ok, lots, _turnover, vol_ratio = volume_gate(t)
    extension = close / ma30 - 1.0 if ma30 else 999.0
    already = str(s.get("primary_last_trigger_key") or "") == key_date

    if allow_trigger and close > key_high and volume_ok and extension <= MAX_30MA_EXTENSION and not already:
        s["primary_last_trigger_key"] = key_date
        s["primary_last_trigger_date"] = date
        return s, {
            "date": date,
            "code": code,
            "signal_route": "主結構關鍵K突破",
            "signal_light": "🟢綠燈",
            "close": round(close, 2),
            "key_date": key_date,
            "key_high": round(key_high, 2),
            "key_low": round(key_low, 2),
            "primary_key_date": key_date,
            "primary_key_high": round(key_high, 2),
            "primary_key_low": round(key_low, 2),
            "volume_lots": round(lots, 0),
            "volume_ratio": round(vol_ratio, 2),
            "extension_30ma_pct": round(extension * 100, 2),
            "extension_20ma_pct": "",
            "support_lower": round(key_low, 2),
            "support_upper": round(key_low, 2),
            "support_source": "主結構關鍵K低點（初始失效參考）",
        }
    return s, None
'''
s = s.replace(marker, primary_fn + marker, 1)

old = '                "signal_route": "30MA回踩",\n                "close": round(close, 2),'
new = '                "signal_route": "30MA回踩",\n                "signal_light": "🟡黃燈",\n                "close": round(close, 2),'
assert old in s
s = s.replace(old, new, 1)

old = '                "signal_route": "強勢20MA續強",\n                "close": round(close, 2),'
new = '                "signal_route": "強勢20MA續強",\n                "signal_light": "🟢綠燈",\n                "close": round(close, 2),'
assert old in s
s = s.replace(old, new, 1)

old = '''        "date", "code", "name", "trend", "signal_route", "baseline_entry",
        "primary_key_date", "primary_key_high", "primary_key_low",
'''
new = '''        "date", "code", "name", "trend", "signal_route", "signal_light", "baseline_entry",
        "primary_key_date", "primary_key_high", "primary_key_low",
'''
assert old in s
s = s.replace(old, new, 1)

old = '''        state_after_30, trigger30 = detect_layer2(code, x, previous_state, allow_trigger=bool(l1.get("standard_ok")))
        new_state, trigger20 = detect_strong20_buy(code, x, state_after_30, l1, fundamental_ok)
'''
new = '''        state_after_30, trigger30 = detect_layer2(code, x, previous_state, allow_trigger=bool(l1.get("standard_ok")))
        state_after_primary, trigger_primary = detect_primary_breakout(
            code, x, state_after_30, allow_trigger=bool(l1.get("standard_ok"))
        )
        new_state, trigger20 = detect_strong20_buy(code, x, state_after_primary, l1, fundamental_ok)
'''
assert old in s
s = s.replace(old, new, 1)

old = '        trigger = trigger20 or trigger30\n'
new = '        trigger = trigger_primary or trigger20 or trigger30\n'
assert old in s
s = s.replace(old, new, 1)

old = '        lines = [f"🦞 關鍵K形成通知｜{latest_date}", "⚠️ 觀察通知，是否進場由你自行判斷"]\n'
new = '        lines = [f"🦞 🔵藍燈｜關鍵K形成通知｜{latest_date}", "⚠️ 觀察中，尚未形成正式突破買點"]\n'
assert old in s
s = s.replace(old, new, 1)

old = '''        for r in triggers:
            lines += ["", f"🔴 {r['code']} {r['name']}｜{r['trend']}", f"買點路徑：{r['signal_route']}"]
'''
new = '''        for r in triggers:
            light = r.get("signal_light", "🟡黃燈")
            lines += ["", f"{light} {r['code']} {r['name']}｜{r['trend']}", f"買點路徑：{r['signal_route']}"]
'''
assert old in s
s = s.replace(old, new, 1)

p.write_text(s, encoding='utf-8')
print('primary-key live optimization applied')
