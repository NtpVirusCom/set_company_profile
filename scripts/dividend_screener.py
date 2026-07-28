#!/usr/bin/env python3
"""
SET Dividend Screener + Calendar
=================================
ดึงประวัติเงินปันผล → คำนวณ Yield + Payout + วิเคราะห์รูปแบบการจ่าย
คาดการณ์วันปันผลถัดไปจากประวัติ (Annual/Semi-annual pattern)
"""

import json
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

# ============ CONFIG ============
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

COMPANIES_JSON = DATA_DIR / "companies.json"
DIVIDEND_JSON = DATA_DIR / "dividend_screening.json"
CALENDAR_JSON = DATA_DIR / "dividend_calendar.json"

MIN_YIELD = 3.0
MAX_PAYOUT = 80.0
MIN_CONSECUTIVE_YEARS = 2
LOOKBACK_YEARS = 5
SLEEP = 0.15


def fetch_dividend_data(symbol: str):
    """ดึงข้อมูลปันผล + วิเคราะห์ Calendar จาก Yahoo Finance"""
    yf_symbol = f"{symbol}.BK"
    
    try:
        ticker = yf.Ticker(yf_symbol)
        div_history = ticker.dividends
        
        if div_history is None or div_history.empty:
            return None, None
        
        # ราคาล่าสุด
        hist = ticker.history(period="5d", interval="1d")
        if hist.empty:
            return None, None
        last_price = float(hist["Close"].iloc[-1])
        
        # ข้อมูลพื้นฐาน
        info = ticker.info or {}
        
        # === Screener Data ===
        cutoff = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=12)
        recent_divs = div_history[div_history.index >= cutoff]
        ttm_dividend = float(recent_divs.sum())
        div_yield = (ttm_dividend / last_price) * 100 if last_price > 0 else 0
        
        payout_ratio = None
        if "payoutRatio" in info and info["payoutRatio"] is not None:
            payout_ratio = float(info["payoutRatio"]) * 100
        
        if payout_ratio is None:
            try:
                eps = info.get("trailingEps") or info.get("forwardEps")
                if eps and eps > 0:
                    payout_ratio = (ttm_dividend / eps) * 100
            except:
                pass
        
        # จ่ายติดต่อกันกี่ปี
        div_by_year = div_history.groupby(div_history.index.year).sum()
        consecutive = 0
        current_year = datetime.now().year
        for year in range(current_year, current_year - LOOKBACK_YEARS, -1):
            if year in div_by_year.index and div_by_year[year] > 0:
                consecutive += 1
            else:
                break
        
        last_ex_date = div_history.index[-1]
        last_dividend = float(div_history.iloc[-1])
        
        # Growth YoY
        div_growth = None
        if len(div_by_year) >= 2:
            years = sorted(div_by_year.index)
            latest = div_by_year[years[-1]]
            previous = div_by_year[years[-2]]
            if previous > 0:
                div_growth = ((latest - previous) / previous) * 100
        
        screener = {
            "symbol": symbol,
            "last_price": round(last_price, 2),
            "ttm_dividend": round(ttm_dividend, 3),
            "dividend_yield": round(div_yield, 2),
            "payout_ratio": round(payout_ratio, 2) if payout_ratio else None,
            "consecutive_years": consecutive,
            "last_ex_date": str(last_ex_date.date()),
            "last_dividend": round(last_dividend, 3),
            "dividend_growth_yoy": round(div_growth, 2) if div_growth else None,
            "total_div_history": len(div_history),
            "source": "Yahoo Finance"
        }
        
        # === Calendar Analysis ===
        calendar = analyze_calendar(div_history, screener)
        
        return screener, calendar
        
    except Exception as e:
        print(f"   ⚠️  {symbol}: {str(e)[:60]}")
        return None, None


def analyze_calendar(div_history, screener_data):
    """
    วิเคราะห์รูปแบบการจ่ายปันผลเพื่อคาดการณ์วันถัดไป
    """
    if div_history is None or len(div_history) < 2:
        return None
    
    now = datetime.now()
    
    # 1) หาเดือนที่จ่ายบ่อยที่สุด
    months = [d.month for d in div_history.index]
    month_counts = Counter(months)
    most_common_month, freq = month_counts.most_common(1)[0]
    
    # 2) คำนวณช่วงห่างเฉลี่ยระหว่างการจ่าย (วัน)
    intervals = []
    for i in range(1, len(div_history)):
        delta = (div_history.index[i] - div_history.index[i-1]).days
        intervals.append(delta)
    avg_interval = sum(intervals) / len(intervals) if intervals else 365
    
    # 3) กำหนดความถี่
    if avg_interval < 200:
        frequency = "SEMI_ANNUAL"
        freq_label = "2x/Year"
    elif avg_interval < 400:
        frequency = "ANNUAL"
        freq_label = "1x/Year"
    else:
        frequency = "IRREGULAR"
        freq_label = "Irregular"
    
    # 4) คาดการณ์วันถัดไป
    last_date = pd.Timestamp(div_history.index[-1]).to_pydatetime()
    expected_next = last_date + timedelta(days=int(avg_interval))
    
    # ถ้า expected_next ผ่านมาแล้ว ให้ปรับไปรอบถัดไป
    while expected_next < now:
        expected_next += timedelta(days=int(avg_interval))
    
    days_since = (now - last_date).days
    days_until = (expected_next - now).days
    
    # 5) กำหนด Status
    if days_since > avg_interval * 1.4:
        status = "OVERDUE"  # น่าจะประกาศแล้วแต่ยังไม่มีข้อมูล หรือเลื่อน
        status_emoji = "🔴"
    elif days_until <= 45:
        status = "EXPECTED_SOON"
        status_emoji = "🟢"
    elif days_since < 60:
        status = "RECENTLY_PAID"
        status_emoji = "🟡"
    else:
        status = "REGULAR"
        status_emoji = "⚪"
    
    # 6) ชื่อเดือนไทย/อังกฤษ
    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    typical_month_name = month_names[most_common_month]
    
    return {
        "symbol": screener_data["symbol"],
        "last_ex_date": screener_data["last_ex_date"],
        "last_dividend": screener_data["last_dividend"],
        "dividend_yield": screener_data["dividend_yield"],
        "frequency": frequency,
        "frequency_label": freq_label,
        "typical_month": most_common_month,
        "typical_month_name": typical_month_name,
        "avg_interval_days": int(avg_interval),
        "expected_next_date": str(expected_next.date()),
        "days_since_last": days_since,
        "days_until_next": days_until,
        "status": status,
        "status_emoji": status_emoji
    }


