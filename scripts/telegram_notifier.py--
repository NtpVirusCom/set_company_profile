#!/usr/bin/env python3
"""
Telegram Notifier for SET Stock Analysis
========================================
สร้างข้อความสรุปและส่งแจ้งเตือนผ่าน Telegram Bot
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ============ CONFIG ============
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

DATA_DIR = Path(__file__).parent.parent / "data"
TA_JSON = DATA_DIR / "technical_analysis.json"
HISTORY_JSON = DATA_DIR / "history.json"
DIVIDEND_JSON = DATA_DIR / "dividend_screening.json"


def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """ส่งข้อความผ่าน Telegram Bot API"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  TELEGRAM_BOT_TOKEN หรือ TELEGRAM_CHAT_ID ไม่ได้ตั้งค่า ข้ามการส่ง")
        return False
    
    url = TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
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


def truncate_text(text: str, max_len: int = 4000) -> str:
    """ตัดข้อความให้ไม่เกิน Telegram limit"""
    if len(text) <= max_len:
        return text
    return text[:max_len - 20] + "\n... (truncated)"


def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ==================== BUILDERS ====================

def build_daily_report(ta_data: dict) -> str:
    """สร้างข้อความสรุปรายวัน"""
    stocks = ta_data.get("stocks", [])
    watchlist = ta_data.get("watchlist", [])
    sector = ta_data.get("sector_sentiment", [])
    
    if not stocks:
        return "📊 <b>SET Daily Report</b>\nไม่มีข้อมูลสำหรับวันนี้"
    
    total = len(stocks)
    strong_buy = len([s for s in stocks if s.get("signal") == "STRONG_BUY"])
    buy = len([s for s in stocks if s.get("signal") == "BUY"])
    sell = len([s for s in stocks if s.get("signal") in ("SELL", "STRONG_SELL")])
    hold = total - strong_buy - buy - sell
    avg_score = sum(s.get("score", 50) for s in stocks) / total if total else 50
    
    top_buys = watchlist[:5]
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
            lines.append(f"{i}. {emoji} <b>{w['symbol']}</b> (Score: {w['score']}, RSI: {w.get('rsi', 'N/A')})")
    else:
        lines.append("   ไม่มีสัญญาณ Buy ในวันนี้")
    
    lines.extend(["", "🏭 <b>Top 5 Bullish Sectors</b>"])
    if bull_sectors:
        for i, s in enumerate(bull_sectors, 1):
            emoji = "🟢" if "STRONG" in s.get("sentiment", "") else "🟡"
            lines.append(f"{i}. {emoji} <b>{s['name']}</b> (Avg: {s.get('avg_score', 0)}, Stocks: {s.get('total', 0)})")
    else:
        lines.append("   ไม่มี Sector ที่ Bullish")
    
    lines.extend(["", "—", "<i>ข้อมูลจาก SET + Yahoo Finance | ไม่ใช่คำแนะนำการลงทุน</i>"])
    return "\n".join(lines)


def build_change_alert(history_data: dict) -> str | None:
    """สร้างข้อความแจ้งเตือนการเปลี่ยนแปลง"""
    changes = history_data.get("changes", [])
    if not changes:
        return None
    
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
            lines.append(f"🔄 <b>{sym}</b> → {field}: <code>{old_v}</code> → <code>{new_v}</code>")
            has_alert = True
    
    if not has_alert:
        return None
    lines.extend(["", "<i>ตรวจพบจากการเปรียบเทียบข้อมูลเก่า-ใหม่</i>"])
    return "\n".join(lines)


def build_strong_buy_alert(ta_data: dict) -> str | None:
    """สร้างข้อความ Strong Buy"""
    stocks = ta_data.get("stocks", [])
    strong_buys = [s for s in stocks if s.get("score", 0) >= 75]
    if not strong_buys:
        return None
    
    strong_buys.sort(key=lambda x: x.get("score", 0), reverse=True)
    top = strong_buys[:10]
    
    lines = [f"🌟 <b>Strong Buy Alert</b>", f"พบหุ้นที่ได้คะแนน ≥ 75 จำนวน {len(strong_buys)} ตัว", ""]
    for s in top:
        lines.append(f"• <b>{s['symbol']}</b> | Score: <code>{s['score']}</code> | Price: {s.get('price', 'N/A')} | RSI: {s.get('rsi', 'N/A')}")
        reasons = []
        if "ABOVE" in s.get("price_vs_cloud", ""): reasons.append("เหนือ Cloud")
        if "BULLISH" in s.get("macd_signal", ""): reasons.append("MACD Bull")
        if s.get("rsi_condition") == "OVERSOLD": reasons.append("RSI Oversold")
        if reasons: lines.append(f"   └ {' + '.join(reasons)}")
    
    lines.extend(["", "<i>⚠️ ใช้เป็น screening tool เท่านั้น</i>"])
    return "\n".join(lines)


