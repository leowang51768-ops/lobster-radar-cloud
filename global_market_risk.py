#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""24h global market risk news collector for Wall Street Ghost."""
from __future__ import annotations
import json, math, re, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"data"/"global_market_risk.json"
OUT.parent.mkdir(parents=True,exist_ok=True)
TZ_TW=timezone(timedelta(hours=8))
UA="Mozilla/5.0 WallStreetGhost/1.0 (+GitHub Actions)"

MARKETS={
"US_SOX":("費半","^SOX"),"US_NASDAQ":("NASDAQ","^IXIC"),"US_SP500":("S&P500","^GSPC"),
"US_DOW":("道瓊","^DJI"),"VIX":("VIX","^VIX"),"JP_NIKKEI":("日經225","^N225"),
"KR_KOSPI":("KOSPI","^KS11"),"TW_TAIEX":("台股加權","^TWII"),"HK_HSI":("恆生","^HSI"),
"US10Y":("美債10年殖利率","^TNX")}
NEWS_QUERIES=[
"global stock market OR Wall Street OR semiconductor market",
"Taiwan stock market OR 台股 OR 台積電 OR 金管會 OR 經濟部",
"Japan stocks Nikkei semiconductor",
"Korea stocks KOSPI Samsung SK hynix semiconductor",
"China Hong Kong stocks market",
"Federal Reserve Powell Treasury yield inflation jobs",
"tariff sanctions export controls chip semiconductor",
"war attack missile earthquake financial market",
"president prime minister central bank governor market statement"]
SEVERE=["war","attack","missile","invasion","military strike","emergency","bank failure","default",
"capital controls","trading halt","market crash","earthquake","tsunami","nuclear","blockade","martial law",
"戰爭","開戰","攻擊","飛彈","入侵","軍事","緊急","銀行倒閉","違約","停止交易","崩盤","地震","海嘯","封鎖","戒嚴"]
NEGATIVE=["tariff","sanction","export ban","export control","restriction","ban","rate hike","hawkish",
"inflation surge","higher than expected","layoff","profit warning","cuts forecast","downgrade","shortage","recall",
"關稅","制裁","禁令","出口管制","限制","升息","鷹派","通膨升溫","高於預期","裁員","下修","砍單","召回","停工","火災"]
POSITIVE=["rate cut","dovish","ceasefire","stimulus","beats forecast","raises forecast","降息","鴿派","停火","刺激政策","優於預期","上修"]
MARKET_WORDS=["stock","market","nasdaq","semiconductor","chip","treasury","yield","fed","taiwan","japan",
"korea","china","hong kong","tsmc","nvidia","apple","台股","股市","半導體","晶片","美債","殖利率","聯準會","日本","韓國","中國","香港","台積電"]
LEADER_WORDS=["president","prime minister","chair","governor","powell","trump","xi jinping","總統","主席","首相","央行總裁","鮑爾","川普","習近平"]

def now_tw(): return datetime.now(TZ_TW)
def fetch_bytes(url,timeout=15):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"*/*"})
    with urllib.request.urlopen(req,timeout=timeout) as r:return r.read()
def safe_json(url):
    try:return json.loads(fetch_bytes(url).decode("utf-8","replace"))
    except Exception:return None
def market_snapshot():
    out={}
    for key,(label,symbol) in MARKETS.items():
        enc=urllib.parse.quote(symbol,safe="")
        obj=safe_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?range=2d&interval=5m&includePrePost=true")
        try:
            meta=obj["chart"]["result"][0]["meta"]; price=float(meta.get("regularMarketPrice"))
            prev=float(meta.get("chartPreviousClose") or meta.get("previousClose")); change=price-prev
            pct=(change/prev*100.0) if prev else 0.0
            out[key]={"label":label,"symbol":symbol,"price":round(price,4),"change":round(change,4),"change_pct":round(pct,2)}
        except Exception:
            out[key]={"label":label,"symbol":symbol,"price":None,"change":None,"change_pct":None}
    return out
def google_news_rss(query):
    q=urllib.parse.quote(query)
    url=f"https://news.google.com/rss/search?q={q}+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    try:root=ET.fromstring(fetch_bytes(url))
    except Exception:return []
    rows=[]
    for item in root.findall(".//item")[:20]:
        title=(item.findtext("title") or "").strip(); link=(item.findtext("link") or "").strip()
        pub=(item.findtext("pubDate") or "").strip(); src=item.find("source")
        source=(src.text or "").strip() if src is not None and src.text else ""
        try:
            dt=parsedate_to_datetime(pub)
            if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
            dt=dt.astimezone(TZ_TW)
        except Exception:dt=now_tw()
        rows.append({"title":title,"url":link,"source":source,"published_at":dt.isoformat(timespec="minutes")})
    return rows
def contains_any(text,words):
    t=text.lower(); return any(w.lower() in t for w in words)
