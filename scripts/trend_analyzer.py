#!/usr/bin/env python3
"""
SET Thailand Trend Analyzer — Ichimoku + EMA89/200 + RSI + MACD
================================================================
จัดกลุ่มหุ้นไทยตามสถานะ Trend เป็น 4 กลุ่ม:
  • 🌱 เริ่มเทรน     (STARTING)   — สัญญาณ bullish กำลังสร้างตัว
  • 🚀 เทรนกำลังเดิน (RUNNING)    — Trend ชัดเจน มomentum ดี
  • 😰 เทรนเริ่มล้า  (EXHAUSTING) — ยังอยู่ใน trend แต่ momentum ลด / overbought
  • 🔚 จบรอบเทรน   (ENDING)     — สัญญาณ bearish ชัดเจน

Usage:
  pip install pandas requests yfinance lxml
  python trend_analyzer.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

# ============ CONFIG ============
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
COMPANIES_JSON = DATA_DIR / "companies.json"
OUTPUT_JSON = DATA_DIR / "trend_analysis.json"

SET_XLS_URL = "https://www.set.or.th/dat/eod/listedcompany/static/listedCompanies_en_US.xls"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

LOOKBACK_DAYS = 250   # ต้อง >= 200 วัน เพื่อคำนวณ EMA 200 ได้สมบูรณ์
SLEEP_PER_STOCK = 0.25
MAX_STOCKS = 0        # 0 = วิเคราะห์ทั้งหมดที่ดึงได้

# ============ SET FETCHER ============

def fetch_set_companies():
    """ดึงรายชื่อบริษัทจาก SET ถ้ายังไม่มี companies.json"""
    print(f"[{datetime.now()}] 📥 Downloading company list from SET...")
    print(f"   URL: {SET_XLS_URL}")
    try:
        resp = requests.get(SET_XLS_URL, headers=HEADERS, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        print(f"   ❌ Download failed: {e}")
        return None

    for enc in ["tis-620", "cp874", "utf-8"]:
        try:
            html = resp.content.decode(enc)
            print(f"   🔤 Decoded with: {enc}")
            break
        except UnicodeDecodeError:
            continue
    else:
        print("   ❌ Cannot decode response with any known encoding")
        return None

    try:
        dfs = pd.read_html(StringIO(html))
    except Exception as e:
        print(f"   ❌ Parse HTML failed: {e}")
        return None

    if not dfs:
        print("   ❌ No tables found")
        return None

    df = dfs[0].iloc[2:].reset_index(drop=True)
    df.columns = [
        "symbol", "company_name_en", "market", "industry",
        "sector", "address", "zip_code", "telephone", "fax", "website"
    ]

    # Clean
    df = df[df["symbol"].notna()]
    df = df[df["symbol"].astype(str).str.match(r"^[A-Z0-9]+$", na=False)]
    df = df[df["symbol"].astype(str).str.len().between(2, 6)]

    for col in ["industry", "sector"]:
        df[col] = df[col].astype(str).replace(["-", "nan", "None"], "")

    df.insert(1, "company_name_th", "")
    df["updated_at"] = datetime.now(timezone.utc).isoformat()
    df = df.reset_index(drop=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_json(COMPANIES_JSON, orient="records", force_ascii=False, indent=2)
    print(f"   ✅ Saved {len(df)} companies ({df['market'].value_counts().to_dict()})")
    return df.to_dict("records")


def load_companies():
    if COMPANIES_JSON.exists():
        with open(COMPANIES_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"📂 Loaded {len(data)} companies from {COMPANIES_JSON}")
        return data
    return fetch_set_companies()


# ============ INDICATORS ============

def fetch_price(symbol, days=250):
    """ดึงราคาจาก Yahoo Finance (.BK)"""
    try:
        ticker = yf.Ticker(f"{symbol}.BK")
        df = ticker.history(period=f"{days}d", interval="1d")
        if df.empty or len(df) < 200:          # ต้องพอสำหรับ EMA 200
            return None
        df = df.reset_index()
        df.columns = [c.lower().replace(" ", "_") for c in df.columns]
        return df
    except Exception:
        return None


def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def calc_ichimoku(df):
    """คำนวณ Ichimoku Cloud + Chikou Span"""
    high, low, close = df["high"], df["low"], df["close"]

    tenkan = ((high.rolling(9).max() + low.rolling(9).min()) / 2).iloc[-1]
    kijun = ((high.rolling(26).max() + low.rolling(26).min()) / 2).iloc[-1]
    senkou_a = (tenkan + kijun) / 2
    senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).iloc[-1]

    price = close.iloc[-1]
    cloud_top = max(senkou_a, senkou_b)
    cloud_bottom = min(senkou_a, senkou_b)

    if price > cloud_top:
        pvc = "ABOVE_CLOUD"
    elif price < cloud_bottom:
        pvc = "BELOW_CLOUD"
    else:
        pvc = "INSIDE_CLOUD"

    # Chikou Span = ราคาวันนี้เทียบกับราคา 26 วันก่อน
    chikou_signal = "N/A"
    if len(close) >= 27:
        price_26ago = close.iloc[-27]
        chikou_signal = "BULLISH" if price > price_26ago else "BEARISH"

    return {
        "price": round(price, 2),
        "tenkan": round(tenkan, 2) if pd.notna(tenkan) else None,
        "kijun": round(kijun, 2) if pd.notna(kijun) else None,
        "senkou_a": round(senkou_a, 2) if pd.notna(senkou_a) else None,
        "senkou_b": round(senkou_b, 2) if pd.notna(senkou_b) else None,
        "cloud_color": "BULLISH" if senkou_a > senkou_b else "BEARISH",
        "price_vs_cloud": pvc,
        "tk_signal": "BULLISH" if tenkan > kijun else "BEARISH",
        "chikou_signal": chikou_signal,
    }


def calc_rsi(df, period=14):
    """RSI ด้วย Wilder's Smoothing (RMA) — ตรงกับ TradingView"""
    close = df["close"]
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    val = round(rsi.iloc[-1], 2)

    if pd.isna(val):
        return {"rsi": None, "condition": "N/A"}

    if val >= 70:
        cond = "OVERBOUGHT"
    elif val <= 30:
        cond = "OVERSOLD"
    else:
        cond = "NEUTRAL"

    return {"rsi": val, "condition": cond}


