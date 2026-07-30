#!/usr/bin/env python3
"""
Enhanced Dividend Fetcher with REAL Payment Date
================================================
ดึงข้อมูลปันผลจาก Yahoo Finance + Payment Date จาก SET Official API
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

# ============ CONFIGURATION ============
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
COMPANIES_CSV = DATA_DIR / "companies.csv"
DIVIDEND_CSV = DATA_DIR / "dividend_stocks.csv"
DIVIDEND_JSON = DATA_DIR / "dividend_stocks.json"
PAYMENT_DATE_CACHE = DATA_DIR / "payment_date_cache.json"

MIN_DIVIDEND_YIELD = 0.0
MAX_WORKERS = 5          # จำกัด worker ป้องกัน rate limit
DELAY_BETWEEN_BATCH = 1  # วินาทีพักระหว่าง batch

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html",
}


# ============ PAYMENT DATE FETCHERS ============

def fetch_payment_date_from_set_api(symbol: str):
    """
    ดึง Payment Date จาก SET Official API (แม่นยำสุด)
    Endpoint: /api/set/stock/corporate-action
    """
    url = f"https://www.set.or.th/api/set/stock/corporate-action?symbol={symbol}&locale=th&page=0&size=20"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        actions = data.get("data", []) or data.get("content", []) or []
        
        for item in actions:
            action_type = str(item.get("actionType", "")).lower()
            # หา action ที่เป็นเงินปันผล (Cash Dividend / เงินสด)
            if any(k in action_type for k in ["dividend", "เงินปันผล", "cash"]):
                # ลองหา field paymentDate หรือ payDate หรือ similar
                payment_date = (
                    item.get("paymentDate")
                    or item.get("payDate")
                    or item.get("payment date")
                    or item.get("pay date")
                )
                if payment_date and payment_date not in ["-", "", None]:
                    # รูปแบบ ISO: 2025-04-25T00:00:00.000+07:00
                    return str(payment_date)[:10]
        return None
    except Exception:
        return None


def fetch_payment_date_from_set_html(symbol: str):
    """
    Fallback: Scrape จากหน้า Corporate Action ของ SET (HTML)
    """
    url = f"https://www.set.or.th/th/market/product-and-services/stocks/corporate-actions.html?symbol={symbol}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        dfs = pd.read_html(resp.text)
        
        thai_months = {
            "ม.ค.": "01", "ก.พ.": "02", "มี.ค.": "03", "เม.ย.": "04",
            "พ.ค.": "05", "มิ.ย.": "06", "ก.ค.": "07", "ส.ค.": "08",
            "ก.ย.": "09", "ต.ค.": "10", "พ.ย.": "11", "ธ.ค.": "12"
        }
        
        for df in dfs:
            cols = [str(c) for c in df.columns]
            # หา table ที่มีคอลัมน์ "จ่ายเงินปันผล" / "Payment Date" / "วันจ่าย"
            pay_col = None
            for c in cols:
                c_lower = c.lower()
                if any(k in c_lower for k in ["payment", "จ่าย", "pay date", "วันจ่าย"]):
                    pay_col = c
                    break
            
            if pay_col and not df.empty:
                val = str(df.iloc[0][pay_col]).strip()
                if val and val != "-":
                    # แปลงรูปแบบไทย: "25 เม.ย. 2025" → "2025-04-25"
                    for th, num in thai_months.items():
                        if th in val:
                            parts = val.replace(th, num).split()
                            if len(parts) == 3:
                                return f"{parts[2]}-{num}-{parts[0].zfill(2)}"
                    # รูปแบบสากล: 25/04/2025
                    if "/" in val:
                        dt = datetime.strptime(val, "%d/%m/%Y")
                        return dt.strftime("%Y-%m-%d")
                    if "-" in val and len(val) == 10:
                        return val
        return None
    except Exception:
        return None


def estimate_payment_date(ex_div_date: str):
    """
    Fallback สุดท้าย: ประมาณจาก Ex Date + 20 วัน
    (หุ้นไทยส่วนใหญ่จ่ายภายใน 15-30 วันหลัง XD)
    """
    if not ex_div_date:
        return None
    try:
        ex_dt = pd.to_datetime(ex_div_date)
        estimated = ex_dt + timedelta(days=20)
        return estimated.strftime("%Y-%m-%d")
    except Exception:
        return None


def get_payment_date(symbol: str, ex_div_date: str, cache: dict):
    """
    หา Payment Date จากหลายแหล่ง เรียงตามความน่าเชื่อถือ:
    1. Cache → 2. SET API → 3. SET HTML → 4. ประมาณจาก Ex Date
    """
    # 1. Cache
    if symbol in cache:
        return cache[symbol]
    
    # 2. SET API (แม่นยำสุด)
    result = fetch_payment_date_from_set_api(symbol)
    if result:
        cache[symbol] = result
        return result
    
    # 3. SET HTML (fallback)
    result = fetch_payment_date_from_set_html(symbol)
    if result:
        cache[symbol] = result
        return result
    
    # 4. ประมาณจาก Ex Date + 20 วัน (บันทึกแยกไว้ว่าเป็นการประมาณ)
    result = estimate_payment_date(ex_div_date)
    if result:
        cache[symbol] = result  # ยังบันทึก cache ไว้ใช้รอบถัดไป
        return result
    
    cache[symbol] = None
    return None


# ============ DIVIDEND FETCHER ============

def analyze_dividend_history(ticker_obj):
    """วิเคราะห์ประวัติเงินปันผลจาก ticker.dividends"""
    try:
        divs = ticker_obj.dividends
        if divs is None or divs.empty:
            return {}
        
        if divs.index.tz:
            divs.index = divs.index.tz_localize(None)
        
        df_div = pd.DataFrame({"date": divs.index, "amount": divs.values})
        df_div["year"] = df_div["date"].dt.year
        annual = df_div.groupby("year")["amount"].sum().sort_index()
        
        if len(annual) < 2:
            return {}
        
        # CAGR
        cagrs = {}
        for period in [3, 5, 10]:
            if len(annual) >= period:
                start = annual.iloc[-period]
                end = annual.iloc[-1]
                if start > 0:
                    cagrs[f"dividend_cagr_{period}y"] = round(
                        ((end / start) ** (1 / period) - 1) * 100, 2
                    )
        
        # Frequency
        latest_year = annual.index[-1]
        freq = len(df_div[df_div["year"] == latest_year])
        if freq == 0 and len(annual) >= 2:
            freq = len(df_div[df_div["year"] == annual.index[-2]])
        
        # Consecutive years
        years = sorted(annual.index.tolist())
        consecutive = 1
        for i in range(len(years) - 1, 0, -1):
            if years[i] - years[i - 1] == 1:
                consecutive += 1
            else:
                break
        
        total_10y = annual.tail(10).sum() if len(annual) >= 10 else annual.sum()
        
        return {
            "dividend_frequency_per_year": freq,
            "consecutive_dividend_years": consecutive,
            "annual_dividend_latest": round(annual.iloc[-1], 4),
            "annual_dividend_10y_total": round(total_10y, 4),
            **cagrs,
        }
    except Exception:
        return {}


def fetch_single_symbol(symbol: str, payment_cache: dict):
    """ดึงข้อมูลปันผลแบบครบถ้วน + Payment Date จริง"""
    try:
        ticker = yf.Ticker(f"{symbol}.BK")
        info = ticker.info or {}
        
        # --- Basic metrics ---
        div_yield = info.get("dividendYield")
        div_rate = info.get("trailingAnnualDividendRate")
        payout = info.get("payoutRatio")
        ex_div_ts = info.get("exDividendDate")
        last_div_val = info.get("lastDividendValue")
        five_y_avg = info.get("fiveYearAvgDividendYield")
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        high_52w = info.get("fiftyTwoWeekHigh")
        low_52w = info.get("fiftyTwoWeekLow")
        
        if div_yield is None and div_rate and price and price > 0:
            div_yield = div_rate / price
        
        if div_yield is None or div_yield <= 0:
            return None
        
        # --- Dates ---
        ex_div_date = None
        if ex_div_ts:
            ex_div_date = pd.to_datetime(ex_div_ts, unit="s").strftime("%Y-%m-%d")
        
        # ✅ Payment Date จาก SET (แม่นยำ) หรือประมาณจาก Ex Date
        payment_date = get_payment_date(symbol, ex_div_date, payment_cache)
        
        # ตรวจสอบว่าเป็น estimated หรือไม่
        is_estimated = False
        if payment_date and ex_div_date:
            pay_dt = pd.to_datetime(payment_date)
            ex_dt = pd.to_datetime(ex_div_date)
            if (pay_dt - ex_dt).days == 20:
                # น่าจะเป็นการประมาณ (พอดี 20 วัน)
                is_estimated = True
        
        # --- Calculated metrics ---
        yield_vs_5y = None
        if five_y_avg and five_y_avg > 0:
            yield_vs_5y = round((div_yield * 100) - five_y_avg, 2)
        
        yield_on_low = round((div_rate / low_52w) * 100, 2) if (low_52w and low_52w > 0) else None
        yield_on_high = round((div_rate / high_52w) * 100, 2) if (high_52w and high_52w > 0) else None
        
        # Safety Score
        safety_score = None
        if payout is not None and 0 <= payout <= 1:
            if payout < 0.4:
                safety_score = 5
            elif payout < 0.6:
                safety_score = 4
            elif payout < 0.8:
                safety_score = 3
            elif payout <= 1.0:
                safety_score = 2
            else:
                safety_score = 1
        
        # History analysis
        history = analyze_dividend_history(ticker)
        
        return {
            "symbol": symbol,
            "company_name_en": "",  # จะ merge ทีหลัง
            "dividend_yield_pct": round(div_yield * 100, 2),
            "dividend_rate_baht": div_rate,
            "last_price": price,
            "payout_ratio": round(payout, 4) if payout else None,
            "safety_score": safety_score,
            "ex_dividend_date": ex_div_date,           # วันขึ้น XD
            "payment_date": payment_date,               # ✅ วันจ่ายเงินจริง
            "payment_date_estimated": is_estimated,     # บอกว่าประมาณหรือไม่
            "last_dividend_value": last_div_val,
            "five_year_avg_yield_pct": five_y_avg,
            "yield_vs_5y_avg": yield_vs_5y,
            "yield_on_52w_low_pct": yield_on_low,
            "yield_on_52w_high_pct": yield_on_high,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            **history,
        }
        
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


# ============ MAIN ============

def main():
    print("=" * 70)
    print("💰 Enhanced Dividend Fetcher with REAL Payment Date")
    print("   Payment Date sources: SET API → SET HTML → ExDate+20d (est.)")
    print("=" * 70)
    
    if not COMPANIES_CSV.exists():
        print(f"❌ {COMPANIES_CSV} not found. Run fetch.py first.")
        sys.exit(1)
    
    df_companies = pd.read_csv(COMPANIES_CSV, dtype=str)
    symbols = df_companies["symbol"].dropna().unique().tolist()
    print(f"📋 Loaded {len(symbols)} symbols")
    
    # โหลด cache
    payment_cache = {}
    if PAYMENT_DATE_CACHE.exists():
        with open(PAYMENT_DATE_CACHE, "r", encoding="utf-8") as f:
            payment_cache = json.load(f)
        print(f"💾 Payment date cache: {len(payment_cache)} entries")
    
    results = []
    errors = []
    
    print(f"\n[{datetime.now()}] 🚀 Starting fetch (workers={MAX_WORKERS})...")
    print("   ⏳ Note: Payment date scraping may take 15-25 minutes for all stocks")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_sym = {
            executor.submit(fetch_single_symbol, sym, payment_cache): sym
            for sym in symbols
        }
        
        for i, future in enumerate(as_completed(future_to_sym)):
            res = future.result()
            sym = future_to_sym[future]
            
            if res and "error" not in res:
                results.append(res)
            elif res and "error" in res:
                errors.append(res["symbol"])
            
            if (i + 1) % 50 == 0:
                print(f"   ... {i + 1}/{len(symbols)} done (cached payment dates: {len(payment_cache)})")
                # บันทึก cache ระหว่างทาง (กันพังกลางคัน)
                with open(PAYMENT_DATE_CACHE, "w", encoding="utf-8") as f:
                    json.dump(payment_cache, f, ensure_ascii=False, indent=2)
            
            # พักเล็กน้อยทุกๆ 5 ตัว
            if (i + 1) % 5 == 0:
                time.sleep(0.5)
    
    # บันทึก cache สุดท้าย
    with open(PAYMENT_DATE_CACHE, "w", encoding="utf-8") as f:
        json.dump(payment_cache, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Success: {len(results)} | ❌ Errors: {len(errors)}")
    
    # กรองและ merge
    df = pd.DataFrame(results)
    df = df[df["dividend_yield_pct"] > MIN_DIVIDEND_YIELD]
    
    df_merged = df.merge(
        df_companies[["symbol", "company_name_en", "market", "industry", "sector"]],
        on="symbol",
        how="left"
    )
    
    # จัดเรียง: Safety สูง → Yield สูง
    df_merged = df_merged.sort_values(
        by=["safety_score", "dividend_yield_pct"],
        ascending=[False, False]
    ).reset_index(drop=True)
    
    # บันทึก
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_merged.to_csv(DIVIDEND_CSV, index=False, encoding="utf-8-sig")
    df_merged.to_json(DIVIDEND_JSON, orient="records", force_ascii=False, indent=2)
    
    # สรุป Payment Date
    has_payment = df_merged["payment_date"].notna().sum()
    estimated_count = df_merged["payment_date_estimated"].sum() if "payment_date_estimated" in df_merged.columns else 0
    
    print(f"\n💾 Saved {len(df_merged)} dividend stocks:")
    print(f"   CSV : {DIVIDEND_CSV}")
    print(f"   JSON: {DIVIDEND_JSON}")
    print(f"\n📅 Payment Date Coverage:")
    print(f"   • มีข้อมูล: {has_payment}/{len(df_merged)} ({has_payment/len(df_merged)*100:.1f}%)")
    print(f"   • จาก SET (แม่นยำ): {has_payment - estimated_count} ตัว")
    print(f"   • ประมาณ (ExDate+20d): {estimated_count} ตัว")
    
    # ตัวอย่าง 10 ตัวแรก
    display_cols = ["symbol", "company_name_en", "dividend_yield_pct", "payment_date", "payment_date_estimated"]
    print(f"\n📊 Top 10 (with Payment Date):")
    print(df_merged[display_cols].head(10).to_string(index=False))
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
