#!/usr/bin/env python3
"""
SET Thailand Companies Fetcher
ดึงข้อมูลบริษัทจดทะเบียนในตลาดหลักทรัพย์ไทย (SET)
รวมถึง Industry และ Sector จาก SET Public API (ฟรี ไม่ต้องใช้ API Key)
"""

import requests
import pandas as pd
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ============ CONFIGURATION ============
BASE_DIR = Path(__file__).parent.parent      # ย้อนกลับไปโฟลเดอร์หลักของโปรเจกต์
DATA_DIR = BASE_DIR / "data"                  # โฟลเดอร์เก็บไฟล์ข้อมูล
HISTORY_FILE = DATA_DIR / "history.json"      # ไฟล์เก็บประวัติ
CSV_FILE = DATA_DIR / "companies.csv"         # ไฟล์ CSV หลัก
JSON_FILE = DATA_DIR / "companies.json"       # ไฟล์ JSON หลัก

# SET Public API Endpoints — ไม่ต้องใช้ API Key
SET_LIST_API = "https://www.set.or.th/api/set/stock/list?lang=en&market=SET"
SET_PROFILE_API = "https://www.set.or.th/api/set/stock/quote/{symbol}/company-profile/information?lang=en"

# Headers ปลอมตัวเป็น Browser ป้องกันโดนบล็อก
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.set.or.th/en/market/product/stock/quote/list-of-security",
    "Accept-Language": "en-US,en;q=0.9"
}

def fetch_stock_list():
    """
    ดึงรายชื่อหุ้นทั้งหมดในตลาด SET
    คืนค่าเป็น list ของ dict
    """
    print(f"[{datetime.now()}] Fetching stock list from SET Public API...")
    try:
        resp = requests.get(SET_LIST_API, headers=HEADERS, timeout=30)
        resp.raise_for_status()                    # ถ้า HTTP error ให้โยน exception
        data = resp.json()
        
        # API อาจห่อข้อมูลไว้ใน key "data" หรือ "securities"
        if isinstance(data, dict):
            if "data" in data:
                return data["data"]
            if "securities" in data:
                return data["securities"]
            if "stockList" in data:
                return data["stockList"]
        return data if isinstance(data, list) else []
        
    except Exception as e:
        print(f"ERROR fetching stock list: {e}")
        return []

def fetch_company_profile(symbol):
    """
    ดึงข้อมูลบริษัทเฉพาะตัว: industry, sector, website ฯลฯ
    มี retry 1 ครั้งถ้าล้มเหลว
    """
    url = SET_PROFILE_API.format(symbol=symbol)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        # รอ 0.5 วินาทีแล้วลองอีกครั้ง
        time.sleep(0.5)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e2:
            print(f"  ⚠️  Failed to fetch profile for {symbol}: {e2}")
            return {}

def extract_profile_fields(profile, stock):
    """
    แยกข้อมูล Industry/Sector จาก JSON response
    รองรับหลายรูปแบบเพราะ SET อาจเปลี่ยนโครงสร้าง
    """
    # ลองหาจากหลายๆ key ที่เป็นไปได้
    industry = (
        profile.get("industry") or 
        profile.get("industryName") or 
        profile.get("industryNameEn") or
        stock.get("industry") or
        ""
    )
    
    sector = (
        profile.get("sector") or 
        profile.get("sectorName") or 
        profile.get("sectorNameEn") or 
        profile.get("gicsSector") or
        stock.get("sector") or
        ""
    )
    
    sub_sector = (
        profile.get("subSector") or 
        profile.get("subIndustry") or 
        profile.get("gicsSubIndustry") or
        ""
    )
    
    website = profile.get("website") or profile.get("url") or ""
    
    return industry, sector, sub_sector, website

def build_companies_data(stock_list):
    """
    วนลูปดึงข้อมูลแต่ละบริษัท แล้วสร้าง DataFrame
    """
    records = []
    total = len(stock_list)
    
    for i, stock in enumerate(stock_list, 1):
        # หา symbol จากหลายชื่อ key ที่เป็นไปได้
        symbol = (
            stock.get("symbol") or 
            stock.get("securitySymbol") or 
            stock.get("name") or 
            stock.get("securityName") or
            ""
        ).strip()
        
        if not symbol:
            continue
        
        print(f"[{i:04d}/{total}] Processing {symbol}...", end=" ")
        
        # ดึง profile
        profile = fetch_company_profile(symbol)
        industry, sector, sub_sector, website = extract_profile_fields(profile, stock)
        
        # สร้าง record
        record = {
            "symbol": symbol,
            "company_name_th": stock.get("companyNameTh") or stock.get("nameTh") or "",
            "company_name_en": stock.get("companyNameEn") or stock.get("nameEn") or stock.get("securityName") or "",
            "market": stock.get("market") or stock.get("exchange") or "SET",
            "industry": industry,
            "sector": sector,
            "sub_sector": sub_sector,
            "website": website,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        records.append(record)
        
        status = "✅" if (industry or sector) else "⚠️ (no sector data)"
        print(status)
        
        # รอเล็กน้อยเพื่อไม่ให้ยิง API เร็วเกินไป
        time.sleep(0.3)
    
    return pd.DataFrame(records)

def load_history():
    """โหลดไฟล์ history.json ถ้ามีอยู่แล้ว"""
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"snapshots": [], "changes": []}

