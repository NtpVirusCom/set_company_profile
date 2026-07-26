#!/usr/bin/env python3
"""
SET Thailand Companies Fetcher (Playwright Edition)
แก้ปัญหา 403 โดยเปิด Browser จริงผ่าน Playwright แล้วดึงข้อมูล
หลังจาก JavaScript โหลดเสร็จ
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

# ============ CONFIGURATION ============
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
HISTORY_FILE = DATA_DIR / "history.json"
CSV_FILE = DATA_DIR / "companies.csv"
JSON_FILE = DATA_DIR / "companies.json"

# URL ที่มีตารางหุ้นทั้งหมดของ SET
SET_SEARCH_URL = "https://www.set.or.th/en/market/product/stock/quote/list-of-security"
# URL ดึง profile แต่ละตัว (อันนี้อาจยังโดน 403 อยู่ ถ้าใช้ requests ธรรมดา)
# ดังนั้นจะดึงข้อมูลพื้นฐานจากตารางหลักก่อน แล้วค่อยๆ เปิดแต่ละหน้า profile ผ่าน Playwright

def fetch_with_playwright():
    """
    ใช้ Playwright เปิดหน้า SET รอให้ตารางโหลด แล้วดึงข้อมูล
    """
    print(f"[{datetime.now()}] Launching Playwright browser...")
    
    companies = []
    
    with sync_playwright() as p:
        # เปิด Chromium แบบ headless (ไม่มีหน้าต่าง)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US"
        )
        page = context.new_page()
        
        try:
            # 1) ไปหน้าหลักที่มีตารางหุ้น
            print(f"Navigating to {SET_SEARCH_URL} ...")
            page.goto(SET_SEARCH_URL, wait_until="networkidle", timeout=60000)
            
            # รอให้ตารางปรากฏ (SET ใช้ <table> หรือ <div> ที่มี role=table)
            # ลองหาด้วยหลาย selector เพราะ SET อาจเปลี่ยนโครงสร้าง
            selectors = [
                "table tbody tr",
                "[role='table'] tbody tr",
                ".ag-center-cols-container .ag-row",  # ถ้าใช้ Ag-Grid
                ".p-datatable-tbody tr",               # ถ้าใช้ PrimeNG
            ]
            
            rows = []
            for sel in selectors:
                try:
                    page.wait_for_selector(sel, timeout=15000)
                    rows = page.query_selector_all(sel)
                    if len(rows) > 5:
                        print(f"✅ Found table rows using selector: {sel} ({len(rows)} rows)")
                        break
                except Exception:
                    continue
            
            if not rows:
                print("⚠️  Could not find table rows. Saving page HTML for debugging...")
                html = page.content()
                debug_file = DATA_DIR / "debug_page.html"
                debug_file.write_text(html, encoding="utf-8")
                print(f"   Debug HTML saved to: {debug_file}")
                return []
            
            # 2) ดึงข้อมูลจากแต่ละแถว
            for row in rows:
                try:
                    cells = row.query_selector_all("td, div.ag-cell")
                    if len(cells) < 3:
                        continue
                    
                    # ดึง text จากแต่ละ cell
                    texts = [c.inner_text().strip() for c in cells]
                    
                    # หา symbol (มักอยู่คอลัมน์แรก เป็นลิงก์หรือตัวหนังสือพิมพ์ใหญ่)
                    symbol = texts[0] if texts else ""
                    
                    # กรองเอาเฉพาะที่ดูเหมือน symbol หุ้น (ตัวพิมพ์ใหญ่ 2-6 ตัว)
                    if not symbol or not symbol.isupper() or len(symbol) > 6 or not symbol.isalpha():
                        continue
                    
                    # ชื่อบริษัทมักอยู่คอลัมน์ถัดไป
                    name_en = texts[1] if len(texts) > 1 else ""
                    
                    # ลองหา market/sector/industry ถ้ามีในตาราง
                    market = "SET"
                    industry = texts[2] if len(texts) > 2 else ""
                    sector = texts[3] if len(texts) > 3 else ""
                    
                    companies.append({
                        "symbol": symbol,
                        "company_name_th": "",  # ตารางภาษาอังกฤษอาจไม่มีชื่อไทย
                        "company_name_en": name_en,
                        "market": market,
                        "industry": industry,
                        "sector": sector,
                        "sub_sector": "",
                        "website": "",
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    })
                    
                except Exception as e:
                    continue
            
            # 3) (Optional) ถ้าต้องการดึง Industry/Sector ที่ละเอียดกว่า
            # ให้วนเปิดหน้า profile แต่ละตัว แต่จะช้ามาก (800+ ตัว)
            # แนะนำให้ดึงครั้งแรกจากตารางหลักก่อน แล้วค่อยเพิ่ม logic นี้ทีหลัง
            # สำหรับตอนนี้ ให้ใช้ข้อมูลเบื้องต้นจากตารางก่อน
            
            print(f"✅ Extracted {len(companies)} companies from table")
            
        except Exception as e:
            print(f"❌ Error during scraping: {e}")
        finally:
            browser.close()
    
    return companies

def fetch_profiles_with_playwright(companies):
    """
    (Optional) เปิดหน้า profile แต่ละบริษัทเพื่อดึง Industry/Sector ที่ถูกต้อง
    รันช้า ~2-3 นาที สำหรับ 100 ตัว แนะนำให้รันแค่บางตัวหรือทำเป็น batch
    """
    if not companies:
        return companies
    
    print(f"[{datetime.now()}] Fetching detailed profiles for {len(companies)} companies...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        
        for i, comp in enumerate(companies[:50], 1):  # จำกัด 50 ตัวแรกก่อน (test)
            symbol = comp["symbol"]
            url = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/company-profile/information"
            
            try:
                print(f"[{i:03d}/{len(companies)}] {symbol}...", end=" ")
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)  # รอ JS โหลด 2 วิ
                
                # หา Industry / Sector จากหน้า profile
                # SET มักแสดงในรูปแบบ label + value
                html = page.content()
                
                # ใช้ simple string search ก่อน (เร็วกว่า parse HTML ซ้ำ)
                if "Industry" in html:
                    # หา text หลัง "Industry" หรือ "Sector"
                    # วิธีนี้เป็น heuristic อาจต้องปรับตามโครงสร้างจริง
                    pass
                
                print("✅")
                
            except Exception as e:
                print(f"⚠️  {e}")
                continue
        
        browser.close()
    
    return companies

def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"snapshots": [], "changes": []}

def save_history(history):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def detect_changes(old_df, new_df):
    changes = []
    if old_df is None or old_df.empty:
        return changes
    
    compare_cols = ["industry", "sector", "sub_sector"]
    merged = new_df.merge(
        old_df[["symbol"] + compare_cols],
        on="symbol",
        how="outer",
        suffixes=("", "_old"),
        indicator=True
    )
    
    for _, row in merged.iterrows():
        sym = row["symbol"]
        if row["_merge"] == "left_only":
            changes.append({"symbol": sym, "field": "status", "old_value": "not_listed", "new_value": "listed", "detected_at": datetime.now(timezone.utc).isoformat()})
        elif row["_merge"] == "right_only":
            changes.append({"symbol": sym, "field": "status", "old_value": "listed", "new_value": "delisted", "detected_at": datetime.now(timezone.utc).isoformat()})
        else:
            for field in compare_cols:
                old_val = str(row.get(f"{field}_old", "")).strip()
                new_val = str(row.get(field, "")).strip()
                if old_val != new_val and (old_val or new_val):
                    changes.append({"symbol": sym, "field": field, "old_value": old_val, "new_value": new_val, "detected_at": datetime.now(timezone.utc).isoformat()})
    return changes

def main():
    print("=" * 70)
    print("🏛️  SET Thailand Companies Fetcher (Playwright)")
    print(f"🕐  Started at: {datetime.now()}")
    print("=" * 70)
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # ดึงข้อมูลจากตารางหลัก
    companies = fetch_with_playwright()
    
    if not companies:
        print("❌ No companies found. Check debug_page.html in data/ folder")
        sys.exit(1)
    
    # (ถ้าต้องการดึง profile ละเอียด ให้ uncomment บรรทัดด้านล่าง)
    # companies = fetch_profiles_with_playwright(companies)
    
    df = pd.DataFrame(companies)
    
    # โหลดข้อมูลเก่า
    old_df = None
    if CSV_FILE.exists():
        try:
            old_df = pd.read_csv(CSV_FILE, dtype=str).fillna("")
        except Exception as e:
            print(f"⚠️  Could not read old CSV: {e}")
    
    # ตรวจหาการเปลี่ยนแปลง
    changes = detect_changes(old_df, df)
    
    # บันทึกไฟล์
    df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
    df.to_json(JSON_FILE, orient="records", force_ascii=False, indent=2)
    
    # อัปเดต history
    history = load_history()
    history["snapshots"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_companies": len(df),
        "columns": df.columns.tolist()
    })
    if changes:
        history["changes"].extend(changes)
        print(f"🔔 Detected {len(changes)} change(s)")
    save_history(history)
    
    print(f"\n💾 Saved: {CSV_FILE} ({len(df)} rows)")
    print("✅ Done!")

if __name__ == "__main__":
    main()
