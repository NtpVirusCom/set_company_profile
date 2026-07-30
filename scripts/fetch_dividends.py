#!/usr/bin/env python3
"""
Dividend Data Fetcher for Thai Stocks
ดึงข้อมูลปันผลจาก Yahoo Finance สำหรับหุ้นไทย (.BK)
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

# ============ CONFIGURATION ============
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
COMPANIES_CSV = DATA_DIR / "companies.csv"
DIVIDEND_CSV = DATA_DIR / "dividend_stocks.csv"
DIVIDEND_JSON = DATA_DIR / "dividend_stocks.json"
RAW_DIVIDEND_JSON = DATA_DIR / "dividend_raw.json"

# เกณฑ์ขั้นต่ำสำหรับคัดว่าเป็นหุ้นปันผล (0 = มีปันผลอะไรก็ได้, 3 = 3% ขึ้นไป)
MIN_DIVIDEND_YIELD = 0.0


def fetch_single_symbol(symbol: str):
    """
    ดึงข้อมูลปันผลของหุ้นตัวเดียวจาก Yahoo Finance
    หุ้นไทยต้องเติม .BK (เช่น PTT.BK, SCB.BK)
    """
    try:
        ticker = yf.Ticker(f"{symbol}.BK")
        info = ticker.info or {}

        div_yield = info.get("dividendYield")          # อัตราปันผลล่าสุด (เช่น 0.05 = 5%)
        div_rate = info.get("trailingAnnualDividendRate")  # เงินปันผลต่อหุ้น/ปี
        payout = info.get("payoutRatio")               # อัตราจ่ายเงินปันผล
        ex_div = info.get("exDividendDate")            # วันขึ้นเครื่องหมาย XD
        price = info.get("currentPrice") or info.get("regularMarketPrice")

        # ถ้าไม่มี dividendYield แต่มีเงินปันผล + ราคา ให้คำนวณเอง
        if div_yield is None and div_rate and price and price > 0:
            div_yield = div_rate / price

        if div_yield is None:
            return None

        return {
            "symbol": symbol,
            #"dividend_yield_pct": round(div_yield * 100, 2),
            "dividend_yield_pct": round(div_yield, 2),
            "dividend_rate": div_rate,
            "last_price": price,
            #"payout_ratio": round(payout, 4) if payout else None,
            "payout_ratio": round(payout * 100, 4) if payout else None,
            "ex_dividend_date": pd.to_datetime(ex_div, unit="s").isoformat() if ex_div else None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        # บางตัวอาจไม่มีข้อมูลใน Yahoo Finance ให้ข้ามไป
        return {"symbol": symbol, "error": str(e)}


def fetch_all_dividends(symbols, max_workers=8, delay=0.5):
    """
    ดึงข้อมูลปันผลแบบ Multi-thread พร้อม Delay เล็กน้อยป้องกัน Rate Limit
    """
    results = []
    errors = []

    print(f"[{datetime.now()}] 🚀 Fetching dividend data for {len(symbols)} symbols...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # ส่งงานทีละชุดพร้อม delay
        future_to_sym = {}
        for i, sym in enumerate(symbols):
            future = executor.submit(fetch_single_symbol, sym)
            future_to_sym[future] = sym
            if i % max_workers == 0 and i > 0:
                time.sleep(delay)  # พักเล็กน้อยทุกๆ ชุด

        for future in as_completed(future_to_sym):
            res = future.result()
            if res and "error" not in res:
                results.append(res)
            elif res and "error" in res:
                errors.append(res["symbol"])

    print(f"   ✅ Success: {len(results)} | ❌ Failed/No data: {len(errors)}")
    if errors:
        print(f"   Missing symbols (first 10): {errors[:10]}")
    return results


def main():
    print("=" * 70)
    print("💰 Thai Dividend Stock Filter")
    print("=" * 70)

    if not COMPANIES_CSV.exists():
        print(f"❌ {COMPANIES_CSV} not found. Run fetch.py first.")
        sys.exit(1)

    # 1) อ่านรายชื่อบริษัทจาก fetch.py
    df_companies = pd.read_csv(COMPANIES_CSV, dtype=str)
    symbols = df_companies["symbol"].dropna().unique().tolist()
    print(f"📋 Loaded {len(symbols)} symbols from companies.csv")

    # 2) ดึงข้อมูลปันผล
    raw_data = fetch_all_dividends(symbols, max_workers=8, delay=0.3)

    # 3) บันทึก Raw Data (สำหรับ Debug)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(RAW_DIVIDEND_JSON, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)

    # 4) กรองเฉพาะหุ้นปันผล (yield > MIN_DIVIDEND_YIELD)
    df_div = pd.DataFrame(raw_data)
    df_div = df_div[df_div["dividend_yield_pct"] > MIN_DIVIDEND_YIELD]

    # 5) Merge กับข้อมูลบริษัท (ชื่อ, อุตสาหกรรม, ตลาด)
    df_merged = df_div.merge(
        df_companies[["symbol", "company_name_en", "market", "industry", "sector"]],
        on="symbol",
        how="left"
    )

    # จัดเรียงตาม Dividend Yield สูง → ต่ำ
    df_merged = df_merged.sort_values("dividend_yield_pct", ascending=False).reset_index(drop=True)

    # 6) บันทึกไฟล์
    df_merged.to_csv(DIVIDEND_CSV, index=False, encoding="utf-8-sig")
    df_merged.to_json(DIVIDEND_JSON, orient="records", force_ascii=False, indent=2)

    print(f"\n💾 Saved {len(df_merged)} dividend stocks:")
    print(f"   CSV : {DIVIDEND_CSV}")
    print(f"   JSON: {DIVIDEND_JSON}")
    print(f"\n📊 Top 5 Dividend Yields:")
    print(df_merged[["symbol", "company_name_en", "dividend_yield_pct"]].head().to_string(index=False))


if __name__ == "__main__":
    main()
