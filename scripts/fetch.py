#!/usr/bin/env python3
"""
SET Thailand Companies Fetcher
==============================
ดึงข้อมูลบริษัทจดทะเบียนในตลาดหลักทรัพย์ไทย (SET + mai)
จากไฟล์ official .xls ของ SET โดยตรง

URL: https://www.set.or.th/dat/eod/listedcompany/static/listedCompanies_en_US.xls
ข้อมูล: Symbol, Company Name, Market, Industry, Sector, Address, Tel, Fax, Website
"""

import json
import os
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

# ============ CONFIGURATION ============
BASE_DIR = Path(__file__).parent.parent      # ย้อนกลับไปโฟลเดอร์หลักของโปรเจกต์
DATA_DIR = BASE_DIR / "data"                  # โฟลเดอร์เก็บไฟล์ข้อมูล
HISTORY_FILE = DATA_DIR / "history.json"      # ไฟล์เก็บประวัติการเปลี่ยนแปลง
CSV_FILE = DATA_DIR / "companies.csv"         # ไฟล์ CSV หลัก
JSON_FILE = DATA_DIR / "companies.json"       # ไฟล์ JSON หลัก

# URL ไฟล์ official จาก SET (จริงๆ เป็น HTML table แต่ตั้งชื่อว่า .xls)
SET_XLS_URL = "https://www.set.or.th/dat/eod/listedcompany/static/listedCompanies_en_US.xls"

# Headers ปลอมตัวเป็น Browser ป้องกันโดนบล็อก
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def download_xls():
    """
    ดาวน์โหลดไฟล์ .xls จาก SET
    คืนค่าเป็น string (HTML) ที่ decode ด้วย TIS-620 แล้ว
    """
    print(f"[{datetime.now()}] 📥 Downloading from SET official source...")
    print(f"   URL: {SET_XLS_URL}")
    
    try:
        resp = requests.get(SET_XLS_URL, headers=HEADERS, timeout=60)
        resp.raise_for_status()                    # ถ้า HTTP error (4xx/5xx) ให้โยน exception
        
        print(f"   ✅ Downloaded: {len(resp.content):,} bytes")
        print(f"   Content-Type: {resp.headers.get('Content-Type', 'unknown')}")
        
        # ไฟล์นี้เป็น HTML table แต่ encode ด้วย TIS-620 (ภาษาไทยเก่า)
        # ลอง decode ด้วย TIS-620 ก่อน ถ้าไม่ได้จะ fallback ไป cp874
        for encoding in ["tis-620", "cp874", "utf-8"]:
            try:
                html_text = resp.content.decode(encoding)
                print(f"   🔤 Decoded with: {encoding}")
                return html_text
            except UnicodeDecodeError:
                continue
        
        raise UnicodeDecodeError("Cannot decode response with any known encoding")
        
    except requests.RequestException as e:
        print(f"   ❌ Download failed: {e}")
        return None