def screen_dividend_stocks(companies: list) -> tuple:
    """วนลูปดึงข้อมูลปันผลทุกตัว"""
    all_results = []
    passed = []
    calendars = []
    
    total = len(companies)
    for i, comp in enumerate(companies, 1):
        symbol = comp["symbol"]
        print(f"[{i:04d}/{total}] {symbol} (Dividend)...", end=" ", flush=True)
        
        screener, calendar = fetch_dividend_data(symbol)
        
        if screener:
            all_results.append(screener)
            if calendar:
                calendars.append(calendar)
            
            checks = {
                "yield_ok": screener["dividend_yield"] >= MIN_YIELD,
                "payout_ok": (screener["payout_ratio"] is None) or (screener["payout_ratio"] <= MAX_PAYOUT),
                "consecutive_ok": screener["consecutive_years"] >= MIN_CONSECUTIVE_YEARS,
            }
            
            if all(checks.values()):
                screener["passed"] = True
                screener["pass_reason"] = f"Yield {screener['dividend_yield']}% | {screener['consecutive_years']}Y"
                passed.append(screener)
                print(f"✅ PASS (Yield {screener['dividend_yield']}%)")
            else:
                screener["passed"] = False
                fail_reasons = []
                if not checks["yield_ok"]: fail_reasons.append(f"Yield {screener['dividend_yield']}%")
                if not checks["payout_ok"]: fail_reasons.append(f"Payout {screener['payout_ratio']}%")
                if not checks["consecutive_ok"]: fail_reasons.append(f"{screener['consecutive_years']}Y")
                screener["fail_reason"] = " | ".join(fail_reasons)
                print(f"❌ FAIL")
        else:
            print("⚠️  No data")
        
        time.sleep(SLEEP)
    
    return all_results, passed, calendars


def main():
    print("=" * 70)
    print("💰 SET Dividend Screener + Calendar")
    print(f"🕐  Started at: {datetime.now()}")
    print(f"   Criteria: Yield ≥ {MIN_YIELD}%, Payout ≤ {MAX_PAYOUT}%, Consecutive ≥ {MIN_CONSECUTIVE_YEARS}Y")
    print("=" * 70)
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    if not COMPANIES_JSON.exists():
        print("❌ companies.json not found. Run fetch.py first.")
        sys.exit(1)
    
    with open(COMPANIES_JSON, "r", encoding="utf-8") as f:
        companies = json.load(f)
    
    print(f"📋 Loaded {len(companies)} companies\n")
    
    all_results, passed, calendars = screen_dividend_stocks(companies)
    
    # เรียง passed ตาม Yield สูงสุด
    passed.sort(key=lambda x: x["dividend_yield"], reverse=True)
    
    # เรียง Calendar ตาม days_until_next (ใกล้สุดก่อน)
    valid_calendars = [c for c in calendars if c and c.get("days_until_next") is not None]
    valid_calendars.sort(key=lambda x: x["days_until_next"])
    
    # แยกเฉพาะที่น่าสนใจ (Expected Soon หรือ Overdue)
    upcoming = [c for c in valid_calendars if c["status"] in ("EXPECTED_SOON", "OVERDUE")]
    
    # บันทึก Screener
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "criteria": {"min_yield": MIN_YIELD, "max_payout": MAX_PAYOUT, "min_consecutive_years": MIN_CONSECUTIVE_YEARS},
        "summary": {
            "total_analyzed": len(all_results),
            "total_passed": len(passed),
            "pass_rate": round(len(passed) / len(all_results) * 100, 2) if all_results else 0
        },
        "all_stocks": all_results,
        "dividend_stocks": passed
    }
    
    with open(DIVIDEND_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    # บันทึก Calendar
    calendar_output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_tracked": len(valid_calendars),
        "upcoming_count": len(upcoming),
        "upcoming": upcoming,           # เฉพาะที่ใกล้เข้า
        "calendar": valid_calendars     # ทั้งหมด
    }
    
    with open(CALENDAR_JSON, "w", encoding="utf-8") as f:
        json.dump(calendar_output, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 Saved: {DIVIDEND_JSON}")
    print(f"💾 Saved: {CALENDAR_JSON}")
    print(f"\n📊 Summary:")
    print(f"   • Analyzed: {len(all_results)} stocks")
    print(f"   • Passed screening: {len(passed)} ({output['summary']['pass_rate']}%)")
    print(f"   • Calendar tracked: {len(valid_calendars)}")
    print(f"   • Upcoming/Overdue: {len(upcoming)}")
    print("✅ Done!")


if __name__ == "__main__":
    main()
