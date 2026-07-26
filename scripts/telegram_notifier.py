#!/usr/bin/env python3
"""
Telegram Notifier for SET Stock Analysis
========================================
สร้างข้อความสรุปและส่งแจ้งเตือนผ่าน Telegram Bot
รันใน GitHub Actions หลังจาก fetch.py + technical_analysis.py เสร็จ
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ============ CONFIG ============
TELEGRAM_BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("CHAT_ID", "")
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

DATA_DIR = Path(__file__).parent.parent / "data"
TA_JSON = DATA_DIR / "technical_analysis.json"
HISTORY_JSON = DATA_DIR / "history.json"


def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """
    ส่งข้อความผ่าน Telegram Bot API
    - message: ข้อความที่จะส่ง (รองรับ HTML tag)
    - parse_mode: "HTML" หรือ "Markdown"
    - คืนค่า True/False ว่าส่งสำเร็จหรือไม่
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  TELEGRAM_BOT_TOKEN หรือ TELEGRAM_CHAT_ID ไม่ได้ตั้งค่า ข้ามการส่ง")
        return False
    
    url = TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
    
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True  # ไม่ต้อง preview ลิงก์
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        
        if result.get("ok"):
            print("✅ Telegram sent successfully")
            return True
        else:
            print(f"❌ Telegram API error: {result}")
            return False
            
    except Exception as e:
        print(f"❌ Failed to send Telegram: {e}")
        return False


def load_json(path: Path):
    """โหลดไฟล์ JSON ถ้ามี"""
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def truncate_text(text: str, max_len: int = 4000) -> str:
    """
    ตัดข้อความให้ไม่เกิน limit ของ Telegram (4096 chars)
    ถ้าเกินจะตัดท้ายและใส่ ...
    """
    if len(text) <= max_len:
        return text
    return text[:max_len - 20] + "\n... (truncated)"


def build_daily_report(ta_data: dict) -> str:
    """
    สร้างข้อความสรุปรายวัน (Daily Report)
    รวม: สถิติรวม, Top 10 Buy, Top 5 Bearish, Sector ยอดนิยม
    """
    stocks = ta_data.get("stocks", [])
    watchlist = ta_data.get("watchlist", [])
    sector = ta_data.get("sector_sentiment", [])
    
    if not stocks:
        return "📊 <b>SET Daily Report</b>\nไม่มีข้อมูลสำหรับวันนี้"
    
    # นับสถิติ
    total = len(stocks)
    strong_buy = len([s for s in stocks if s.get("signal") == "STRONG_BUY"])
    buy = len([s for s in stocks if s.get("signal") == "BUY"])
    sell = len([s for s in stocks if s.get("signal") in ("SELL", "STRONG_SELL")])
    hold = total - strong_buy - buy - sell
    
    avg_score = sum(s.get("score", 50) for s in stocks) / total if total else 50
    
    # หา Top 5 Buy (เรียงจาก watchlist ที่เรียงแล้ว)
    top_buys = watchlist[:5]
    
    # หา Top 5 Sector Bullish
    bull_sectors = [s for s in sector if "BULL" in s.get("sentiment", "")][:5]
    
    lines = [
        "📊 <b>SET Daily Report</b>",
        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        f"📈 <b>Market Overview</b>",
        f"   • Total Analyzed: <b>{total}</b> stocks",
        f"   • Avg Score: <b>{avg_score:.1f}</b>",
        f"   • 🟢 Strong Buy: {strong_buy} | 📈 Buy: {buy}",
        f"   • 🟡 Hold: {hold} | 🔴 Sell: {sell}",
        "",
        "⭐ <b>Top 5 Buy Signals</b>"
    ]
    
    if top_buys:
        for i, w in enumerate(top_buys, 1):
            emoji = "🌟" if w.get("score", 0) >= 75 else "📈"
            lines.append(
                f"{i}. {emoji} <b>{w['symbol']}</b> "
                f"(Score: {w['score']}, RSI: {w.get('rsi', 'N/A')})"
            )
    else:
        lines.append("   ไม่มีสัญญาณ Buy ในวันนี้")
    
    lines.extend(["", "🏭 <b>Top 5 Bullish Sectors</b>"])
    
    if bull_sectors:
        for i, s in enumerate(bull_sectors, 1):
            emoji = "🟢" if "STRONG" in s.get("sentiment", "") else "🟡"
            lines.append(
                f"{i}. {emoji} <b>{s['name']}</b> "
                f"(Avg Score: {s.get('avg_score', 0)}, Stocks: {s.get('total', 0)})"
            )
    else:
        lines.append("   ไม่มี Sector ที่ Bullish")
    
    lines.extend([
        "",
        "—",
        "<i>ข้อมูลจาก SET + Yahoo Finance | ไม่ใช่คำแนะนำการลงทุน</i>"
    ])
    
    return "\n".join(lines)


