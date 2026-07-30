#!/usr/bin/env python3
"""
Enhanced Dividend Data Fetcher
ดึงข้อมูลปันผลแบบเต็มรูปแบบจาก Yahoo Finance
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
COMPANIES_CSV = DATA_DIR / "companies.csv"
DIVIDEND_CSV = DATA_DIR / "dividend_stocks.csv"
DIVIDEND_JSON = DATA_DIR / "dividend_stocks.json"

MIN_DIVIDEND_YIELD = 0.0


def analyze_dividend_history(ticker_obj, symbol: str):
    """
    วิเคราะห์ประวัติเงินปันผลย้อนหลังจาก ticker.dividends
    คืนค่า dict ของ metrics ที่คำนวณได้
    """
    try:
        divs = ticker_obj.dividends
        if divs is None or divs.empty:
            return {}

        # แปลง timezone
        if divs.index.tz:
            divs.index = divs.index.tz_localize(None)

        # --- 1. สรุปเงินปันผลรายปี ---
        df_div = pd.DataFrame({"date": divs.index, "amount": divs.values})
        df_div["year"] = df_div["date"].dt.year
        annual = df_div.groupby("year")["amount"].sum().sort_index()

        if len(annual) < 2:
            return {}

        # --- 2. CAGR 3Y, 5Y, 10Y ---
        cagrs = {}
        for period in [3, 5, 10]:
            if len(annual) >= period:
                start = annual.iloc[-period]
                end = annual.iloc[-1]
                if start > 0:
                    cagrs[f"dividend_cagr_{period}y"] = round(
                        ((end / start) ** (1 / period) - 1) * 100, 2
                    )

        # --- 3. จำนวนครั้งจ่ายต่อปี (ล่าสุด) ---
        latest_year = annual.index[-1]
        freq = len(df_div[df_div["year"] == latest_year])
        # ถ้าปีปัจจุบันยังไม่จบ อาจน้อยกว่าปกติ ให้ดูปีก่อนแทน
        if freq == 0 and len(annual) >= 2:
            freq = len(df_div[df_div["year"] == annual.index[-2]])

        # --- 4. ปีที่จ่ายต่อเนื่องล่าสุด (Consecutive Years) ---
        years = sorted(annual.index.tolist())
        consecutive = 1
        for i in range(len(years) - 1, 0, -1):
            if years[i] - years[i - 1] == 1:
                consecutive += 1
            else:
                break

        # --- 5. เงินปันผลรวม 10 ปี ---
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


def fetch_single_symbol(symbol: str):
    """ดึงข้อมูลปันผลแบบครบถ้วน"""
    try:
        ticker = yf.Ticker(f"{symbol}.BK")
        info = ticker.info or {}

        # --- Basic Info ---
        div_yield = info.get("dividendYield")
        div_rate = info.get("trailingAnnualDividendRate")
        payout = info.get("payoutRatio")
        ex_div_ts = info.get("exDividendDate")
        last_div_date_ts = info.get("lastDividendDate")
        last_div_val = info.get("lastDividendValue")
        five_y_avg = info.get("fiveYearAvgDividendYield")
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        high_52w = info.get("fiftyTwoWeekHigh")
        low_52w = info.get("fiftyTwoWeekLow")

        # คำนวณ yield เองถ้าไม่มี
        if div_yield is None and div_rate and price and price > 0:
            div_yield = div_rate / price

        if div_yield is None or div_yield <= 0:
            return None

        # --- แปลงวันที่ ---
        ex_div_date = None
        if ex_div_ts:
            ex_div_date = pd.to_datetime(ex_div_ts, unit="s").strftime("%Y-%m-%d")

        last_div_date = None
        if last_div_date_ts:
            last_div_date = pd.to_datetime(last_div_date_ts, unit="s").strftime("%Y-%m-%d")

        # --- คำนวณเพิ่มเติม ---
        yield_vs_5y = None
        if five_y_avg and five_y_avg > 0:
            yield_vs_5y = round((div_yield * 100) - five_y_avg, 2)

        yield_on_low = round((div_rate / low_52w) * 100, 2) if (low_52w and low_52w > 0) else None
        yield_on_high = round((div_rate / high_52w) * 100, 2) if (high_52w and high_52w > 0) else None

        # --- วิเคราะห์ประวัติ ---
        history_metrics = analyze_dividend_history(ticker, symbol)

        # --- Safety Score ---
        safety_score = None
        if payout is not None and 0 <= payout <= 1:
            # payout < 40% = 5 ดาว, < 60% = 4 ดาว, < 80% = 3 ดาว, < 100% = 2 ดาว, > 100% = 1 ดาว
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

        result = {
            "symbol": symbol,
            #"dividend_yield_pct": round(div_yield * 100, 2),
            "dividend_yield_pct": round(div_yield, 2),
            "dividend_rate_baht": div_rate,
            "last_price": price,
            #"payout_ratio": round(payout, 4) if payout else None,
            "payout_ratio": round(payout * 100, 2) if payout else None,
            "safety_score": safety_score,
            "ex_dividend_date": ex_div_date,
            "last_dividend_date": last_div_date,
            "last_dividend_value": last_div_val,
            "five_year_avg_yield_pct": five_y_avg,
            "yield_vs_5y_avg": yield_vs_5y,
            "yield_on_52w_low_pct": yield_on_low,
            "yield_on_52w_high_pct": yield_on_high,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            **history_metrics,
        }

        return result

    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


def main():
    print("=" * 70)
    print("💰 Enhanced Dividend Fetcher (Full Metrics)")
    print("=" * 70)

    if not COMPANIES_CSV.exists():
        print(f"❌ {COMPANIES_CSV} not found.")
        sys.exit(1)

    df_companies = pd.read_csv(COMPANIES_CSV, dtype=str)
    symbols = df_companies["symbol"].dropna().unique().tolist()
    print(f"📋 {len(symbols)} symbols loaded")

    results = []
    errors = []

    print(f"[{datetime.now()}] 🚀 Fetching... (this may take 10-15 min)")

    with ThreadPoolExecutor(max_workers=5) as executor:
        for i, sym in enumerate(symbols):
            future = executor.submit(fetch_single_symbol, sym)
            res = future.result()
            if res and "error" not in res:
                results.append(res)
            elif res and "error" in res:
                errors.append(res["symbol"])

            if (i + 1) % 50 == 0:
                print(f"   ... {i + 1}/{len(symbols)} done")

            # พักเล็กน้อยทุกตัว
            if (i + 1) % 5 == 0:
                time.sleep(1)

    print(f"\n✅ Success: {len(results)} | ❌ Errors: {len(errors)}")

    # กรองเฉพาะที่มี yield > 0
    df = pd.DataFrame(results)
    df = df[df["dividend_yield_pct"] > MIN_DIVIDEND_YIELD]

    # Merge กับ companies
    df_merged = df.merge(
        df_companies[["symbol", "company_name_en", "market", "industry", "sector"]],
        on="symbol",
        how="left"
    )

    # จัดเรียง: safety_score สูง → yield สูง
    df_merged = df_merged.sort_values(
        by=["safety_score", "dividend_yield_pct"],
        ascending=[False, False]
    ).reset_index(drop=True)

    # บันทึก
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_merged.to_csv(DIVIDEND_CSV, index=False, encoding="utf-8-sig")
    df_merged.to_json(DIVIDEND_JSON, orient="records", force_ascii=False, indent=2)

    print(f"\n💾 Saved {len(df_merged)} stocks:")
    print(f"   CSV : {DIVIDEND_CSV}")
    print(f"   JSON: {DIVIDEND_JSON}")

    # สรุป
    print(f"\n📊 Summary:")
    print(f"   • Avg Yield: {df_merged['dividend_yield_pct'].mean():.2f}%")
    print(f"   • Avg Safety Score: {df_merged['safety_score'].mean():.1f}/5")
    if "dividend_cagr_5y" in df_merged.columns:
        valid_cagr = df_merged["dividend_cagr_5y"].dropna()
        if len(valid_cagr) > 0:
            print(f"   • Avg 5Y CAGR: {valid_cagr.mean():.2f}%")
    print(f"   • 5-Star Safety: {(df_merged['safety_score'] == 5).sum()} stocks")
    print("=" * 70)


if __name__ == "__main__":
    main()
