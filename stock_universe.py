"""Fixed AI-industry stock universe for every Lobster Radar scanner.

The user-supplied list is labelled "175 stocks", but it contains 161 unique
company names.  The intended companies are retained here with official ticker
and market-suffix corrections; do not silently add companies to reach 175.
"""
from __future__ import annotations


STOCKS_TO_TRACK = {
    # 1. 矽晶圓與晶圓代工
    "2330.TW": "台積電", "6488.TWO": "環球晶", "3532.TW": "台勝科",
    "6182.TWO": "合晶", "2303.TW": "聯電", "5347.TWO": "世界先進",
    "6770.TW": "力積電",

    # 2. 矽智財 (IP) 與客製化晶片 (ASIC)
    "3661.TW": "世芯-KY", "3443.TW": "創意", "3529.TWO": "力旺",
    "3035.TW": "智原", "6643.TWO": "M31", "6533.TW": "晶心科",
    "6531.TW": "愛普*", "8227.TWO": "巨有科技", "6462.TWO": "神盾",
    "6684.TWO": "安格",

    # 3. 高速傳輸、BMC、記憶體與控制器
    "5274.TWO": "信驊", "5269.TW": "祥碩", "4966.TWO": "譜瑞-KY",
    "8299.TWO": "群聯", "2454.TW": "聯發科", "2379.TW": "瑞昱",
    "2408.TW": "南亞科", "2344.TW": "華邦電", "6485.TWO": "點序",
    "4967.TW": "十銓", "3260.TWO": "威剛", "3006.TW": "晶豪科",

    # 4. 先進封裝 (CoWoS/FOPLP)、OSAT 封測與檢測分析
    "3374.TWO": "精材", "6789.TW": "采鈺", "3711.TW": "日月光投控",
    "2449.TW": "京元電子", "6239.TW": "力成", "6257.TW": "矽格",
    "2441.TW": "超豐", "3289.TWO": "宜特", "3587.TWO": "閎康",
    "6830.TW": "汎銓", "8150.TW": "南茂", "8110.TW": "華東",
    "2369.TW": "菱生",

    # 5. CoWoS 設備、再生晶圓與廠務工程
    "3131.TWO": "弘塑", "3583.TW": "辛耘", "6187.TWO": "萬潤",
    "2467.TW": "志聖", "6640.TWO": "均華", "5443.TWO": "均豪",
    "1560.TW": "中砂", "8028.TW": "昇陽半導體", "3580.TWO": "友威科",
    "8064.TWO": "東捷", "6139.TW": "亞翔", "2404.TW": "漢唐",
    "5536.TWO": "聖暉*", "6196.TW": "帆宣", "6667.TWO": "信紘科",
    "6829.TWO": "千附精密",

    # 6. 測試介面、探針卡與晶圓載具
    "6223.TWO": "旺矽", "6515.TW": "穎崴", "6510.TWO": "精測",
    "3680.TWO": "家登", "7556.TWO": "意德士", "6788.TWO": "華景電",
    "3689.TWO": "湧德",

    # 7. 矽光子 (CPO)、光通訊與高階網通
    "3081.TWO": "聯亞", "4979.TWO": "華星光", "4977.TW": "眾達-KY",
    "4908.TWO": "前鼎", "3363.TWO": "上詮", "3163.TWO": "波若威",
    "6442.TW": "光聖", "2360.TW": "致茂", "2345.TW": "智邦",
    "3380.TW": "明泰", "4903.TWO": "聯光通", "6530.TWO": "創威",
    "3047.TW": "訊舟", "6426.TW": "統新",

    # 8. 散熱模組、液冷系統與水冷零組件
    "3017.TW": "奇鋐", "3324.TWO": "雙鴻", "3653.TW": "健策",
    "2421.TW": "建準", "8996.TW": "高力", "6805.TW": "富世達",
    "3483.TWO": "力致", "3338.TW": "泰碩", "6125.TWO": "廣運",
    "6230.TW": "尼得科超眾", "3071.TWO": "協禧", "6275.TWO": "元山",
    "4566.TW": "時碩工業", "1773.TW": "勝一",

    # 9. 電源供應器、高功率 PSU 與電網重電設備
    "2308.TW": "台達電", "2301.TW": "光寶科", "2385.TW": "群光",
    "6412.TW": "群電", "1519.TW": "華城", "1503.TW": "士電",
    "1513.TW": "中興電", "1514.TW": "亞力", "6282.TW": "康舒",
    "3015.TW": "全漢", "3032.TW": "偉訓", "3078.TWO": "僑威",
    "6203.TWO": "海韻電", "3540.TWO": "曜越",

    # 10. 高階 CCL、銅箔、PCB 與 ABF 載板
    "2383.TW": "台光電", "8358.TWO": "金居", "6274.TWO": "台燿",
    "3037.TW": "欣興", "2368.TW": "金像電", "8046.TW": "南電",
    "3189.TW": "景碩", "3044.TW": "健鼎", "2313.TW": "華通",
    "4958.TW": "臻鼎-KY", "3715.TW": "定穎投控", "5469.TW": "瀚宇博",
    "6191.TW": "精成科", "2355.TW": "敬鵬", "4989.TW": "榮科",
    "6269.TW": "台郡",

    # 11. 伺服器機殼、高階滑軌、高速線束與連接器
    "2059.TW": "川湖", "6584.TWO": "南俊國際", "8210.TW": "勤誠",
    "3013.TW": "晟銘電", "3665.TW": "貿聯-KY", "3533.TW": "嘉澤",
    "3693.TWO": "營邦", "6290.TWO": "良維", "3217.TWO": "優群",
    "6117.TW": "迎廣", "3325.TWO": "旭品", "5426.TWO": "振發",
    "8103.TW": "瀚荃", "3605.TW": "宏致", "3526.TWO": "凡甲",

    # 12. 伺服器系統整合 (ODM/OEM) 與 Edge AI / 光學鏡頭 / 機器人
    "3008.TW": "大立光", "3406.TW": "玉晶光", "2382.TW": "廣達",
    "6669.TW": "緯穎", "3231.TW": "緯創", "2317.TW": "鴻海",
    "2356.TW": "英業達", "4938.TW": "和碩", "2376.TW": "技嘉",
    "2357.TW": "華碩", "2377.TW": "微星", "3515.TW": "華擎",
    "3706.TW": "神達", "6933.TW": "AMAX-KY", "2395.TW": "研華",
    "6414.TW": "樺漢", "6166.TW": "凌華", "5289.TWO": "宜鼎",
    "2359.TW": "所羅門", "4585.TW": "達明", "6215.TW": "和椿",
    "2324.TW": "仁寶", "2312.TW": "金寶",
}

AI_STOCK_CODES = frozenset(ticker.split(".", 1)[0] for ticker in STOCKS_TO_TRACK)
AI_STOCK_COUNT = len(AI_STOCK_CODES)

if len(STOCKS_TO_TRACK) != AI_STOCK_COUNT:
    raise RuntimeError("AI stock universe contains duplicate four-digit codes")
if AI_STOCK_COUNT != 161:
    raise RuntimeError(f"Unexpected AI stock universe size: {AI_STOCK_COUNT}; expected 161")


def filter_market_frame(frame, code_column: str = "code"):
    """Return only the fixed AI universe, normalising codes as four digits."""
    codes = frame[code_column].astype(str).str.zfill(4)
    return frame.loc[codes.isin(AI_STOCK_CODES)].copy()