def headline_score(title):
    score=risk=0
    if contains_any(title,MARKET_WORDS):score+=2
    if contains_any(title,LEADER_WORDS):score+=1
    if contains_any(title,NEGATIVE):risk+=2;score+=2
    if contains_any(title,SEVERE):risk+=4;score+=3
    if contains_any(title,POSITIVE):score+=1
    return score,risk
def collect_news():
    seen=set(); rows=[]; cutoff=now_tw()-timedelta(hours=30)
    for query in NEWS_QUERIES:
        for r in google_news_rss(query):
            key=re.sub(r"\s+"," ",r["title"].lower()).strip()
            if not key or key in seen:continue
            seen.add(key)
            try:dt=datetime.fromisoformat(r["published_at"])
            except Exception:dt=now_tw()
            if dt<cutoff:continue
            score,risk=headline_score(r["title"])
            if score<2:continue
            r["importance"]=score;r["risk_score"]=risk;rows.append(r)
    rows.sort(key=lambda r:(r["risk_score"],r["importance"],r["published_at"]),reverse=True)
    return rows[:18]
def q(m,key):
    v=(m.get(key) or {}).get("change_pct")
    return float(v) if isinstance(v,(int,float)) and math.isfinite(float(v)) else None
def compute_risk(markets,news):
    pts=0; reasons=[]
    for key,th,add,label in [
        ("US_SOX",-2.5,2,"費半明顯下跌"),("US_NASDAQ",-2.0,2,"NASDAQ明顯下跌"),
        ("JP_NIKKEI",-2.0,1,"日經明顯下跌"),("KR_KOSPI",-2.0,1,"韓股明顯下跌"),
        ("TW_TAIEX",-2.0,2,"台股明顯下跌"),("HK_HSI",-2.5,1,"港股明顯下跌")]:
        v=q(markets,key)
        if v is not None and v<=th:pts+=add;reasons.append(label)
    vix=(markets.get("VIX") or {}).get("price")
    if isinstance(vix,(int,float)) and vix>=30:pts+=2;reasons.append("VIX進入高檔")
    tnx=(markets.get("US10Y") or {}).get("change")
    if isinstance(tnx,(int,float)) and tnx>=0.12:pts+=2;reasons.append("美債10年殖利率快速上升")
    severe=[n for n in news if n.get("risk_score",0)>=4]
    negative=[n for n in news if n.get("risk_score",0)>=2]
    if severe:pts+=3;reasons.append("出現重大風險新聞")
    elif len(negative)>=3:pts+=2;reasons.append("多則市場負面消息同時發酵")
    return ("red" if pts>=3 else "green"),pts,reasons[:5]
def signed(v):return "—" if v is None else f"{v:+.1f}%"
def ticker_lines(markets,news,level):
    parts=[]; mtxt=[]
    for k in ["US_SOX","US_NASDAQ","JP_NIKKEI","KR_KOSPI","TW_TAIEX","HK_HSI"]:
        m=markets.get(k) or {}
        if m.get("change_pct") is not None:mtxt.append(f"{m.get('label')} {signed(m.get('change_pct'))}")
    if mtxt:parts.append("｜".join(mtxt))
    tnx=markets.get("US10Y") or {};vix=markets.get("VIX") or {};aux=[]
    if tnx.get("price") is not None:aux.append(f"美債10Y {tnx['price']:.2f}%")
    if vix.get("price") is not None:aux.append(f"VIX {vix['price']:.1f}")
    if aux:parts.append("｜".join(aux))
    for n in news[:8]:
        title=re.sub(r"\s+-\s+[^-]{1,45}$","",n["title"]).strip()
        if title:parts.append(title[:95])
    if not parts:parts=["目前未取得新的重大市場消息"]
    prefix="⚠ 全球市場風險" if level=="red" else "全球市場正常"
    return [f"{prefix}｜{p}" for p in parts[:10]]
def compact_market(markets):
    out={}
    for k,m in markets.items():
        pct=m.get("change_pct");bucket=None if pct is None else round(round(float(pct)/0.2)*0.2,1)
        out[k]={"label":m.get("label"),"price":m.get("price"),"change_pct":bucket}
    return out
def load_old():
    try:return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:return {}
def main():
    markets=market_snapshot();news=collect_news();level,score,reasons=compute_risk(markets,news)
    payload={"schema":1,"risk_level":level,"risk_score":score,"risk_reasons":reasons,
      "ticker":ticker_lines(markets,news,level),"markets":compact_market(markets),"active_news":news[:12],
      "source_note":"全球市場風險新聞台：僅保留目前仍具市場影響力的消息；舊聞/低影響消息不保留。"}
    old=load_old(); comparable_old={k:old.get(k) for k in payload.keys()}
    if comparable_old==payload:
        print("no material change");return 0
    payload["updated_at"]=now_tw().isoformat(timespec="seconds")
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"updated: risk={level} news={len(news)} reasons={reasons}")
    return 0
if __name__=="__main__":raise SystemExit(main())
