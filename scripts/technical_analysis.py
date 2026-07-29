#!/usr/bin/env python3
"""
SET Technical Analysis Module
=============================
คำนวณ Ichimoku + MACD + RSI + Bollinger + Score สำหรับหุ้น SET
รันบน GitHub Actions (ไม่มี time limit) แล้ว commit ผลลัพธ์เป็น ta.json
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

# ============ CONFIG ============
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
COMPANIES_JSON = DATA_DIR / "companies.json"
TA_JSON = DATA_DIR / "technical_analysis.json"
SECTOR_SENTIMENT_JSON = DATA_DIR / "sector_sentiment.json"

LOOKBACK_DAYS = 90
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# จำกัดจำนวนตัวที่วิเคราะห์ต่อรอบ (0 = ทุกตัว)
# ถ้าใช้ GitHub Actions ไม่มี limit แต่ Yahoo อาจ ban ถ้าเร็วเกินไป
MAX_STOCKS = 0
SLEEP_PER_STOCK = 0.15  # วินาทีระหว่างตัว (ป้องกัน rate limit)


# ==================== INDICATORS ====================

def fetch_yf_history(symbol, days=90):
    """ดึงราคาจาก Yahoo Finance สำหรับหุ้นไทย (.BK)"""
    yf_symbol = f"{symbol}.BK"
    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=f"{days}d", interval="1d")
        if df.empty or len(df) < 30:
            return None
        df = df.reset_index()
        df.columns = [c.lower().replace(" ", "_") for c in df.columns]
        return df
    except Exception as e:
        print(f"   ⚠️  {symbol}: {e}")
        return None


def calculate_sma(series, period):
    return series.rolling(window=period).mean()


def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def calculate_ichimoku(df):
    """คำนวณ Ichimoku Cloud"""
    high = df['high']
    low = df['low']
    close = df['close']
    
    # Tenkan-sen (9)
    tenkan = ((high.rolling(9).max() + low.rolling(9).min()) / 2).iloc[-1]
    # Kijun-sen (26)
    kijun = ((high.rolling(26).max() + low.rolling(26).min()) / 2).iloc[-1]
    # Senkou Span A
    senkou_a = ((tenkan + kijun) / 2)
    # Senkou Span B (52)
    senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).iloc[-1]
    
    price = close.iloc[-1]
    
    # Cloud color & price position
    cloud_top = max(senkou_a, senkou_b)
    cloud_bottom = min(senkou_a, senkou_b)
    
    if price > cloud_top:
        price_vs_cloud = "ABOVE_CLOUD (Strong Bull)"
    elif price < cloud_bottom:
        price_vs_cloud = "BELOW_CLOUD (Strong Bear)"
    else:
        price_vs_cloud = "INSIDE_CLOUD (Neutral)"
    
    cloud_color = "BULLISH" if senkou_a > senkou_b else "BEARISH"
    
    # TK Cross
    tk_signal = "BULLISH" if tenkan > kijun else "BEARISH"
    
    return {
        "tenkan": round(tenkan, 2) if pd.notna(tenkan) else None,
        "kijun": round(kijun, 2) if pd.notna(kijun) else None,
        "senkou_a": round(senkou_a, 2) if pd.notna(senkou_a) else None,
        "senkou_b": round(senkou_b, 2) if pd.notna(senkou_b) else None,
        "cloud_color": cloud_color,
        "price_vs_cloud": price_vs_cloud,
        "tk_signal": tk_signal,
        "price": round(price, 2)
    }


def calculate_macd(df):
    """คำนวณ MACD (12,26,9)"""
    close = df['close']
    ema12 = calculate_ema(close, 12)
    ema26 = calculate_ema(close, 26)
    macd_line = ema12 - ema26
    signal_line = calculate_ema(macd_line, 9)
    histogram = macd_line - signal_line
    
    last_macd = macd_line.iloc[-1]
    last_signal = signal_line.iloc[-1]
    last_hist = histogram.iloc[-1]
    prev_macd = macd_line.iloc[-2]
    prev_signal = signal_line.iloc[-2]
    
    # Detect cross
    if prev_macd <= prev_signal and last_macd > last_signal:
        signal_text = "BUY (Cross Up)"
    elif prev_macd >= prev_signal and last_macd < last_signal:
        signal_text = "SELL (Cross Down)"
    elif last_macd > last_signal:
        signal_text = "BULLISH (MACD > Signal)"
    else:
        signal_text = "BEARISH (MACD < Signal)"
    
    return {
        "macd": round(last_macd, 3),
        "signal": round(last_signal, 3),
        "histogram": round(last_hist, 3),
        "signal_text": signal_text
    }


def calculate_rsi(df, period=14):
    """คำนวณ RSI ด้วย Wilder's Smoothing (ดั้งเดิมของ Welles Wilder)
    
    Wilder's Smoothing = EMA ที่ alpha = 1/period (เทียบเท่า com = period-1)
    ต่างจาก SMA ธรรมดาตรงที่ให้น้ำหนักลดหลั่นแบบ exponential ตามสูตรดั้งเดิม
    """
    close = df['close']
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    
    # Wilder's Smoothing: ใช้ EMA ด้วย alpha = 1/period
    # ค่าเฉลี่ยแรกจะเป็น SMA อัตโนมัติ (เพราะ min_periods=period) 
    # จากนั้นจะ smooth ด้วยสูตร: avg = (prev_avg * (n-1) + current) / n
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    last_rsi = rsi.iloc[-1]
    
    if pd.isna(last_rsi):
        return {"rsi": None, "condition": "N/A"}
    
    val = round(last_rsi, 2)
    if val >= RSI_OVERBOUGHT:
        cond = "OVERBOUGHT"
    elif val <= RSI_OVERSOLD:
        cond = "OVERSOLD"
    else:
        cond = "NEUTRAL"
    
    return {"rsi": val, "condition": cond}


def calculate_bollinger(df, period=20, mult=2):
    """คำนวณ Bollinger Bands"""
    close = df['close']
    sma = calculate_sma(close, period)
    std = close.rolling(window=period).std()
    upper = sma + (std * mult)
    lower = sma - (std * mult)
    
    price = close.iloc[-1]
    last_sma = sma.iloc[-1]
    last_upper = upper.iloc[-1]
    last_lower = lower.iloc[-1]
    
    if pd.isna(last_sma):
        return {"position": "N/A"}
    
    if price > last_upper:
        pos = "ABOVE_UPPER (Overbought)"
    elif price < last_lower:
        pos = "BELOW_LOWER (Oversold)"
    elif price > last_sma:
        pos = "UPPER_HALF (Bullish Bias)"
    else:
        pos = "LOWER_HALF (Bearish Bias)"
    
    return {
        "upper": round(last_upper, 2),
        "middle": round(last_sma, 2),
        "lower": round(last_lower, 2),
        "position": pos
    }


def calculate_score(ichi, macd, rsi, bb):
    """รวมคะแนน 0-100"""
    score = 50
    
    # Ichimoku (30%)
    if "ABOVE" in ichi['price_vs_cloud']:
        score += 15
    elif "BELOW" in ichi['price_vs_cloud']:
        score -= 15
    
    if ichi['tk_signal'] == "BULLISH":
        score += 10
    else:
        score -= 10
    
    if ichi['cloud_color'] == "BULLISH":
        score += 5
    else:
        score -= 5
    
    # MACD (25%)
    if "BUY" in macd['signal_text']:
        score += 15
    elif "SELL" in macd['signal_text']:
        score -= 15
    elif "BULLISH" in macd['signal_text']:
        score += 10
    else:
        score -= 10
    
    if macd['histogram'] > 0:
        score += 5
    else:
        score -= 5
    
    # RSI (20%)
    if rsi['condition'] == "OVERSOLD":
        score += 15
    elif rsi['condition'] == "OVERBOUGHT":
        score -= 15
    elif rsi['rsi'] and rsi['rsi'] > 50:
        score += 5
    else:
        score -= 5
    
    # Bollinger (15%)
    if "BELOW_LOWER" in bb['position']:
        score += 10
    elif "ABOVE_UPPER" in bb['position']:
        score -= 10
    elif "UPPER" in bb['position']:
        score += 5
    else:
        score -= 5
    
    score = max(0, min(100, score))
    
    if score >= 75:
        sig, strength = "STRONG_BUY", "STRONG"
    elif score >= 60:
        sig, strength = "BUY", "MODERATE"
    elif score >= 45:
        sig, strength = "HOLD", "WEAK"
    elif score >= 30:
        sig, strength = "SELL", "MODERATE"
    else:
        sig, strength = "STRONG_SELL", "STRONG"
    
    return {"score": score, "signal": sig, "strength": strength}


# ==================== MAIN ====================

def analyze_stock(symbol):
    """วิเคราะห์หุ้นตัวเดียว คืนค่า dict หรือ None"""
    df = fetch_yf_history(symbol, LOOKBACK_DAYS)
    if df is None or len(df) < 60:
        return None
    
    try:
        ichi = calculate_ichimoku(df)
        macd = calculate_macd(df)
        rsi = calculate_rsi(df)
        bb = calculate_bollinger(df)
        score = calculate_score(ichi, macd, rsi, bb)
        
        return {
            "symbol": symbol,
            "price": ichi['price'],
            "ichimoku_trend": ichi['tk_signal'],
            "ichimoku_cloud": ichi['cloud_color'],
            "price_vs_cloud": ichi['price_vs_cloud'],
            "chikou": ichi.get('chikou'),
            "macd_signal": macd['signal_text'],
            "macd_histogram": macd['histogram'],
            "rsi": rsi['rsi'],
            "rsi_condition": rsi['condition'],
            "bb_position": bb['position'],
            "score": score['score'],
            "signal": score['signal'],
            "strength": score['strength'],
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        print(f"   ❌ Calculation error: {e}")
        return None


def main():
    print("=" * 70)
    print("🔬 SET Technical Analysis (GitHub Actions)")
    print(f"🕐  Started at: {datetime.now()}")
    print("=" * 70)
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # โหลดรายชื่อบริษัทจาก companies.json
    if not COMPANIES_JSON.exists():
        print("❌ companies.json not found. Run fetch.py first.")
        sys.exit(1)
    
    with open(COMPANIES_JSON, "r", encoding="utf-8") as f:
        companies = json.load(f)
    
    print(f"📋 Loaded {len(companies)} companies from companies.json")
    
    # Filter เฉพาะ SET (ไม่เอา mai) ถ้าต้องการเร็วขึ้น
    # targets = [c for c in companies if c.get("market") == "SET"]
    targets = companies  # วิเคราะห์ทุกตัว
    
    if MAX_STOCKS > 0:
        targets = targets[:MAX_STOCKS]
        print(f"⚡ Limited to first {MAX_STOCKS} stocks")
    
    # วิเคราะห์ทีละตัว
    results = []
    watchlist = []
    sector_scores = {}  # เก็บ score รวมตาม sector/industry
    
    total = len(targets)
    for i, comp in enumerate(targets, 1):
        symbol = comp['symbol']
        print(f"[{i:04d}/{total}] {symbol}...", end=" ", flush=True)
        
        result = analyze_stock(symbol)
        
        if result:
            results.append(result)
            print(f"✅ Score={result['score']} {result['signal']}")
            
            # เก็บ sector sentiment
            sector = comp.get('sector', 'Unknown')
            industry = comp.get('industry', 'Unknown')
            
            if sector not in sector_scores:
                sector_scores[sector] = []
            if industry not in sector_scores:
                sector_scores[industry] = []
            
            sector_scores[sector].append(result['score'])
            sector_scores[industry].append(result['score'])
            
            if result['signal'] in ('BUY', 'STRONG_BUY'):
                watchlist.append({
                    **result,
                    "company_name": comp.get('company_name_en', ''),
                    "market": comp.get('market', 'SET')
                })
        else:
            print("⚠️  No data")
        
        time.sleep(SLEEP_PER_STOCK)
    
    # คำนวณ Sector Sentiment
    sentiment = []
    for name, scores in sector_scores.items():
        avg = round(sum(scores) / len(scores))
        if avg >= 60:
            sent = "🟢 BULLISH"
        elif avg <= 40:
            sent = "🔴 BEARISH"
        elif avg > 50:
            sent = "🟡 SLIGHT_BULL"
        else:
            sent = "🟠 SLIGHT_BEAR"
        
        sentiment.append({
            "name": name,
            "total": len(scores),
            "avg_score": avg,
            "sentiment": sent
        })
    
    sentiment.sort(key=lambda x: x['avg_score'], reverse=True)
    
    # เรียง watchlist ตาม score สูงสุด
    watchlist.sort(key=lambda x: x['score'], reverse=True)
    
    # บันทึกไฟล์
    ta_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_analyzed": len(results),
        "total_companies": len(targets),
        "stocks": results,
        "watchlist": watchlist,
        "sector_sentiment": sentiment
    }
    
    with open(TA_JSON, "w", encoding="utf-8") as f:
        json.dump(ta_data, f, ensure_ascii=False, indent=2)
    
    with open(SECTOR_SENTIMENT_JSON, "w", encoding="utf-8") as f:
        json.dump(sentiment, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 Saved: {TA_JSON}")
    print(f"💾 Saved: {SECTOR_SENTIMENT_JSON}")
    print(f"\n📊 Summary:")
    print(f"   • Analyzed: {len(results)}/{len(targets)}")
    print(f"   • Buy signals: {len([r for r in results if 'BUY' in r['signal']])}")
    print(f"   • Sell signals: {len([r for r in results if 'SELL' in r['signal']])}")
    print(f"   • Watchlist: {len(watchlist)} stocks")
    print("✅ Done!")


if __name__ == "__main__":
    main()
