# -*- coding: utf-8 -*-
"""
🦞 Lobster Radar - 本機資料庫更新器 v1.1
第一階段：
1) 建立 SQLite 主資料庫
2) 建立/更新台股股票主檔
3) 下載最近約 6 個月日K
4) 計算 20MA、MACD(6,13,9) DIF/DEA/HIST
5) 記錄續傳進度與失敗清單
6) 每次執行前自動備份資料庫
7) 預先建立法人、融資、基本面、訊號、5/10/20日績效資料表

後續可在同一份 lobster_radar.db 上擴充，不必重建資料庫。
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "lobster_radar.db"
PROGRESS_PATH = ROOT / "progress.json"
LOG_DIR = ROOT / "logs"
BACKUP_DIR = ROOT / "backup"

BATCH_SIZE = 40
SLEEP_BETWEEN_SYMBOLS = 0.4
PRICE_MONTHS_BACK = 7  # 多抓一點，確保至少有 20MA 暖機資料
USER_AGENT = "Mozilla/5.0 LobsterRadar/1.0"

LOG_DIR.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / f"lobster_{datetime.now():%Y%m%d}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("lobster")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def connect():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    return con


def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            market TEXT,
            yahoo_symbol TEXT NOT NULL,
            industry TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            adj_close REAL,
            volume REAL,
            PRIMARY KEY(symbol, trade_date),
            FOREIGN KEY(symbol) REFERENCES stocks(symbol)
        );

        CREATE TABLE IF NOT EXISTS technicals (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            ma20 REAL,
            ma20_slope REAL,
            ema6 REAL,
            ema13 REAL,
            dif REAL,
            dea REAL,
            macd_hist REAL,
            close_above_ma20 INTEGER,
            dif_cross_zero INTEGER,
            PRIMARY KEY(symbol, trade_date),
            FOREIGN KEY(symbol) REFERENCES stocks(symbol)
        );

        CREATE TABLE IF NOT EXISTS institutional (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            foreign_net REAL,
            investment_trust_net REAL,
            dealer_net REAL,
            total_net REAL,
            source TEXT,
            PRIMARY KEY(symbol, trade_date)
        );

        CREATE TABLE IF NOT EXISTS margin (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            margin_balance REAL,
            margin_change REAL,
            short_balance REAL,
            short_change REAL,
            source TEXT,
            PRIMARY KEY(symbol, trade_date)
        );

        CREATE TABLE IF NOT EXISTS fundamentals (
            symbol TEXT NOT NULL,
            period TEXT NOT NULL,
            revenue REAL,
            revenue_yoy REAL,
            eps REAL,
            gross_margin REAL,
            operating_margin REAL,
            net_margin REAL,
            source TEXT,
            updated_at TEXT,
            PRIMARY KEY(symbol, period)
        );

        CREATE TABLE IF NOT EXISTS signals (
            symbol TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            ma20_up INTEGER,
            close_above_ma20 INTEGER,
            dif_cross_zero INTEGER,
            fundamental_ok INTEGER,
            four_point_pass INTEGER,
            secondary_pass INTEGER,
            note TEXT,
            PRIMARY KEY(symbol, signal_date)
        );

        CREATE TABLE IF NOT EXISTS signal_performance (
            symbol TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            entry_close REAL,
            ret_5d REAL,
            ret_10d REAL,
            ret_20d REAL,
            max_drawdown_20d REAL,
            updated_at TEXT,
            PRIMARY KEY(symbol, signal_date)
        );

        CREATE TABLE IF NOT EXISTS failed_list (
            symbol TEXT PRIMARY KEY,
            stage TEXT,
            error TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_failed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_prices_date ON daily_prices(trade_date);
        CREATE INDEX IF NOT EXISTS idx_technicals_date ON technicals(trade_date);
        CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date);
        """)


def backup_db():
    if not DB_PATH.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / f"lobster_radar_{stamp}.db"
    shutil.copy2(DB_PATH, dst)
    # 只保留最近 20 份
    backups = sorted(BACKUP_DIR.glob("lobster_radar_*.db"), reverse=True)
    for old in backups[20:]:
        try:
            old.unlink()
        except Exception:
            pass
    log.info("資料庫備份完成：%s", dst.name)


