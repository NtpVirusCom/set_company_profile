#!/usr/bin/env python3
"""
SET Dividend Screener
====================
ดึงประวัติเงินปันผลจาก Yahoo Finance คำนวณ Yield + Payout + Consistency
กรองหุ้นปันผลคุณภาพสูง บันทึกเป็น dividend_screening.json
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

# ============ CONFIG ============
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

COMPANIES_JSON = DATA_DIR / "companies.json"
DIVIDEND_JSON = DATA_DIR / "dividend_screening.json"

# เกณฑ์คัดกรอง (ปรับได้ตามความเหมาะสม)
#MIN_YIELD = 3.0          # ขั้นต่ำ 3%
#MAX_PAYOUT = 80.0        # ไม่เกิน 80%
#MIN_CONSECUTIVE_YEARS = 2  # จ่ายต่อเนื่องอย่างน้อย 2 ปี
#LOOKBACK_YEARS = 5       # ดูประวัติย้อนหลัง 5 ปี
#SLEEP = 0.15             # หน่วงระหว่างตัว
MIN_YIELD = 4.0          # ขั้นต่ำ 3%
MAX_PAYOUT = 60.0        # ไม่เกิน 80%
MIN_CONSECUTIVE_YEARS = 5  # จ่ายต่อเนื่องอย่างน้อย 2 ปี
LOOKBACK_YEARS = 5       # ดูประวัติย้อนหลัง 5 ปี
SLEEP = 0.15             # หน่วงระหว่างตัว


def fetch_dividend_data(symbol: str):
    """
    ดึงข้อมูลปันผลจาก Yahoo Finance สำหรับหุ้นไทย (.BK)
    คืนค่า dict หรือ None ถ้าไม่มีข้อมูล
    """
    yf_symbol = f"{symbol}.BK"
    
    try:
        ticker = yf.Ticker(yf_symbol)
        
        # 1) ดึงประวัติเงินปันผล (Series: index=date, value=dividend amount)
        div_history = ticker.dividends
        if div_history is None or div_history.empty:
            return None
        
        # 2) ดึงราคาล่าสุด
        hist = ticker.history(period="5d", interval="1d")
        if hist.empty:
            return None
        last_price = float(hist["Close"].iloc[-1])
        
        # 3) ดึงข้อมูลพื้นฐานจาก .info
        info = ticker.info or {}
        
        # 4) คำนวณเงินปันผล 12 เดือนล่าสุด
        cutoff = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=12)
        recent_divs = div_history[div_history.index >= cutoff]
        ttm_dividend = float(recent_divs.sum())  # TTM = Trailing Twelve Months
        
        # 5) คำนวณ Dividend Yield (%)
        div_yield = (ttm_dividend / last_price) * 100 if last_price > 0 else 0
        
        # 6) คำนวณ Payout Ratio (%)
        # ลองหาจาก info ก่อน ถ้าไม่มีให้ประมาณจาก EPS ล่าสุด
        payout_ratio = None
        if "payoutRatio" in info and info["payoutRatio"] is not None:
            payout_ratio = float(info["payoutRatio"]) * 100  # yfinance ให้เป็น decimal
        
        # ถ้าไม่มี payoutRatio ใน info ลองคำนวณจาก EPS trailing
        if payout_ratio is None:
            try:
                # ดึง earnings หรือ financials
                eps = info.get("trailingEps") or info.get("forwardEps")
                if eps and eps > 0:
                    payout_ratio = (ttm_dividend / eps) * 100
            except:
                pass
        
        # 7) นับจำนวนปีที่จ่ายต่อเนื่อง (มีเงินปันผลทุกปี)
        div_by_year = div_history.groupby(div_history.index.year).sum()
        consecutive = 0
        current_year = datetime.now().year
        
        for year in range(current_year, current_year - LOOKBACK_YEARS, -1):
            if year in div_by_year.index and div_by_year[year] > 0:
                consecutive += 1
            else:
                break  # ขาดปีไหนถือว่าหยุด
        
        # 8) หา Ex-Dividend Date ล่าสุด + เงินปันผลล่าสุด
        last_ex_date = str(div_history.index[-1].date()) if len(div_history) > 0 else None
        last_dividend = float(div_history.iloc[-1]) if len(div_history) > 0 else 0
        
        # 9) คำนวณ Dividend Growth (YoY)
        div_growth = None
        if len(div_by_year) >= 2:
            years = sorted(div_by_year.index)
            latest = div_by_year[years[-1]]
            previous = div_by_year[years[-2]]
            if previous > 0:
                div_growth = ((latest - previous) / previous) * 100
        
        return {
            "symbol": symbol,
            "last_price": round(last_price, 2),
            "ttm_dividend": round(ttm_dividend, 3),
            "dividend_yield": round(div_yield, 2),
            "payout_ratio": round(payout_ratio, 2) if payout_ratio else None,
            "consecutive_years": consecutive,
            "last_ex_date": last_ex_date,
            "last_dividend": round(last_dividend, 3),
            "dividend_growth_yoy": round(div_growth, 2) if div_growth else None,
            "total_div_history": len(div_history),
            "source": "Yahoo Finance"
        }
        
    except Exception as e:
        print(f"   ⚠️  {symbol}: {str(e)[:60]}")
        return None


def screen_dividend_stocks(companies: list) -> tuple:
    """
    วนลูปดึงข้อมูลปันผลทุกตัว แล้วกรองตามเกณฑ์
    คืนค่า (all_results, passed_screening)
    """
    all_results = []
    passed = []
    
    total = len(companies)
    for i, comp in enumerate(companies, 1):
        symbol = comp["symbol"]
        print(f"[{i:04d}/{total}] {symbol} (Dividend)...", end=" ", flush=True)
        
        data = fetch_dividend_data(symbol)
        
        if data:
            all_results.append(data)
            
            # === กรองตามเกณฑ์ ===
            checks = {
                "yield_ok": data["dividend_yield"] >= MIN_YIELD,
                "payout_ok": (data["payout_ratio"] is None) or (data["payout_ratio"] <= MAX_PAYOUT),
                "consecutive_ok": data["consecutive_years"] >= MIN_CONSECUTIVE_YEARS,
            }
            
            # ถ้าผ่านทุกข้อ ให้ใส่ passed
            if all(checks.values()):
                data["passed"] = True
                data["pass_reason"] = f"Yield {data['dividend_yield']}% | {data['consecutive_years']}Y consistent"
                passed.append(data)
                print(f"✅ PASS (Yield {data['dividend_yield']}%)")
            else:
                data["passed"] = False
                fail_reasons = []
                if not checks["yield_ok"]: fail_reasons.append(f"Yield {data['dividend_yield']}% < {MIN_YIELD}%")
                if not checks["payout_ok"]: fail_reasons.append(f"Payout {data['payout_ratio']}% > {MAX_PAYOUT}%")
                if not checks["consecutive_ok"]: fail_reasons.append(f"Only {data['consecutive_years']}Y")
                data["fail_reason"] = " | ".join(fail_reasons)
                print(f"❌ FAIL ({data['fail_reason']})")
        else:
            print("⚠️  No dividend data")
        
        time.sleep(SLEEP)
    
    return all_results, passed


def main():
    print("=" * 70)
    print("💰 SET Dividend Screener")
    print(f"🕐  Started at: {datetime.now()}")
    print(f"   Criteria: Yield ≥ {MIN_YIELD}%, Payout ≤ {MAX_PAYOUT}%, Consecutive ≥ {MIN_CONSECUTIVE_YEARS}Y")
    print("=" * 70)
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # โหลดรายชื่อหุ้นจาก companies.json
    if not COMPANIES_JSON.exists():
        print("❌ companies.json not found. Run fetch.py first.")
        sys.exit(1)
    
    with open(COMPANIES_JSON, "r", encoding="utf-8") as f:
        companies = json.load(f)
    
    print(f"📋 Loaded {len(companies)} companies")
    
    # วิเคราะห์ + กรอง
    all_results, passed = screen_dividend_stocks(companies)
    
    # เรียง passed ตาม Yield สูงสุด
    passed.sort(key=lambda x: x["dividend_yield"], reverse=True)
    
    # บันทึกไฟล์
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "criteria": {
            "min_yield": MIN_YIELD,
            "max_payout": MAX_PAYOUT,
            "min_consecutive_years": MIN_CONSECUTIVE_YEARS
        },
        "summary": {
            "total_analyzed": len(all_results),
            "total_passed": len(passed),
            "pass_rate": round(len(passed) / len(all_results) * 100, 2) if all_results else 0
        },
        "all_stocks": all_results,      # ทุกตัวที่มีข้อมูล (ไม่ว่าจะผ่านหรือไม่)
        "dividend_stocks": passed       # เฉพาะที่ผ่านเกณฑ์
    }
    
    with open(DIVIDEND_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 Saved: {DIVIDEND_JSON}")
    print(f"\n📊 Summary:")
    print(f"   • Analyzed: {len(all_results)} stocks")
    print(f"   • Passed screening: {len(passed)} stocks ({output['summary']['pass_rate']}%)")
    if passed:
        avg_yield = sum(p["dividend_yield"] for p in passed) / len(passed)
        print(f"   • Avg Yield (passed): {avg_yield:.2f}%")
    print("✅ Done!")


if __name__ == "__main__":
    main()