def calc_macd(df):
    """MACD (12,26,9) พร้อมตรวจจับ Cross & Histogram Trend"""
    close = df["close"]
    ema12 = calc_ema(close, 12)
    ema26 = calc_ema(close, 26)
    macd_line = ema12 - ema26
    signal_line = calc_ema(macd_line, 9)
    histogram = macd_line - signal_line

    last_macd = macd_line.iloc[-1]
    last_signal = signal_line.iloc[-1]
    last_hist = histogram.iloc[-1]
    prev_hist = histogram.iloc[-2] if len(histogram) >= 2 else last_hist
    prev_macd = macd_line.iloc[-2] if len(macd_line) >= 2 else last_macd
    prev_signal = signal_line.iloc[-2] if len(signal_line) >= 2 else last_signal

    if prev_macd <= prev_signal and last_macd > last_signal:
        sig = "BUY (Cross Up)"
    elif prev_macd >= prev_signal and last_macd < last_signal:
        sig = "SELL (Cross Down)"
    elif last_macd > last_signal:
        sig = "BULLISH (MACD > Signal)"
    else:
        sig = "BEARISH (MACD < Signal)"

    return {
        "macd": round(last_macd, 3),
        "signal": round(last_signal, 3),
        "histogram": round(last_hist, 3),
        "hist_prev": round(prev_hist, 3),
        "signal_text": sig,
        "hist_increasing": last_hist > prev_hist,
        "hist_positive": last_hist > 0,
    }


# ============ TREND CLASSIFICATION ============

