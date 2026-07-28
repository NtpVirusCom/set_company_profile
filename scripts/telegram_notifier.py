#!/usr/bin/env python3
"""
Telegram Notifier for SET Stock Analysis
========================================
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

DATA_DIR = Path(__file__).parent.parent / "data"
TA_JSON = DATA_DIR / "technical_analysis.json"
HISTORY_JSON = DATA_DIR / "history.json"
DIVIDEND_JSON = DATA_DIR / "dividend_screening.json"
CALENDAR_JSON = DATA_DIR / "dividend_calendar.json"


def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  TELEGRAM secrets not set")
        return False
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN),
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": parse_mode, "disable_web_page_preview": True},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"❌ Telegram failed: {e}")
        return False


def truncate_text(text: str, max_len: int = 4000) -> str:
    return text if len(text) <= max_len else text[:max_len - 20] + "\n... (truncated)"


def load_json(path: Path):
    return json.load(open(path, "r", encoding="utf-8")) if path.exists() else None


# ==================== BUILDERS ====================

def build_daily_report(ta_data: dict) -> str:
    stocks = ta_data.get("stocks", [])
    watchlist = ta_data.get("watchlist", [])
    sector = ta_data.get("sector_sentiment", [])
    if not stocks:
        return "📊 <b>SET Daily Report</b>\nไม่มีข้อมูล"
    
    total = len(stocks)
    strong_buy = len([s for s in stocks if s.get("signal") == "STRONG_BUY"])
    buy = len([s for s in stocks if s.get("signal") == "BUY"])
    avg_score = sum(s.get("score", 50) for s in stocks) / total if total else 50
    
    lines = [
        "📊 <b>SET Daily Report</b>",
        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        f"📈 Market: <b>{total}</b> stocks | Avg Score: <b>{avg_score:.1f}</b>",
        f"   🟢 Strong Buy: {strong_buy} | 📈 Buy: {buy}",
        "",
        "⭐ <b>Top 5 Buy Signals</b>"
    ]
    for i, w in enumerate(watchlist[:5], 1):
        emoji = "🌟" if w.get("score", 0) >= 75 else "📈"
        lines.append(f"{i}. {emoji} <b>{w['symbol']}</b> (Score: {w['score']})")
    
    lines.extend(["", "🏭 <b>Top 3 Bullish Sectors</b>"])
    for s in (sector or [])[:3]:
        lines.append(f"• <b>{s['name']}</b> (Avg: {s.get('avg_score', 0)})")
    
    lines.extend(["", "—", "<i>ข้อมูลจาก SET + Yahoo Finance | ไม่ใช่คำแนะนำการลงทุน</i>"])
    return "\n".join(lines)


def build_change_alert(history_data: dict) -> str | None:
    changes = history_data.get("changes", []) if history_data else []
    if not changes:
        return None
    recent = changes[-10:]
    lines = ["🔔 <b>Change Alert</b>", ""]
    has = False
    for c in recent:
        sym, field, old_v, new_v = c.get("symbol","?"), c.get("field","?"), c.get("old_value","-"), c.get("new_value","-")
        if field == "status":
            if new_v == "listed": lines.append(f"🆕 <b>{sym}</b> → IPO"); has = True
            elif new_v == "delisted": lines.append(f"⛔ <b>{sym}</b> → Delisted"); has = True
        else:
            lines.append(f"🔄 <b>{sym}</b> → {field}: <code>{old_v}</code> → <code>{new_v}</code>"); has = True
    if not has:
        return None
    lines.append(""); lines.append("<i>ตรวจพบจากการเปรียบเทียบข้อมูลเก่า-ใหม่</i>")
    return "\n".join(lines)


def build_strong_buy_alert(ta_data: dict) -> str | None:
    stocks = [s for s in (ta_data.get("stocks") or []) if s.get("score", 0) >= 75]
    if not stocks:
        return None
    stocks.sort(key=lambda x: x.get("score", 0), reverse=True)
    lines = [f"🌟 <b>Strong Buy Alert</b>", f"พบ {len(stocks)} ตัว", ""]
    for s in stocks[:10]:
        lines.append(f"• <b>{s['symbol']}</b> | Score: <code>{s['score']}</code> | Price: {s.get('price','N/A')} | RSI: {s.get('rsi','N/A')}")
    lines.extend(["", "<i>⚠️ ใช้เป็น screening tool เท่านั้น</i>"])
    return "\n".join(lines)


def build_dividend_alert(div_data: dict) -> str | None:
    if not div_data:
        return None
    passed = div_data.get("dividend_stocks", [])
    if not passed:
        return None
    
    lines = [
        "💰 <b>Dividend Opportunities</b>",
        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"📋 ผ่านเกณฑ์ {len(passed)} ตัว",
        ""
    ]
    for i, d in enumerate(passed[:10], 1):
        payout = f"{d['payout_ratio']}%" if d.get('payout_ratio') else "N/A"
        lines.append(f"{i}. <b>{d['symbol']}</b> | Yield: <code>{d['dividend_yield']}%</code> | Payout: {payout} | {d['consecutive_years']}Y")
    
    lines.extend(["", "<i>💡 หุ้นปันผลเหมาะกับการถือระยะยาว | DYOR</i>"])
    return "\n".join(lines)


def build_calendar_alert(cal_data: dict) -> str | None:
    if not cal_data:
        return None
    upcoming = cal_data.get("upcoming", [])
    if not upcoming:
        return None
    
    lines = [
        "📅 <b>Dividend Calendar Alert</b>",
        f"พบ {len(upcoming)} ตัว ที่ใกล้ถึงวันปันผล หรือเลยกำหนดประกาศ",
        ""
    ]
    for i, c in enumerate(upcoming[:15], 1):
        emoji = c.get("status_emoji", "⚪")
        days = c.get("days_until_next", 0)
        days_text = f"in {days} days" if days > 0 else f"overdue {-days} days"
        lines.append(
            f"{i}. {emoji} <b>{c['symbol']}</b> | "
            f"Expected: <code>{c['expected_next_date']}</code> ({days_text}) | "
            f"Yield: {c['dividend_yield']}% | "
            f"Last: {c['last_dividend']} Baht"
        )
        lines.append(f"   └ จ่ายประจำ: {c['typical_month_name']} ({c['frequency_label']})")
    
    lines.extend(["", "<i>⚠️ วันที่คาดการณ์จากประวัติย้อนหลัง อาจไม่ตรงกับประกาศจริงของบริษัท</i>"])
    return "\n".join(lines)


# ==================== 🆕 COMBO ALERT ====================

def build_combo_alert(ta_data: dict, div_data: dict) -> str | None:
    """
    หุ้นที่เป็น BOTH Strong Buy Technical + Dividend King
    เงื่อนไข:
      Technical: Score ≥ 75, เหนือ Cloud, Tenkan > Kijun, MACD Bullish, ไม่ Overbought
      Dividend: Yield ≥ 3%, Payout ≤ 80%, จ่ายต่อเนื่อง ≥ 2 ปี
    """
    if not ta_data or not div_data:
        return None
    
    # สร้าง lookup จาก dividend_stocks (ผ่านเกณฑ์ปันผลแล้ว)
    div_lookup = {d["symbol"]: d for d in div_data.get("dividend_stocks", [])}
    
    combo_stocks = []
    
    for stock in ta_data.get("stocks", []):
        sym = stock.get("symbol")
        score = stock.get("score", 0)
        
        # === Technical Checks ===
        if score < 75:
            continue
        if "ABOVE_CLOUD (Strong Bull)" not in stock.get("price_vs_cloud", ""):
            continue
        if stock.get("ichimoku_trend") != "BULLISH":
            continue
        if "BULLISH (MACD > Signal)" not in stock.get("macd_signal", "") and "BUY (Cross Up)" not in stock.get("macd_signal", ""):
            continue
        if stock.get("rsi_condition") == "OVERBOUGHT":
            continue
        
        # === Dividend Checks ===
        div = div_lookup.get(sym)
        if not div:
            continue
        
        # กรองอีกชั้นเพื่อความชัดเจน (ถึงจะอยู่ใน dividend_stocks แล้ว)
        if div.get("dividend_yield", 0) < 3.0:
            continue
        if div.get("payout_ratio") is not None and div["payout_ratio"] > 80:
            continue
        if div.get("consecutive_years", 0) < 2:
            continue
        
        combo_stocks.append({
            "symbol": sym,
            "score": score,
            "price": stock.get("price"),
            "rsi": stock.get("rsi"),
            "dividend_yield": div["dividend_yield"],
            "payout_ratio": div.get("payout_ratio"),
            "consecutive_years": div["consecutive_years"],
            "last_dividend": div.get("last_dividend"),
            "expected_next": div.get("expected_next_date", "N/A")
        })
    
    if not combo_stocks:
        #return None
        # แก้ตรงนี้: ส่งข้อความบอกว่าไม่มี แทนที่จะคืน None
        return (
            "👑 <b>COMBO ALERT: Strong Buy + Dividend</b>\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
            "📭 วันนี้ไม่มีหุ้นที่ผ่านเงื่อนไขทั้ง Technical + Dividend พร้อมกัน\n"
            "   (Score ≥ 75 + Yield ≥ 5% + Payout ≤ 80% + จ่ายต่อเนื่อง ≥ 10 ปี)\n\n"
            "<i>💡 ลองดูข้อความ 🌟 Strong Buy Alert หรือ 💰 Dividend Opportunities แยกก่อน</i>"
        )
    
    # เรียงตาม Score สูงสุด
    combo_stocks.sort(key=lambda x: x["score"], reverse=True)
    
    lines = [
        "👑 <b>COMBO ALERT: Strong Buy + Dividend King</b>",
        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
        f"📋 พบ {len(combo_stocks)} ตัว ที่ผ่านเงื่อนไขทั้ง Technical + Dividend",
        "",
        "<b>🎯 เงื่อนไขที่ใช้คัด</b>",
        "   Technical: Score ≥ 75 | เหนือ Cloud | Tenkan > Kijun | MACD Bullish | ไม่ Overbought",
        "   Dividend: Yield ≥ 5% | Payout ≤ 80% | จ่ายต่อเนื่อง ≥ 10 ปี",
        "",
        "<b>🏆 หุ้นที่ผ่านเกณฑ์</b>"
    ]
    
    for i, c in enumerate(combo_stocks[:15], 1):
        payout_str = f"{c['payout_ratio']}%" if c.get('payout_ratio') else "N/A"
        lines.append(
            f"{i}. 👑 <b>{c['symbol']}</b>\n"
            f"   ├ Score: <code>{c['score']}</code> | Price: {c['price']} | RSI: {c['rsi']}\n"
            f"   ├ Yield: <code>{c['dividend_yield']}%</code> | Payout: {payout_str} | {c['consecutive_years']}Y\n"
            f"   └ Next Div: {c['expected_next']} | Last: {c['last_dividend']} ฿"
        )
    
    lines.extend([
        "",
        "<i>⚠️ หุ้นนี้ผ่านทั้ง Technical (Momentum) และ Dividend (Income) | DYOR</i>"
    ])
    
    return "\n".join(lines)


# ==================== MAIN ====================

def main():
    print("=" * 60); print("📨 Telegram Notifier"); print("=" * 60)
    
    ta_data = load_json(TA_JSON)
    history_data = load_json(HISTORY_JSON)
    div_data = load_json(DIVIDEND_JSON)
    cal_data = load_json(CALENDAR_JSON)
    
    if not ta_data:
        print("❌ No TA data"); sys.exit(1)
    
    # 1) Daily
    send_telegram(truncate_text(build_daily_report(ta_data)))
    
    # 2) Changes
    if history_data:
        msg = build_change_alert(history_data)
        if msg: send_telegram(truncate_text(msg))
    
    # 3) Strong Buy
    msg = build_strong_buy_alert(ta_data)
    if msg: send_telegram(truncate_text(msg))
    
    # 4) Dividend
    if div_data:
        msg = build_dividend_alert(div_data)
        if msg: send_telegram(truncate_text(msg))
    
    # 5) Calendar
    if cal_data:
        msg = build_calendar_alert(cal_data)
        if msg: send_telegram(truncate_text(msg))
    
    # 6) 🆕 Combo Alert
    if div_data:
        msg = build_combo_alert(ta_data, div_data)
        if msg: send_telegram(truncate_text(msg))
    
    print("\n✅ All notifications sent")


if __name__ == "__main__":
    main()