def load_progress():
    default = {
        "stage": "prices",
        "next_index": 0,
        "completed_symbols": [],
        "last_run": None,
        "all_prices_complete": False,
    }
    if not PROGRESS_PATH.exists():
        return default
    try:
        data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        default.update(data)
    except Exception:
        log.warning("progress.json 無法讀取，改用安全預設值。")
    return default


def save_progress(p):
    p["last_run"] = now_iso()
    PROGRESS_PATH.write_text(
        json.dumps(p, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_twse_list():
    """
    取上市股票清單。官方 OpenAPI 若失敗則回空陣列。
    """
    url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    rows = r.json()
    out = []
    for x in rows:
        code = str(x.get("公司代號", "")).strip()
        name = str(x.get("公司簡稱", "")).strip()
        industry = str(x.get("產業別", "")).strip()
        if code.isdigit() and len(code) == 4:
            out.append((code, name, "TWSE", f"{code}.TW", industry))
    return out


def get_tpex_list():
    """
    取上櫃股票清單。使用櫃買中心 OpenAPI。
    """
    urls = [
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
    ]
    # 第一個端點有公司基本資料；若格式變更再嘗試第二個可得到代碼/名稱。
    for url in urls:
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            r.raise_for_status()
            rows = r.json()
            out = []
            for x in rows:
                code = str(
                    x.get("SecuritiesCompanyCode")
                    or x.get("公司代號")
                    or x.get("SecuritiesCompanyCode")
                    or x.get("Code")
                    or ""
                ).strip()
                name = str(
                    x.get("CompanyName")
                    or x.get("公司簡稱")
                    or x.get("SecuritiesCompanyName")
                    or x.get("Name")
                    or ""
                ).strip()
                industry = str(x.get("SecuritiesIndustryCode") or x.get("產業別") or "").strip()
                if code.isdigit() and len(code) == 4:
                    out.append((code, name, "TPEx", f"{code}.TWO", industry))
            if out:
                return out
        except Exception as e:
            log.warning("TPEx 清單端點失敗：%s", e)
    return []


def refresh_stock_master():
    """
    安全更新股票主檔：
    1) 同一天已抓過完整主檔時，直接沿用本機快取，避免每一批都重打 TWSE/TPEx。
    2) 新清單與資料庫既有清單取聯集，避免官方端點暫時少回幾檔就把股票誤停用。
    3) 初始建置階段寧可多保留舊股票，也不要因暫時性 SSL/API 問題漏掉股票。
    """
    with connect() as con:
        old = con.execute(
            "SELECT symbol,name,market,yahoo_symbol,industry,updated_at FROM stocks ORDER BY symbol"
        ).fetchall()

    # 先把資料庫既有股票全部視為可用；這可修復前一版因暫時 API 缺漏而被 active=0 的股票。
    old_map = {
        r[0]: (r[0], r[1], r[2], r[3], r[4])
        for r in old
    }

    today = datetime.now().date().isoformat()
    latest_update = max((str(r[5]) for r in old if r[5]), default="")
    if old_map and latest_update.startswith(today) and len(old_map) >= 1500:
        rows = sorted(old_map.values(), key=lambda x: x[0])
        with connect() as con:
            con.execute("UPDATE stocks SET active=1")
        log.info("沿用今日已快取股票主檔，共 %d 檔；本輪不重抓上市櫃清單。", len(rows))
        return rows

    fresh = []

    try:
        twse = get_twse_list()
        fresh.extend(twse)
        log.info("取得上市股票：%d 檔", len(twse))
    except Exception as e:
        log.warning("上市股票清單抓取失敗，保留本機既有主檔：%s", e)

    try:
        tpex = get_tpex_list()
        fresh.extend(tpex)
        log.info("取得上櫃股票：%d 檔", len(tpex))
    except Exception as e:
        log.warning("上櫃股票清單抓取失敗，保留本機既有主檔：%s", e)

    if not fresh and not old_map:
        raise RuntimeError("無法取得上市櫃股票清單，且資料庫沒有舊清單可沿用。")

    # 新資料優先，但保留舊資料中官方本次暫時漏掉的股票。
    merged = dict(old_map)
    for row in fresh:
        merged[row[0]] = row

    rows = sorted(merged.values(), key=lambda x: x[0])

    with connect() as con:
        con.executemany("""
            INSERT INTO stocks(symbol,name,market,yahoo_symbol,industry,active,updated_at)
            VALUES(?,?,?,?,?,1,?)
            ON CONFLICT(symbol) DO UPDATE SET
              name=excluded.name,
              market=excluded.market,
              yahoo_symbol=excluded.yahoo_symbol,
              industry=excluded.industry,
              active=1,
              updated_at=excluded.updated_at
        """, [(*r, now_iso()) for r in rows])
        # 本版採「不因單次 API 缺漏而停用股票」的保守策略
        con.execute("UPDATE stocks SET active=1")

    log.info("股票主檔安全合併完成，共 %d 檔。", len(rows))
    return rows


def normalize_yf(df):
    if df is None or df.empty:
        return pd.DataFrame()
    # yfinance 有時會回 MultiIndex
    if isinstance(df.columns, pd.MultiIndex):
        if df.columns.nlevels >= 2:
            df.columns = [c[0] for c in df.columns]
    df = df.reset_index()
    rename = {}
    for c in df.columns:
        lc = str(c).lower()
        if lc in ("date", "datetime"):
            rename[c] = "Date"
        elif lc == "open":
            rename[c] = "Open"
        elif lc == "high":
            rename[c] = "High"
        elif lc == "low":
            rename[c] = "Low"
        elif lc == "close":
            rename[c] = "Close"
        elif lc == "adj close":
            rename[c] = "Adj Close"
        elif lc == "volume":
            rename[c] = "Volume"
    return df.rename(columns=rename)


def calc_technicals(df):
    x = df.copy().sort_values("Date")
    close = pd.to_numeric(x["Close"], errors="coerce")
    x["MA20"] = close.rolling(20).mean()
    x["MA20_SLOPE"] = x["MA20"].diff()
    x["EMA6"] = close.ewm(span=6, adjust=False).mean()
    x["EMA13"] = close.ewm(span=13, adjust=False).mean()
    x["DIF"] = x["EMA6"] - x["EMA13"]
    x["DEA"] = x["DIF"].ewm(span=9, adjust=False).mean()
    x["MACD_HIST"] = x["DIF"] - x["DEA"]
    x["CLOSE_ABOVE_MA20"] = (close > x["MA20"]).astype(int)
    x["DIF_CROSS_ZERO"] = ((x["DIF"].shift(1) <= 0) & (x["DIF"] > 0)).astype(int)
    return x


def upsert_prices_and_technicals(symbol, yahoo_symbol):
    end = datetime.now() + timedelta(days=1)
    start = datetime.now() - timedelta(days=PRICE_MONTHS_BACK * 31)

    df = yf.download(
        yahoo_symbol,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        auto_adjust=False,
        progress=False,
        threads=False,
        timeout=25,
    )
    df = normalize_yf(df)
    if df.empty or "Close" not in df.columns:
        raise RuntimeError("Yahoo 無日K資料")

    df = calc_technicals(df)

    price_rows = []
    tech_rows = []
    for _, r in df.iterrows():
        dt = pd.to_datetime(r["Date"]).date().isoformat()
        def val(col):
            v = r.get(col)
            if pd.isna(v):
                return None
            return float(v)

        price_rows.append((
            symbol, dt, val("Open"), val("High"), val("Low"),
            val("Close"), val("Adj Close"), val("Volume")
        ))
        tech_rows.append((
            symbol, dt, val("MA20"), val("MA20_SLOPE"), val("EMA6"),
            val("EMA13"), val("DIF"), val("DEA"), val("MACD_HIST"),
            int(r["CLOSE_ABOVE_MA20"]), int(r["DIF_CROSS_ZERO"])
        ))

    with connect() as con:
        con.executemany("""
            INSERT INTO daily_prices(symbol,trade_date,open,high,low,close,adj_close,volume)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol,trade_date) DO UPDATE SET
              open=excluded.open, high=excluded.high, low=excluded.low,
              close=excluded.close, adj_close=excluded.adj_close, volume=excluded.volume
        """, price_rows)

        con.executemany("""
            INSERT INTO technicals(
              symbol,trade_date,ma20,ma20_slope,ema6,ema13,dif,dea,macd_hist,
              close_above_ma20,dif_cross_zero
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol,trade_date) DO UPDATE SET
              ma20=excluded.ma20, ma20_slope=excluded.ma20_slope,
              ema6=excluded.ema6, ema13=excluded.ema13,
              dif=excluded.dif, dea=excluded.dea, macd_hist=excluded.macd_hist,
              close_above_ma20=excluded.close_above_ma20,
              dif_cross_zero=excluded.dif_cross_zero
        """, tech_rows)

        con.execute("DELETE FROM failed_list WHERE symbol=? AND stage='prices'", (symbol,))


def mark_failed(symbol, stage, err):
    with connect() as con:
        con.execute("""
            INSERT INTO failed_list(symbol,stage,error,retry_count,last_failed_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
              stage=excluded.stage,
              error=excluded.error,
              retry_count=failed_list.retry_count+1,
              last_failed_at=excluded.last_failed_at
        """, (symbol, stage, str(err)[:1000], 1, now_iso()))


def count_db():
    with connect() as con:
        stocks = con.execute("SELECT COUNT(*) FROM stocks WHERE active=1").fetchone()[0]
        prices = con.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        tech = con.execute("SELECT COUNT(*) FROM technicals").fetchone()[0]
        failed = con.execute("SELECT COUNT(*) FROM failed_list").fetchone()[0]
    return stocks, prices, tech, failed


def main():
    log.info("=" * 65)
    log.info("🦞 龍蝦雷達開始執行")
    init_db()
    backup_db()
    stocks = refresh_stock_master()

    progress = load_progress()
    completed = set(progress.get("completed_symbols", []))

    # 每次先重試失敗清單，最多 10 檔
    with connect() as con:
        retry_symbols = [
            r[0] for r in con.execute(
                "SELECT symbol FROM failed_list WHERE stage='prices' ORDER BY last_failed_at LIMIT 10"
            ).fetchall()
        ]
    stock_map = {r[0]: r for r in stocks}
    for s in retry_symbols:
        if s in stock_map:
            row = stock_map[s]
            try:
                log.info("重試 %s %s", s, row[1])
                upsert_prices_and_technicals(s, row[3])
                completed.add(s)
            except Exception as e:
                log.warning("重試失敗 %s：%s", s, e)
                mark_failed(s, "prices", e)
            time.sleep(SLEEP_BETWEEN_SYMBOLS)

    # 找未完成股票
    pending = [r for r in stocks if r[0] not in completed]

    if not pending:
        # 全部跑完後，每次執行改成增量刷新：先更新全部股票的近期資料。
        # 為避免單次執行太久，每次仍只跑 BATCH_SIZE 檔，循環輪替。
        cycle_index = int(progress.get("cycle_index", 0))
        start_idx = cycle_index % max(1, len(stocks))
        batch = (stocks + stocks)[start_idx:start_idx + min(BATCH_SIZE, len(stocks))]
        progress["all_prices_complete"] = True
        progress["cycle_index"] = (start_idx + len(batch)) % max(1, len(stocks))
        log.info("初始建置已完成，進入日常增量更新；本次更新 %d 檔。", len(batch))
    else:
        batch = pending[:BATCH_SIZE]
        log.info("初始建置模式：尚餘 %d 檔，本次處理 %d 檔。", len(pending), len(batch))

    success = 0
    for i, (symbol, name, market, yahoo_symbol, industry) in enumerate(batch, 1):
        try:
            log.info("[%d/%d] %s %s (%s)", i, len(batch), symbol, name, market)
            upsert_prices_and_technicals(symbol, yahoo_symbol)
            completed.add(symbol)
            success += 1
        except Exception as e:
            log.warning("%s %s 失敗：%s", symbol, name, e)
            mark_failed(symbol, "prices", e)
        save_progress({
            **progress,
            "completed_symbols": sorted(completed),
            "all_prices_complete": len(completed) >= len(stocks),
        })
        time.sleep(SLEEP_BETWEEN_SYMBOLS)

    stocks_n, prices_n, tech_n, failed_n = count_db()
    log.info("本次成功：%d/%d", success, len(batch))
    log.info("資料庫狀態：股票 %d｜日K %d｜技術資料 %d｜待重試 %d",
             stocks_n, prices_n, tech_n, failed_n)
    if len(completed) >= len(stocks):
        log.info("✅ 最近約 6 個月日K＋20MA＋MACD 初始建置已完成。")
    else:
        log.info("⏳ 初始建置尚未完成；下次會從未完成股票繼續。")
    log.info("🦞 龍蝦雷達本次執行結束。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.warning("使用者中止執行。進度已盡量保存。")
    except Exception as e:
        log.exception("主程式發生錯誤：%s", e)
        sys.exit(1)