def classify_trend(ichi, ema89, ema200, rsi, macd):
    """
    จัดกลุ่มหุ้นตามสถานะ Trend เป็น 4 กลุ่มหลัก
    อิงจากความสอดคล้องของ Ichimoku + EMA Trend Filter + RSI Momentum + MACD
    """
    price = ichi["price"]

    # ----- Core boolean conditions -----
    c = {
        "price_above_ema200": price > ema200,
        "price_above_ema89": price > ema89,
        "ema89_above_200": ema89 > ema200,
        "above_cloud": "ABOVE_CLOUD" in ichi["price_vs_cloud"],
        "inside_cloud": "INSIDE_CLOUD" in ichi["price_vs_cloud"],
        "below_cloud": "BELOW_CLOUD" in ichi["price_vs_cloud"],
        "tenkan_above_kijun": ichi["tk_signal"] == "BULLISH",
        "cloud_bullish": ichi["cloud_color"] == "BULLISH",
        "chikou_bullish": ichi.get("chikou_signal") == "BULLISH",
        "macd_bullish": "BULLISH" in macd["signal_text"] or "BUY" in macd["signal_text"],
        "macd_cross_buy": "BUY (Cross Up)" in macd["signal_text"],
        "macd_cross_sell": "SELL (Cross Down)" in macd["signal_text"],
        "rsi_45_70": 45 <= rsi["rsi"] <= 70,
        "rsi_above_70": rsi["rsi"] > 70,
        "rsi_below_40": rsi["rsi"] < 40,
        "hist_positive": macd["hist_positive"],
        "hist_increasing": macd["hist_increasing"],
    }

    # ----- Bullish score (0–10) -----
    score = sum([
        c["price_above_ema200"],
        c["price_above_ema89"],
        c["ema89_above_200"],
        c["above_cloud"],
        c["tenkan_above_kijun"],
        c["cloud_bullish"],
        c["macd_bullish"],
        c["hist_positive"],
        c["rsi_45_70"],
        c["hist_increasing"],
    ])

    # ==================== ENDING ====================
    # สัญญาณหลัก bearish: ราคาต่ำกว่า EMA200 หรือต่ำกว่า Cloud + สัญญาณลบอื่น
    if c["below_cloud"] or not c["price_above_ema200"]:
        if c["macd_cross_sell"] or not c["macd_bullish"] or c["rsi_below_40"]:
            return "จบรอบเทรน", "ENDING", score, c

    if not c["price_above_ema89"] and not c["tenkan_above_kijun"] and c["below_cloud"]:
        return "จบรอบเทรน", "ENDING", score, c

    # ==================== EXHAUSTING ====================
    # ยังอยู่ใน trend ขาขึ้น แต่ momentum เริ่มลด หรือ overbought
    if c["above_cloud"] and c["price_above_ema200"] and c["macd_bullish"]:
        if c["rsi_above_70"]:
            return "เทรนเริ่มล้า", "EXHAUSTING", score, c
        if c["hist_positive"] and not c["hist_increasing"]:
            return "เทรนเริ่มล้า", "EXHAUSTING", score, c

    # ==================== STARTING ====================
    # สัญญาณ bullish กำลังสร้างตัว แต่ยังไม่แรง/สมบูรณ์เท่า RUNNING
    if c["price_above_ema200"] and c["above_cloud"] and c["macd_bullish"]:
        # MACD เพิ่งตัดขึ้น + RSI อยู่ในโซนสร้าง momentum + histogram ขยายตัว
        if c["macd_cross_buy"] and c["rsi_45_70"] and c["hist_increasing"]:
            return "เริ่มเทรน", "STARTING", score, c
        # คะแนนยังไม่สูงมาก แต่สัญญาณเริ่มเข้าที่
        if 6 <= score < 9 and c["rsi_45_70"] and c["hist_increasing"]:
            return "เริ่มเทรน", "STARTING", score, c

    # กรณีเพิ่งขึ้นมาเหนือ EMA200/Cloud แต่ EMA89 ยังไม่เหนือ EMA200 (early stage)
    if c["price_above_ema200"] and c["above_cloud"] and c["tenkan_above_kijun"]:
        if c["macd_bullish"] and not c["rsi_above_70"] and score >= 6:
            if not c["ema89_above_200"] or not c["hist_increasing"]:
                return "เริ่มเทรน", "STARTING", score, c

    # ==================== RUNNING ====================
    # ทุกอย่างแข็งแกร่งครบถ้วน
    if score >= 8 and c["price_above_ema200"] and c["above_cloud"] and c["macd_bullish"] and c["hist_increasing"]:
        return "เทรนกำลังเดิน", "RUNNING", score, c

    if score >= 7 and c["price_above_ema200"] and c["above_cloud"] and c["ema89_above_200"] and c["macd_bullish"]:
        return "เทรนกำลังเดิน", "RUNNING", score, c

    # ==================== FALLBACK STARTING ====================
    if score >= 6 and c["price_above_ema200"] and c["above_cloud"]:
        return "เริ่มเทรน", "STARTING", score, c

    # ==================== FALLBACK ENDING ====================
    if score < 4:
        return "จบรอบเทรน", "ENDING", score, c

    return "ไม่แน่นอน", "UNCERTAIN", score, c


# ============ MAIN ============