def build_dividend_alert(div_data: dict) -> str | None:
    """
    🆕 สร้างข้อความหุ้นปันผล (Dividend Opportunities)
    ส่งเฉพาะเมื่อมีหุ้นที่ผ่านเกณฑ์
    """
    if not div_data:
        return None
    
    passed = div_data.get("dividend_stocks", [])
    if not passed:
        return None
    
    criteria = div_data.get("criteria", {})
    summary = div_data.get("summary", {})
    
    lines = [
        "💰 <b>Dividend Opportunities</b>",
        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"📋 ผ่านเกณฑ์ {len(passed)}/{summary.get('total_analyzed', 0)} ตัว ({summary.get('pass_rate', 0)}%)",
        f"   (Yield ≥ {criteria.get('min_yield', '?')}%, Payout ≤ {criteria.get('max_payout', '?')}%)",
        ""
    ]
    
    # แสดง Top 10
    for i, d in enumerate(passed[:10], 1):
        payout_str = f"{d['payout_ratio']}%" if d.get('payout_ratio') else "N/A"
        growth_str = f"+{d['dividend_growth_yoy']}%" if d.get('dividend_growth_yoy') and d['dividend_growth_yoy'] > 0 else "N/A"
        
        lines.append(
            f"{i}. <b>{d['symbol']}</b> | "
            f"Yield: <code>{d['dividend_yield']}%</code> | "
            f"Price: {d['last_price']} | "
            f"Payout: {payout_str} | "
            f"🗓 {d['consecutive_years']}Y"
        )
        if growth_str != "N/A":
            lines[-1] += f" | Growth: {growth_str}"
    
    # สรุป Sector ที่มีหุ้นปันผลเยอะ
    sector_counts = {}
    for p in passed:
        # ดึง sector จาก all_stocks หรือข้ามถ้าไม่มี
        pass
    
    lines.extend([
        "",
        "🎯 <b>เกณฑ์การคัดกรอง</b>",
        f"   • Yield ≥ {criteria.get('min_yield', '?')}% (สูงกว่าดอกเบี้ย)",
        f"   • Payout ≤ {criteria.get('max_payout', '?')}% (ยั่งยืน)",
        f"   • จ่ายต่อเนื่อง ≥ {criteria.get('min_consecutive_years', '?')} ปี",
        "",
        "<i>💡 หุ้นปันผลเหมาะกับการถือระยะยาว | DYOR</i>"
    ])
    
    return "\n".join(lines)


# ==================== MAIN ====================

def main():
    print("=" * 60)
    print("📨 Telegram Notifier")
    print("=" * 60)
    
    ta_data = load_json(TA_JSON)
    history_data = load_json(HISTORY_JSON)
    div_data = load_json(DIVIDEND_JSON)
    
    if not ta_data:
        print("❌ ไม่พบ technical_analysis.json")
        sys.exit(1)
    
    # 1) Daily Report (ทุกวัน)
    daily_msg = build_daily_report(ta_data)
    print("\n📊 Daily Report")
    send_telegram(truncate_text(daily_msg))
    
    # 2) Change Alert
    if history_data:
        change_msg = build_change_alert(history_data)
        if change_msg:
            print("\n🔔 Change Alert")
            send_telegram(truncate_text(change_msg))
    
    # 3) Strong Buy Alert
    strong_msg = build_strong_buy_alert(ta_data)
    if strong_msg:
        print("\n🌟 Strong Buy Alert")
        send_telegram(truncate_text(strong_msg))
    
    # 4) 🆕 Dividend Alert
    if div_data:
        div_msg = build_dividend_alert(div_data)
        if div_msg:
            print("\n💰 Dividend Alert")
            send_telegram(truncate_text(div_msg))
    
    print("\n✅ All notifications sent")


if __name__ == "__main__":
    main()
