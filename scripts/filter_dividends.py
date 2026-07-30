#!/usr/bin/env python3
"""
Dividend Stock Filter
=====================
กรองหุ้นปันผลตามเกณฑ์:
  1. จ่ายปันผลต่อเนื่อง >= 5 ปี
  2. Dividend Yield >= 5%
  3. Payout Ratio <= 80%
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# ============ CONFIGURATION ============
BASE_DIR = Path(__file__).parent.parent      # ย้อนกลับไป root ของ repo
DATA_DIR = BASE_DIR / "data"
INPUT_CSV = DATA_DIR / "dividend_stocks.csv"
OUTPUT_CSV = DATA_DIR / "filtered_dividends.csv"
OUTPUT_JSON = DATA_DIR / "filtered_dividends.json"

# เกณฑ์การคัดกรอง
MIN_CONSECUTIVE_YEARS = 5      # จ่ายต่อเนื่องขั้นต่ำ 5 ปี
MIN_DIVIDEND_YIELD = 5.0       # Yield ขั้นต่ำ 5%
MAX_PAYOUT_RATIO = 80.0        # Payout ไม่เกิน 80%


def main():
    print("=" * 70)
    print("🔍 Dividend Stock Filter")
    print(f"   Criteria: ≥{MIN_CONSECUTIVE_YEARS}Y consecutive | Yield ≥{MIN_DIVIDEND_YIELD}% | Payout ≤{MAX_PAYOUT_RATIO}%")
    print("=" * 70)

    if not INPUT_CSV.exists():
        print(f"❌ {INPUT_CSV} not found. Please run fetch_dividends.py first.")
        return

    # โหลดข้อมูล
    df = pd.read_csv(INPUT_CSV, dtype=str)
    print(f"📥 Loaded {len(df)} stocks from dividend_stocks.csv")

    # แปลงคอลัมน์ตัวเลข ( coerce จะแปลงค่าผิดพลาดเป็น NaN )
    df["consecutive_dividend_years"] = pd.to_numeric(df["consecutive_dividend_years"], errors="coerce")
    df["dividend_yield_pct"] = pd.to_numeric(df["dividend_yield_pct"], errors="coerce")
    df["payout_ratio"] = pd.to_numeric(df["payout_ratio"], errors="coerce")

    # ============ กรองตามเงื่อนไข 3 ข้อ ============
    mask = (
        df["consecutive_dividend_years"].notna() &
        (df["consecutive_dividend_years"] >= MIN_CONSECUTIVE_YEARS) &

        df["dividend_yield_pct"].notna() &
        (df["dividend_yield_pct"] >= MIN_DIVIDEND_YIELD) &

        df["payout_ratio"].notna() &
        (df["payout_ratio"] <= MAX_PAYOUT_RATIO)
    )

    filtered = df[mask].copy()

    # จัดเรียง: Yield สูง → สุขภาพดี (Payout ต่ำ)
    filtered = filtered.sort_values(
        by=["dividend_yield_pct", "payout_ratio"],
        ascending=[False, True]
    ).reset_index(drop=True)

    # ============ บันทึกผลลัพธ์ ============
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    filtered.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    filtered.to_json(OUTPUT_JSON, orient="records", force_ascii=False, indent=2)

    # ============ สรุปผล ============
    print(f"\n✅ Filtered Result: {len(filtered)} / {len(df)} stocks passed")
    print(f"\n📊 Breakdown:")
    print(f"   • Avg Yield:        {filtered['dividend_yield_pct'].mean():.2f}%")
    print(f"   • Avg Payout:       {filtered['payout_ratio'].mean():.1f}%")
    print(f"   • Avg Consecutive:  {filtered['consecutive_dividend_years'].mean():.1f} years")

    if not filtered.empty:
        print(f"\n🏆 Top 5 by Yield:")
        for _, row in filtered.head(5).iterrows():
            print(f"   • {row['symbol']:6s} | Yield: {row['dividend_yield_pct']:.2f}% | "
                  f"Payout: {row['payout_ratio']:.1f}% | {row['company_name_en'][:40]}")

    print(f"\n💾 Output files:")
    print(f"   CSV : {OUTPUT_CSV}")
    print(f"   JSON: {OUTPUT_JSON}")
    print("=" * 70)


if __name__ == "__main__":
    main()