def parse_html_table(html_text):
    """
    แปลง HTML text ให้เป็น pandas DataFrame
    ขั้นตอน:
      1. ใช้ pd.read_html() หา table ทั้งหมดใน HTML
      2. เลือก table แรก (มีแค่ 1 table ในไฟล์นี้)
      3. ข้ามแถว title และ header
      4. ตั้งชื่อคอลัมน์ใหม่
      5. Clean ข้อมูล
    """
    print(f"\n[{datetime.now()}] 📊 Parsing HTML table...")
    
    # read_html() จะคืน list ของ DataFrame (เพราะ HTML อาจมีหลาย table)
    dfs = pd.read_html(StringIO(html_text))
    print(f"   Found {len(dfs)} table(s)")
    
    if not dfs:
        raise ValueError("No tables found in HTML")
    
    df = dfs[0]  # เลือก table แรก (และ table เดียว)
    print(f"   Raw shape: {df.shape} (rows, cols)")
    
    # === จัดการ Header ===
    # แถวที่ 0 ของไฟล์ SET คือชื่อ title: "List of Listed Companies & Contact Information"
    # แถวที่ 1 คือชื่อคอลัมน์จริง: Symbol, Company, Market, Industry, Sector...
    # ดังนั้นข้อมูลจริงเริ่มต้นที่แถวที่ 2
    
    df_data = df.iloc[2:].reset_index(drop=True)  # ตัดแถว 0-1 ออก แล้ว reset index ใหม่
    
    # ตั้งชื่อคอลัมน์ตามลำดับที่ SET กำหนด
    df_data.columns = [
        "symbol",           # 0: ชื่อย่อหุ้น (เช่น SCB, PTT)
        "company_name_en",   # 1: ชื่อบริษัทภาษาอังกฤษ
        "market",            # 2: ตลาด (SET หรือ mai)
        "industry",          # 3: อุตสาหกรรมหลัก (เช่น Financials, Energy)
        "sector",            # 4: กลุ่มย่อย (เช่น Banking, Oil & Gas)
        "address",           # 5: ที่อยู่
        "zip_code",          # 6: รหัสไปรษณีย์
        "telephone",         # 7: โทรศัพท์
        "fax",               # 8: โทรสาร
        "website",           # 9: เว็บไซต์
    ]
    
    # === Clean ข้อมูล ===
    
    # 1) กรองเอาเฉพาะแถวที่ symbol มีค่า และเป็นตัวอักษร/ตัวเลข (ไม่มีช่องว่างพิเศษ)
    df_data = df_data[df_data["symbol"].notna()]
    df_data = df_data[df_data["symbol"].astype(str).str.match(r"^[A-Z0-9]+$", na=False)]
    
    # 2) กรองเอาเฉพาะ symbol ที่ความยาว 2-6 ตัวอักษร (มาตรฐานหุ้นไทย)
    df_data = df_data[df_data["symbol"].astype(str).str.len().between(2, 6)]
    
    # 3) แทนที่ค่า "-" หรือว่างเปล่า ใน sector/industry เป็น string ว่าง
    for col in ["industry", "sector"]:
        df_data[col] = df_data[col].astype(str).replace(["-", "nan", "None"], "")
    
    # 4) เพิ่มคอลัมน์ company_name_th (ว่างเปล่า) เพื่อ compatibility กับระบบเก่า
    #    ถ้าต้องการชื่อไทย ต้องใช้ไฟล์ listedCompanies_th_TH.xls แยกต่างหาก
    df_data.insert(1, "company_name_th", "")
    
    # 5) เพิ่ม timestamp ว่าอัปเดตเมื่อไหร่
    df_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    # 6) รีเซ็ต index ให้เรียง 0, 1, 2...
    df_data = df_data.reset_index(drop=True)
    
    print(f"   ✅ Cleaned shape: {df_data.shape}")
    print(f"   📈 Markets: {df_data['market'].value_counts().to_dict()}")
    
    return df_data


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
      1. หุ้นที่เปลี่ยน Industry/Sector
      2. หุ้นใหม่ที่เข้ามา (IPO)
      3. หุ้นที่หลุดออกไป (Delist)
    """
    changes = []
    
    if old_df is None or old_df.empty:
        return changes
    
    compare_cols = ["industry", "sector"]
    
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
        
        # กรณีหุ้นใหม่เข้ามา (IPO)
        if row["_merge"] == "left_only":
            changes.append({
                "symbol": sym,
                "field": "status",
                "old_value": "not_listed",
                "new_value": "listed",
                "detected_at": datetime.now(timezone.utc).isoformat()
            })
            continue
        
        # กรณีหุ้นหลุดออกไป (Delist)
        if row["_merge"] == "right_only":
            changes.append({
                "symbol": sym,
                "field": "status",
                "old_value": "listed",
                "new_value": "delisted",
                "detected_at": datetime.now(timezone.utc).isoformat()
            })
            continue
        
        # กรณีเปลี่ยน industry หรือ sector
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
    print("   Source: SET Official .xls (HTML table)")
    print(f"🕐  Started at: {datetime.now()}")
    print("=" * 70)
    
    # สร้างโฟลเดอร์ data ถ้ายังไม่มี
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1️⃣ ดาวน์โหลดไฟล์จาก SET
    html_text = download_xls()
    if html_text is None:
        print("❌ Failed to download. Exiting.")
        sys.exit(1)
    
    # 2️⃣ แปลง HTML → DataFrame
    try:
        df = parse_html_table(html_text)
    except Exception as e:
        print(f"❌ Failed to parse table: {e}")
        sys.exit(1)
    
    if df.empty:
        print("❌ No data after cleaning. Exiting.")
        sys.exit(1)
    
    print(f"\n📋 Total companies: {len(df)}")
    
    # 3️⃣ โหลดข้อมูลเก่า (ถ้ามี) เพื่อเปรียบเทียบ
    old_df = None
    if CSV_FILE.exists():
        try:
            old_df = pd.read_csv(CSV_FILE, dtype=str).fillna("")
            print(f"📂 Previous data: {len(old_df)} companies")
        except Exception as e:
            print(f"⚠️  Could not read old CSV: {e}")
    
    # 4️⃣ ตรวจหาการเปลี่ยนแปลง
    changes = detect_changes(old_df, df)
    
    # 5️⃣ บันทึก CSV (utf-8-sig รองรับภาษาไทยใน Excel)
    df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
    print(f"\n💾 Saved CSV: {CSV_FILE}")
    
    # 6️⃣ บันทึก JSON (อ่านง่าย ใช้กับ Google Apps Script)
    df.to_json(JSON_FILE, orient="records", force_ascii=False, indent=2)
    print(f"💾 Saved JSON: {JSON_FILE}")
    
    # 7️⃣ อัปเดต history.json
    history = load_history()
    
    # บันทึก snapshot รอบนี้
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_companies": len(df),
        "markets": df["market"].value_counts().to_dict(),
        "columns": df.columns.tolist()
    }
    history["snapshots"].append(snapshot)
    
    # บันทึก changes ถ้ามี
    if changes:
        history["changes"].extend(changes)
        print(f"\n🔔 Detected {len(changes)} change(s):")
        for c in changes[:10]:  # โชว์แค่ 10 รายการแรก
            arrow = "→"
            print(f"   • {c['symbol']}: {c['field']} | {c['old_value']} {arrow} {c['new_value']}")
        if len(changes) > 10:
            print(f"   ... and {len(changes)-10} more")
    
    save_history(history)
    print(f"💾 Updated history: {HISTORY_FILE}")
    
    # 8️⃣ สรุปผล
    print("\n" + "=" * 70)
    print("✅ Done! Summary:")
    print(f"   • Total companies: {len(df)}")
    print(f"   • SET: {(df['market'] == 'SET').sum()}")
    print(f"   • mai: {(df['market'] == 'mai').sum()}")
    print(f"   • Changes detected: {len(changes)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