def save_history(history):
    """บันทึก history.json พร้อมจัดรูปแบบให้อ่านง่าย"""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def detect_changes(old_df, new_df):
    """
    เปรียบเทียบข้อมูลเก่ากับใหม่ หา:
    1. หุ้นที่เปลี่ยน Industry/Sector/Sub-Sector
    2. หุ้นใหม่ที่เข้ามา
    3. หุ้นที่หลุดออกไป
    """
    changes = []
    
    if old_df is None or old_df.empty:
        return changes
    
    # --- ตรวจหาการเปลี่ยนแปลง field ---
    compare_cols = ["industry", "sector", "sub_sector"]
    
    # Merge ตารางเก่า+ใหม่ โดยใช้ symbol เป็น key
    merged = new_df.merge(
        old_df[["symbol"] + compare_cols],
        on="symbol",
        how="outer",
        suffixes=("", "_old"),
        indicator=True
    )
    
    for _, row in merged.iterrows():
        sym = row["symbol"]
        
        # กรณีหุ้นใหม่เข้ามา
        if row["_merge"] == "left_only":
            changes.append({
                "symbol": sym,
                "field": "status",
                "old_value": "not_listed",
                "new_value": "listed",
                "detected_at": datetime.now(timezone.utc).isoformat()
            })
            continue
            
        # กรณีหุ้นหลุดออกไป
        if row["_merge"] == "right_only":
            changes.append({
                "symbol": sym,
                "field": "status",
                "old_value": "listed",
                "new_value": "delisted",
                "detected_at": datetime.now(timezone.utc).isoformat()
            })
            continue
        
        # กรณีเปลี่ยน field
        for field in compare_cols:
            old_val = str(row.get(f"{field}_old", "")).strip()
            new_val = str(row.get(field, "")).strip()
            if old_val != new_val and (old_val or new_val):
                changes.append({
                    "symbol": sym,
                    "field": field,
                    "old_value": old_val,
                    "new_value": new_val,
                    "detected_at": datetime.now(timezone.utc).isoformat()
                })
    
    return changes

def main():
    """ฟังก์ชันหลัก ควบคุม flow ทั้งหมด"""
    print("=" * 70)
    print("🏛️  SET Thailand Companies Fetcher")
    print(f"🕐  Started at: {datetime.now()}")
    print("=" * 70)
    
    # สร้างโฟลเดอร์ data ถ้ายังไม่มี
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1️⃣ ดึงรายการหุ้นทั้งหมด
    stock_list = fetch_stock_list()
    if not stock_list:
        print("❌ Failed to fetch stock list. Exiting.")
        sys.exit(1)
    
    print(f"📋 Found {len(stock_list)} securities in SET\n")
    
    # 2️⃣ ดึงรายละเอียดแต่ละบริษัท → สร้าง DataFrame
    df = build_companies_data(stock_list)
    
    # 3️⃣ โหลดข้อมูลเก่า (ถ้ามี) เพื่อเปรียบเทียบ
    old_df = None
    if CSV_FILE.exists():
        try:
            old_df = pd.read_csv(CSV_FILE, dtype=str).fillna("")
        except Exception as e:
            print(f"⚠️  Could not read old CSV: {e}")
    
    # 4️⃣ ตรวจหาการเปลี่ยนแปลง
    changes = detect_changes(old_df, df)
    
    # 5️⃣ บันทึก CSV (utf-8-sig รองรับภาษาไทยใน Excel)
    df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
    print(f"\n💾 Saved CSV: {CSV_FILE} ({len(df)} rows)")
    
    # 6️⃣ บันทึก JSON (อ่านง่าย ใช้กับ Google Apps Script)
    df.to_json(JSON_FILE, orient="records", force_ascii=False, indent=2)
    print(f"💾 Saved JSON: {JSON_FILE}")
    
    # 7️⃣ อัปเดต history.json
    history = load_history()
    
    # บันทึก snapshot รอบนี้
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_companies": len(df),
        "columns": df.columns.tolist()
    }
    history["snapshots"].append(snapshot)
    
    # บันทึก changes ถ้ามี
    if changes:
        history["changes"].extend(changes)
        print(f"🔔 Detected {len(changes)} change(s
