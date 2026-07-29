#!/usr/bin/env python3
"""
Dividend Consistency Checker (10 Consecutive Years)
==================================================
ตรวจสอบหุ้นที่จ่ายปันผลต่อเนื่อง 10 ปี จาก Yahoo Finance
รองรับ caching เพื่อลดเวลาในรอบถัดไป
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

# ============ CONFIGURATION ============
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
COMPANIES_CSV = DATA_DIR / "companies.csv"
CONSISTENT_CSV = DATA_DIR / "dividend_consistent_10y.csv"
CONSISTENT_JSON = DATA_DIR / "dividend_consistent_10y.json"
CACHE_FILE = DATA_DIR / "dividend_history_cache.json"
DIVIDEND_CSV = DATA_DIR / "dividend_stocks.csv"

REQUIRED_YEARS = 10          # จำนวนปีที่ต้องจ่ายต่อเนื่อง
MAX_WORKERS = 5              # จำกัด worker ป้องกัน rate limit
BATCH_SIZE = 5               # จำนวนตัวต่อ batch
DELAY_SEC = 2.0              # วินาทีพักระหว่าง batch
MAX_RETRIES = 2              # ลองใหม่ถ้า failed


def get_required_year_range():
    """
    คืนค่าช่วงปีที่ต้องตรวจสอบ
    กฎ: ถ้ายังไม่ถึง 30 มิ.ย. ของปีปัจจุบัน จะไม่บังคับปีปัจจุบัน
         (เพราะหุ้นไทยส่วนใหญ่ยังไม่ถึงวันประชุมผู้ถือหุ้น)
    """
    now = datetime.now()
    current_year = now.year

    # ถ้าถึงกลางปีแล้ว (หลัง มิ.ย.) รวมปีปัจจุบันได้
    if now.month >= 7:
        end_year = current_year
    else:
        end_year = current_year - 1

    start_year = end_year - REQUIRED_YEARS + 1
    return start_year, end_year


def fetch_dividend_history(symbol: str):
    """
    ดึงประวัติเงินปันผลทั้งหมดของหุ้นตัวหนึ่งจาก Yahoo Finance
    คืนค่า dict หรือ None ถ้าไม่มีข้อมูล
    """
    ticker_str = f"{symbol}.BK"

    for attempt in range(MAX_RETRIES):
        try:
            ticker = yf.Ticker(ticker_str)
            # .dividends คืน pandas Series: index=Date, values=Dividend
            divs = ticker.dividends

            if divs is None or divs.empty:
                return {"symbol": symbol, "error": "no_dividend_data"}

            # แปลง timezone ให้เป็น naive เพื่อง่ายต่อการเปรียบเทียบ
            if divs.index.tz:
                divs.index = divs.index.tz_localize(None)

            # ดึงเฉพาะ 12 ปีล่าสุด (buffer)
            cutoff_date = datetime.now() - timedelta(days=365 * 12)
            recent_divs = divs[divs.index >= cutoff_date]

            if recent_divs.empty:
                return {"symbol": symbol, "error": "no_recent_dividends"}

            # นับจำนวนปีที่มีการจ่ายปันผล (มีมากกว่า 0 บาท)
            years_with_div = set()
            for date, amount in recent_divs.items():
                if amount and amount > 0:
                    years_with_div.add(date.year)

            return {
                "symbol": symbol,
                "years_with_dividend": sorted(years_with_div),
                "total_years_found": len(years_with_div),
                "latest_dividend_date": str(recent_divs.index[-1].date()),
                "oldest_dividend_date": str(recent_divs.index[0].date()),
                "raw_dividend_count": len(recent_divs),
            }

        except Exception as e:
            err_msg = str(e).lower()
            # ถ้า Yahoo ส่งคืน 404/Not Found ไม่ต้อง retry ซ้ำ
            if "not found" in err_msg or "no data" in err_msg:
                return {"symbol": symbol, "error": "ticker_not_found"}
            if attempt < MAX_RETRIES - 1:
                time.sleep(2)
                continue
            return {"symbol": symbol, "error": str(e)}

    return {"symbol": symbol, "error": "max_retries_exceeded"}


def load_cache():
    """โหลด cache ถ้ามี (ลดเวลารอบถัดไปได้มาก)"""
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    """บันทึก cache"""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def main():
    print("=" * 70)
    print("📅 Dividend Consistency Checker (10 Consecutive Years)")
    print("   Source: Yahoo Finance (.BK) | With local caching")
    print("=" * 70)

    if not COMPANIES_CSV.exists():
        print(f"❌ {COMPANIES_CSV} not found. Run fetch.py first.")
        sys.exit(1)

    df_companies = pd.read_csv(COMPANIES_CSV, dtype=str)
    symbols = df_companies["symbol"].dropna().unique().tolist()
    print(f"📋 Loaded {len(symbols)} symbols from companies.csv")

    start_year, end_year = get_required_year_range()
    required_years = set(range(start_year, end_year + 1))
    print(f"🔍 Required consecutive years: {start_year} – {end_year} ({len(required_years)} years)")

    # โหลด cache
    cache = load_cache()
    print(f"💾 Cache entries: {len(cache)}")

    results = []
    errors = []
    processed = 0

    print(f"\n[{datetime.now()}] 🚀 Starting fetch (workers={MAX_WORKERS}, delay={DELAY_SEC}s)...")

    # ประมวลผลเป็นชุดๆ เพื่อควบคุม rate
    batch_total = (len(symbols) + (BATCH_SIZE * MAX_WORKERS) - 1) // (BATCH_SIZE * MAX_WORKERS)

    for batch_idx in range(batch_total):
        batch_start = batch_idx * BATCH_SIZE * MAX_WORKERS
        batch_end = batch_start + BATCH_SIZE * MAX_WORKERS
        batch_symbols = symbols[batch_start:batch_end]

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_sym = {
                executor.submit(fetch_dividend_history, sym): sym
                for sym in batch_symbols
            }

            for future in as_completed(future_to_sym):
                res = future.result()
                sym = res["symbol"]
                processed += 1

                if "error" in res:
                    errors.append(res)
                else:
                    cache[sym] = res
                    results.append(res)

                if processed % 50 == 0:
                    print(f"   ... processed {processed}/{len(symbols)}")

        # พักระหว่าง batch (ยกเว้น batch สุดท้าย)
        if batch_idx < batch_total - 1:
            time.sleep(DELAY_SEC)

    # บันทึก cache ทันที (กันพังกลางคัน)
    save_cache(cache)
    print(f"\n✅ Summary: Processed={processed} | Success={len(results)} | Errors={len(errors)}")

    # ============ กรองเฉพาะหุ้นที่จ่ายต่อเนื่อง 10 ปี ============
    consistent_stocks = []

    for res in results:
        years = set(res.get("years_with_dividend", []))
        missing_years = required_years - years

        # ถ้าไม่ขาดปีใดเลย = จ่ายต่อเนื่องครบ
        is_consistent = len(missing_years) == 0

        res["required_years"] = sorted(required_years)
        res["missing_years"] = sorted(missing_years)
        res["is_consistent_10y"] = is_consistent

        if is_consistent:
            consistent_stocks.append(res)

    print(f"\n🎯 Stocks with {REQUIRED_YEARS} consecutive years of dividends: {len(consistent_stocks)}")

    # บันทึกไฟล์ (ถ้าไม่มีผลลัพธ์ก็บันทึกไฟล์ว่างไว้)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not consistent_stocks:
        pd.DataFrame(columns=["symbol"]).to_csv(CONSISTENT_CSV, index=False, encoding="utf-8-sig")
        with open(CONSISTENT_JSON, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        print("   (Saved empty files)")
        return

    # Merge กับข้อมูลบริษัท (ชื่อ, ตลาด, อุตสาหกรรม)
    df_consistent = pd.DataFrame(consistent_stocks)
    df_merged = df_consistent.merge(
        df_companies[["symbol", "company_name_en", "market", "industry", "sector"]],
        on="symbol",
        how="left"
    )

    # Merge กับข้อมูลปันผลล่าสุด (yield, price) ถ้ามี
    if DIVIDEND_CSV.exists():
        df_div = pd.read_csv(DIVIDEND_CSV, dtype=str)
        div_cols = ["symbol", "dividend_yield_pct", "dividend_rate", "last_price", "payout_ratio"]
        div_cols = [c for c in div_cols if c in df_div.columns]
        if div_cols:
            df_merged = df_merged.merge(df_div[div_cols], on="symbol", how="left")

    # จัดเรียงตาม symbol
    df_merged = df_merged.sort_values("symbol").reset_index(drop=True)

    # บันทึก
    df_merged.to_csv(CONSISTENT_CSV, index=False, encoding="utf-8-sig")
    df_merged.to_json(CONSISTENT_JSON, orient="records", force_ascii=False, indent=2)

    print(f"\n💾 Saved:")
    print(f"   CSV : {CONSISTENT_CSV}")
    print(f"   JSON: {CONSISTENT_JSON}")
    print(f"   Cache: {CACHE_FILE}")

    # แสดงตัวอย่าง 10 ตัวแรก
    display_cols = ["symbol", "company_name_en", "years_with_dividend"]
    if "dividend_yield_pct" in df_merged.columns:
        display_cols.append("dividend_yield_pct")
    print(f"\n📊 Sample results (top 10):")
    print(df_merged[display_cols].head(10).to_string(index=False))

    # สรุปตลาด
    if "market" in df_merged.columns:
        print(f"\n📈 By Market:")
        print(df_merged["market"].value_counts().to_string())

    print("\n" + "=" * 70)
    print("✅ Done!")
    print(f"   Total checked    : {len(symbols)}")
    print(f"   With dividend data: {len(results)}")
    print(f"   Consistent 10Y   : {len(consistent_stocks)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