def build_change_alert(history_data: dict, ta_data: dict) -> str | None:
    """
    สร้างข้อความแจ้งเตือนการเปลี่ยนแปลง (Change Alert)
    คืนค่า None ถ้าไม่มีการเปลี่ยนแปลงใหม่ในรอบล่าสุด
    """
    changes = history_data.get("changes", [])
    if not changes:
        return None
    
    # เอาเฉพาะ changes ล่าสุด (10 รายการแรกจากท้ายสุด)
    recent = changes[-10:]
    
    lines = ["🔔 <b>Change Alert</b>", ""]
    has_alert = False
    
    for c in recent:
        sym = c.get("symbol", "?")
        field = c.get("field", "?")
        old_v = c.get("old_value", "-")
        new_v = c.get("new_value", "-")
        
        if field == "status":
            if new_v == "listed":
                lines.append(f"🆕 <b>{sym}</b> → เข้าจดทะเบียนใหม่ (IPO)")
                has_alert = True
            elif new_v == "delisted":
                lines.append(f"⛔ <b>{sym}</b> → หลุดจากการจดทะเบียน")
                has_alert = True
        else:
            lines.append(
                f"🔄 <b>{sym}</b> → {field}: "
                f"<code>{old_v}</code> → <code>{new_v}</code>"
            )
            has_alert = True
    
    if not has_alert:
        return None
    
    lines.extend(["", "<i>ตรวจพบจากการเปรียบเทียบข้อมูลเก่า-ใหม่</i>"])
    return "\n".join(lines)


def build_strong_buy_alert(ta_data: dict) -> str | None:
    """
    สร้างข้อความเฉพาะหุ้น Strong Buy (Score ≥ 75)
    ส่งเฉพาะถ้ามีตัวที่น่าสนใจจริงๆ
    """
    stocks = ta_data.get("stocks", [])
    strong_buys = [s for s in stocks if s.get("score", 0) >= 75]
    
    if not strong_buys:
        return None
    
    # เรียงตาม score สูงสุด
    strong_buys.sort(key=lambda x: x.get("score", 0), reverse=True)
    top = strong_buys[:10]  # ส่งไม่เกิน 10 ตัว
    
    lines = [
        "🌟 <b>Strong Buy Alert</b>",
        f"พบหุ้นที่ได้คะแนน ≥ 75 จำนวน {len(strong_buys)} ตัว",
        ""
    ]
    
    for s in top:
        lines.append(
            f"• <b>{s['symbol']}</b> | "
            f"Score: <code>{s['score']}</code> | "
            f"Price: {s.get('price', 'N/A')} | "
            f"RSI: {s.get('rsi', 'N/A')}"
        )
        # เหตุผลสั้นๆ
        reasons = []
        if "ABOVE" in s.get("price_vs_cloud", ""):
            reasons.append("เหนือ Cloud")
        if "BULLISH" in s.get("macd_signal", ""):
            reasons.append("MACD Bull")
        if s.get("rsi_condition") == "OVERSOLD":
            reasons.append("RSI Oversold")
        
        if reasons:
            lines.append(f"   └ {' + '.join(reasons)}")
    
    lines.append("")
    lines.append("<i>⚠️ ใช้เป็น screening tool เท่านั้น ไม่ใช่คำแนะนำการลงทุน</i>")
    
    return "\n".join(lines)


def main():
    """ฟังก์ชันหลัก: โหลดข้อมูล → สร้างข้อความ → ส่ง Telegram"""
    print("=" * 60)
    print("📨 Telegram Notifier")
    print("=" * 60)
    
    # โหลดข้อมูล
    ta_data = load_json(TA_JSON)
    history_data = load_json(HISTORY_JSON)
    
    if not ta_data:
        print("❌ ไม่พบ technical_analysis.json")
        sys.exit(1)
    
    # 1) ส่ง Daily Report (ทุกวัน)
    daily_msg = build_daily_report(ta_data)
    print("\n📊 Daily Report:")
    print(daily_msg[:500] + "..." if len(daily_msg) > 500 else daily_msg)
    send_telegram(truncate_text(daily_msg))
    
    # 2) ส่ง Change Alert (ถ้ามี)
    if history_data:
        change_msg = build_change_alert(history_data, ta_data)
        if change_msg:
            print("\n🔔 Change Alert:")
            print(change_msg[:500] + "..." if len(change_msg) > 500 else change_msg)
            send_telegram(truncate_text(change_msg))
        else:
            print("\n🔔 ไม่มี Change Alert")
    
    # 3) ส่ง Strong Buy Alert (ถ้ามี)
    strong_msg = build_strong_buy_alert(ta_data)
    if strong_msg:
        print("\n🌟 Strong Buy Alert:")
        print(strong_msg[:500] + "..." if len(strong_msg) > 500 else strong_msg)
        send_telegram(truncate_text(strong_msg))
    else:
        print("\n🌟 ไม่มี Strong Buy Alert")
    
    print("\n✅ Telegram notifications completed")


if __name__ == "__main__":
    main()
