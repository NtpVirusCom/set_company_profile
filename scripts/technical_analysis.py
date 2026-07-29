#!/usr/bin/env python3
"""
SET Technical Analysis Module (Enhanced Edition)
================================================
คำนวณ Ichimoku + MACD + RSI (Wilder's Smoothing) + Bollinger + Score สำหรับหุ้น SET
ปรับปรุงตามข้อเสนอแนะ 3 ระดับ:
  - Level 1: Graduated RSI, Anti Double-Counting, Trend Filter
  - Level 2: Weighted Average, Continuous Scoring, Divergence Detection
  - Level 3: Dynamic Weighting (Trending vs Sideway), Volatility Awareness
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

MAX_STOCKS = 0
SLEEP_PER_STOCK = 0.15


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
    
    tenkan = ((high.rolling(9).max() + low.rolling(9).min()) / 2).iloc[-1]
    kijun = ((high.rolling(26).max() + low.rolling(26).min()) / 2).iloc[-1]
    senkou_a = ((tenkan + kijun) / 2)
    senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).iloc[-1]
    
    price = close.iloc[-1]
    
    cloud_top = max(senkou_a, senkou_b)
    cloud_bottom = min(senkou_a, senkou_b)
    
    if price > cloud_top:
        price_vs_cloud = "ABOVE_CLOUD (Strong Bull)"
    elif price < cloud_bottom:
        price_vs_cloud = "BELOW_CLOUD (Strong Bear)"
    else:
        price_vs_cloud = "INSIDE_CLOUD (Neutral)"
    
    cloud_color = "BULLISH" if senkou_a > senkou_b else "BEARISH"
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


def calculate_rsi_wilder(df, period=14):
    """
    RSI ด้วย Wilder's Smoothing (RMA) - ตรงกับ TradingView / Investing.com
    
    Wilder's Smoothing Formula:
      avg = (prev_avg * (n-1) + current) / n
    
    ใน pandas ใช้ ewm(alpha=1/n, adjust=False) จะได้สูตรเดียวกับ RMA:
      y_t = (1-alpha) * y_{t-1} + alpha * x_t
      เมื่อ alpha = 1/n → y_t = ((n-1)*y_{t-1} + x_t) / n
    """
    close = df['close']
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    
    # Wilder's Smoothing (RMA) - ตรงกับ TradingView
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    last_rsi = rsi.iloc[-1]
    
    if pd.isna(last_rsi):
        return {"rsi": None, "condition": "N/A", "rsi_series": rsi}
    
    val = round(last_rsi, 2)
    if val >= RSI_OVERBOUGHT:
        cond = "OVERBOUGHT"
    elif val <= RSI_OVERSOLD:
        cond = "OVERSOLD"
    else:
        cond = "NEUTRAL"
    
    return {"rsi": val, "condition": cond, "rsi_series": rsi}


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
        return {"position": "N/A", "bb_width": None, "percent_b": None}
    
    if price > last_upper:
        pos = "ABOVE_UPPER (Overbought)"
    elif price < last_lower:
        pos = "BELOW_LOWER (Oversold)"
    elif price > last_sma:
        pos = "UPPER_HALF (Bullish Bias)"
    else:
        pos = "LOWER_HALF (Bearish Bias)"
    
    band_width = last_upper - last_lower
    percent_b = (price - last_lower) / band_width if band_width != 0 else 0.5
    bb_width = band_width / price if price != 0 else 0
    
    return {
        "upper": round(last_upper, 2),
        "middle": round(last_sma, 2),
        "lower": round(last_lower, 2),
        "position": pos,
        "bb_width": round(bb_width, 4),
        "percent_b": round(percent_b, 4)
    }


# ==================== ENHANCED SCORING SYSTEM ====================

def detect_rsi_divergence(df, rsi_series, lookback=14):
    """
    Level 2: ตรวจจับ RSI Divergence
    Returns: "BULLISH_DIV", "BEARISH_DIV", "NONE"
    """
    if rsi_series is None or len(rsi_series) < lookback * 2:
        return "NONE"
    
    close = df['close'].values
    rsi = rsi_series.values
    window = lookback * 2
    mid = window // 2
    
    recent_close = close[-window:]
    recent_rsi = rsi[-window:]
    
    # Bullish Divergence: Price Lower Low, RSI Higher Low
    price_low1 = recent_close[:mid].min()
    price_low2 = recent_close[mid:].min()
    rsi_vals_1 = recent_rsi[:mid][recent_rsi[:mid] > 0]
    rsi_vals_2 = recent_rsi[mid:][recent_rsi[mid:] > 0]
    rsi_low1 = rsi_vals_1.min() if len(rsi_vals_1) > 0 else 999
    rsi_low2 = rsi_vals_2.min() if len(rsi_vals_2) > 0 else 999
    
    if price_low2 < price_low1 and rsi_low2 > rsi_low1 and rsi_low2 < 45:
        return "BULLISH_DIV"
    
    # Bearish Divergence: Price Higher High, RSI Lower High
    price_high1 = recent_close[:mid].max()
    price_high2 = recent_close[mid:].max()
    rsi_high1 = recent_rsi[:mid].max()
    rsi_high2 = recent_rsi[mid:].max()
    
    if price_high2 > price_high1 and rsi_high2 < rsi_high1 and rsi_high2 > 55:
        return "BEARISH_DIV"
    
    return "NONE"


def calculate_trend_regime(ichi):
    """
    Level 3: วัดสภาพตลาดจาก Ichimoku Cloud Thickness
    Returns: "STRONG_TREND", "WEAK_TREND", "SIDEWAY"
    """
    if not ichi['senkou_a'] or not ichi['senkou_b'] or not ichi['price'] or ichi['price'] == 0:
        return "UNKNOWN"
    
    cloud_thickness = abs(ichi['senkou_a'] - ichi['senkou_b']) / ichi['price']
    
    if cloud_thickness < 0.02:
        return "SIDEWAY"
    elif cloud_thickness > 0.05:
        return "STRONG_TREND"
    else:
        return "WEAK_TREND"


def score_ichimoku_continuous(ichi):
    """
    Level 2: Continuous Ichimoku Scoring (0-100)
    """
    score = 50
    price = ichi['price']
    
    if not price or not ichi['senkou_a'] or not ichi['senkou_b']:
        return 50
    
    cloud_top = max(ichi['senkou_a'], ichi['senkou_b'])
    cloud_bottom = min(ichi['senkou_a'], ichi['senkou_b'])
    cloud_range = cloud_top - cloud_bottom
    
    if cloud_range > 0:
        if price > cloud_top:
            above_pct = (price - cloud_top) / cloud_range
            score = min(100, 75 + above_pct * 25)
        elif price < cloud_bottom:
            below_pct = (cloud_bottom - price) / cloud_range
            score = max(0, 25 - below_pct * 25)
        else:
            score = 25 + ((price - cloud_bottom) / cloud_range) * 50
    else:
        score = 50 if price > cloud_top else 50
    
    if ichi['tenkan'] and ichi['kijun']:
        tk_diff = (ichi['tenkan'] - ichi['kijun']) / price * 100
        if tk_diff > 0:
            score += min(10, tk_diff * 2)
        else:
            score -= min(10, abs(tk_diff) * 2)
    
    if ichi['cloud_color'] == "BULLISH":
        score += 3
    else:
        score -= 3
    
    return max(0, min(100, score))


def score_macd_continuous(macd, price):
    """
    Level 2: Continuous MACD Scoring (0-100)
    """
    score = 50
    hist = macd['histogram']
    signal_text = macd['signal_text']
    
    if price and price > 0:
        hist_pct = hist / price * 100
    else:
        hist_pct = 0
    
    if "BUY (Cross Up)" in signal_text:
        score += 15 + min(10, abs(hist_pct) * 5)
    elif "SELL (Cross Down)" in signal_text:
        score -= 15 + min(10, abs(hist_pct) * 5)
    elif "BULLISH" in signal_text:
        score += 5 + min(10, max(0, hist_pct) * 3)
    else:
        score -= 5 + min(10, max(0, -hist_pct) * 3)
    
    return max(0, min(100, score))


def score_rsi_continuous(rsi_data):
    """
    Level 1+2: Graduated RSI Scoring (0-100)
    ใช้ Piecewise Linear แทน Binary jump
    """
    rsi_val = rsi_data['rsi']
    if rsi_val is None:
        return 50
    
    if rsi_val <= 20:
        score = 90
    elif rsi_val <= 30:
        score = 75 + (30 - rsi_val) * 1.5
    elif rsi_val <= 40:
        score = 60 + (40 - rsi_val) * 1.5
    elif rsi_val <= 50:
        score = 50 + (50 - rsi_val) * 1.0
    elif rsi_val <= 60:
        score = 50 - (rsi_val - 50) * 1.0
    elif rsi_val <= 70:
        score = 40 - (rsi_val - 60) * 1.5
    elif rsi_val <= 80:
        score = 25 - (rsi_val - 70) * 1.5
    else:
        score = 10
    
    div = rsi_data.get('divergence', 'NONE')
    if div == "BULLISH_DIV":
        score += 15
    elif div == "BEARISH_DIV":
        score -= 15
    
    return max(0, min(100, score))


def score_bb_continuous(bb):
    """
    Level 2: Continuous Bollinger Scoring (0-100)
    ใช้ %B แทน position text
    """
    percent_b = bb.get('percent_b', 0.5)
    if percent_b is None:
        return 50
    
    score = 100 - (percent_b * 100)
    
    bb_width = bb.get('bb_width', 0)
    if bb_width and bb_width < 0.03:
        score = 50
    
    return max(0, min(100, score))


def calculate_score_enhanced(ichi, macd, rsi_data, bb, df):
    """
    Level 3: Dynamic Weighted Score System
    รวมทุกข้อเสนอแนะ 3 ระดับ
    """
    rsi_series = rsi_data.get('rsi_series')
    divergence = detect_rsi_divergence(df, rsi_series)
    rsi_data['divergence'] = divergence
    
    regime = calculate_trend_regime(ichi)
    
    ichi_score = score_ichimoku_continuous(ichi)
    macd_score = score_macd_continuous(macd, ichi['price'])
    rsi_score = score_rsi_continuous(rsi_data)
    bb_score = score_bb_continuous(bb)
    
    # Dynamic Weights
    if regime == "STRONG_TREND":
        weights = {
            'ichimoku': 0.35,
            'macd': 0.25,
            'rsi': 0.15,
            'bb': 0.10,
            'divergence': 0.15
        }
    elif regime == "SIDEWAY":
        weights = {
            'ichimoku': 0.15,
            'macd': 0.20,
            'rsi': 0.30,
            'bb': 0.25,
            'divergence': 0.10
        }
    else:
        weights = {
            'ichimoku': 0.25,
            'macd': 0.25,
            'rsi': 0.20,
            'bb': 0.15,
            'divergence': 0.15
        }
    
    final_score = (
        ichi_score * weights['ichimoku'] +
        macd_score * weights['macd'] +
        rsi_score * weights['rsi'] +
        bb_score * weights['bb']
    )
    
    if divergence == "BULLISH_DIV":
        final_score += 8
    elif divergence == "BEARISH_DIV":
        final_score -= 8
    
    # Anti Double-Counting (variance dampening)
    scores = [ichi_score, macd_score, rsi_score, bb_score]
    avg_score = sum(scores) / len(scores)
    variance = sum((s - avg_score) ** 2 for s in scores) / len(scores)
    
    if variance > 400:
        final_score = 50 + (final_score - 50) * 0.7
    
    final_score = max(0, min(100, final_score))
    
    if final_score >= 80:
        sig, strength = "STRONG_BUY", "STRONG"
    elif final_score >= 65:
        sig, strength = "BUY", "MODERATE"
    elif final_score >= 45:
        sig, strength = "HOLD", "WEAK"
    elif final_score >= 30:
        sig, strength = "SELL", "MODERATE"
    else:
        sig, strength = "STRONG_SELL", "STRONG"
    
    return {
        "score": round(final_score, 1),
        "signal": sig,
        "strength": strength,
        "regime": regime,
        "divergence": divergence,
        "component_scores": {
            "ichimoku": round(ichi_score, 1),
            "macd": round(macd_score, 1),
            "rsi": round(rsi_score, 1),
            "bb": round(bb_score, 1)
        },
        "weights_used": {k: round(v, 2) for k, v in weights.items()}
    }


# ==================== MAIN ====================

def analyze_stock(symbol):
    """วิเคราะห์หุ้นตัวเดียว คืนค่า dict หรือ None"""
    df = fetch_yf_history(symbol, LOOKBACK_DAYS)
    if df is None or len(df) < 60:
        return None
    
    try:
        ichi = calculate_ichimoku(df)
        macd = calculate_macd(df)
        rsi = calculate_rsi_wilder(df)
        bb = calculate_bollinger(df)
        
        score_result = calculate_score_enhanced(ichi, macd, rsi, bb, df)
        
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
            "score": score_result['score'],
            "signal": score_result['signal'],
            "strength": score_result['strength'],
            "regime": score_result['regime'],
            "divergence": score_result['divergence'],
            "component_scores": score_result['component_scores'],
            "weights_used": score_result['weights_used'],
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        print(f"   ❌ Calculation error: {e}")
        return None


def main():
    print("=" * 70)
    print("🔬 SET Technical Analysis (Enhanced Edition)")
    print("   Features: Wilder RSI | Continuous Scoring | Divergence | Dynamic Weights")
    print(f"🕐  Started at: {datetime.now()}")
    print("=" * 70)
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    if not COMPANIES_JSON.exists():
        print("❌ companies.json not found. Run fetch.py first.")
        sys.exit(1)
    
    with open(COMPANIES_JSON, "r", encoding="utf-8") as f:
        companies = json.load(f)
    
    print(f"📋 Loaded {len(companies)} companies from companies.json")
    
    targets = companies
    
    if MAX_STOCKS > 0:
        targets = targets[:MAX_STOCKS]
        print(f"⚡ Limited to first {MAX_STOCKS} stocks")
    
    results = []
    watchlist = []
    sector_scores = {}
    
    total = len(targets)
    for i, comp in enumerate(targets, 1):
        symbol = comp['symbol']
        print(f"[{i:04d}/{total}] {symbol}...", end=" ", flush=True)
        
        result = analyze_stock(symbol)
        
        if result:
            results.append(result)
            print(f"✅ Score={result['score']} {result['signal']} [{result['regime']}]")
            
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
    
    # Sector Sentiment
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
    watchlist.sort(key=lambda x: x['score'], reverse=True)
    
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