def analyze_stock(symbol):
    df = fetch_price(symbol, LOOKBACK_DAYS)
    if df is None or len(df) < 200:
        return None

    try:
        ichi = calc_ichimoku(df)
        ema89_series = calc_ema(df["close"], 89)
        ema200_series = calc_ema(df["close"], 200)
        ema89 = ema89_series.iloc[-1]
        ema200 = ema200_series.iloc[-1]

        if pd.isna(ema89) or pd.isna(ema200):
            return None

        rsi = calc_rsi(df)
        if rsi["rsi"] is None:
            return None

        macd = calc_macd(df)
        trend_th, trend_en, score, conds = classify_trend(ichi, ema89, ema200, rsi, macd)

        return {
            "symbol": symbol,
            "price": ichi["price"],
            "trend_th": trend_th,
            "trend_en": trend_en,
            "trend_score": score,
            "ema89": round(ema89, 2),
            "ema200": round(ema200, 2),
            "price_above_ema89": conds["price_above_ema89"],
            "price_above_ema200": conds["price_above_ema200"],
            "ema89_above_200": conds["ema89_above_200"],
            "ichimoku_tk": ichi["tk_signal"],
            "ichimoku_cloud": ichi["cloud_color"],
            "price_vs_cloud": ichi["price_vs_cloud"],
            "chikou_signal": ichi.get("chikou_signal", "N/A"),
            "macd_signal": macd["signal_text"],
            "macd_histogram": macd["histogram"],
            "macd_hist_increasing": macd["hist_increasing"],
            "rsi": rsi["rsi"],
            "rsi_condition": rsi["condition"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None


def main():
    print("=" * 72)
    print("📈 SET Thailand Trend Analyzer")
    print("   Indicators: Ichimoku | EMA 89 | EMA 200 | RSI | MACD")
    print(f"🕐  Started: {datetime.now()}")
    print("=" * 72)

    companies = load_companies()
    if not companies:
        print("❌ No companies data"); sys.exit(1)

    targets = companies[:MAX_STOCKS] if MAX_STOCKS > 0 else companies

    results = []
    groups = {
        "เริ่มเทรน": [],
        "เทรนกำลังเดิน": [],
        "เทรนเริ่มล้า": [],
        "จบรอบเทรน": [],
        "ไม่แน่นอน": [],
    }

    total = len(targets)
    for i, comp in enumerate(targets, 1):
        sym = comp["symbol"]
        print(f"[{i:04d}/{total}] {sym}...", end=" ", flush=True)

        res = analyze_stock(sym)
        if res:
            results.append(res)
            groups[res["trend_th"]].append(res)
            print(f"✅ {res['trend_th']} (Score: {res['trend_score']})")
        else:
            print("⚠️  No data")

        time.sleep(SLEEP_PER_STOCK)

    # Sort by score within groups
    for g in groups:
        groups[g].sort(key=lambda x: x["trend_score"], reverse=True)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_analyzed": len(results),
        "total_companies": len(targets),
        "summary": {k: len(v) for k, v in groups.items()},
        "groups": groups,
        "all_stocks": results,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ===== Terminal Report =====
    emoji_map = {
        "เริ่มเทรน": "🌱",
        "เทรนกำลังเดิน": "🚀",
        "เทรนเริ่มล้า": "😰",
        "จบรอบเทรน": "🔚",
        "ไม่แน่นอน": "❓",
    }

    print("\n" + "=" * 72)
    print("📊 TREND SUMMARY")
    print("=" * 72)

    for name, stocks in groups.items():
        if not stocks:
            continue
        emo = emoji_map[name]
        print(f"\n{emo} {name} ({len(stocks)} ตัว)")
        print("-" * 60)
        for s in stocks[:15]:
            flag = "🚩" if s["trend_en"] == "EXHAUSTING" else ""
            print(
                f"   {s['symbol']:6s}  {s['price']:8.2f} ฿  | "
                f"Score: {s['trend_score']:2d}  | "
                f"RSI: {s['rsi']:5.1f}  | "
                f"MACD: {s['macd_signal']:22s}  | "
                f"Cloud: {s['price_vs_cloud']:14s} {flag}"
            )
        if len(stocks) > 15:
            print(f"   ... และอีก {len(stocks)-15} ตัว")

    print(f"\n💾 Saved: {OUTPUT_JSON}")
    print("✅ Done!")


if __name__ == "__main__":
    main()
