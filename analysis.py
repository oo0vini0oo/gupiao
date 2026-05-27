"""
股票分析模块 - 板块分类 / 龙头股识别 / 趋势预测
"""

import numpy as np
from datetime import datetime, timedelta
from config import SECTOR_MAP
from db import _get_connection


def classify_sector(stock_code: str) -> str:
    """根据股票代码前缀判断所属板块"""
    for sector_name, prefixes in SECTOR_MAP.items():
        for prefix in prefixes:
            if stock_code.startswith(prefix):
                return sector_name
    return "其他"


def get_sector_summary(trade_date: str = None) -> list:
    """获取各板块汇总数据（股票数、平均涨跌幅、总市值）"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        if trade_date is None:
            cur.execute("SELECT MAX(trade_date) FROM stock_daily")
            trade_date = str(cur.fetchone()[0])

        cur.execute(
            "SELECT stock_code, stock_name, latest_price, change_pct, total_market_cap, volume "
            "FROM stock_daily WHERE trade_date = %s",
            (trade_date,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    sectors = {}
    for code, name, price, pct, mcap, vol in rows:
        sector = classify_sector(code)
        if sector not in sectors:
            sectors[sector] = {"stocks": [], "count": 0}
        sectors[sector]["stocks"].append({
            "code": code, "name": name, "price": float(price) if price else 0,
            "change_pct": float(pct) if pct is not None else 0,
            "market_cap": float(mcap) if mcap else 0,
            "volume": int(vol) if vol else 0,
        })
        sectors[sector]["count"] += 1

    result = []
    for sector_name, data in sectors.items():
        stocks = data["stocks"]
        avg_change = np.mean([s["change_pct"] for s in stocks]) if stocks else 0
        total_mcap = sum(s["market_cap"] for s in stocks)
        up_count = sum(1 for s in stocks if s["change_pct"] > 0)
        down_count = sum(1 for s in stocks if s["change_pct"] < 0)
        # 龙头股（市值 TOP3）
        leaders = sorted(stocks, key=lambda x: x["market_cap"], reverse=True)[:3]
        # 涨幅 TOP10
        top_gainers = sorted(stocks, key=lambda x: x["change_pct"], reverse=True)[:10]

        result.append({
            "sector": sector_name,
            "count": data["count"],
            "avg_change_pct": round(avg_change, 2),
            "total_market_cap": round(total_mcap, 2),
            "up_count": up_count,
            "down_count": down_count,
            "leaders": [{"code": s["code"], "name": s["name"], "market_cap": round(s["market_cap"], 2)} for s in leaders],
            "top_gainers": [{"code": s["code"], "name": s["name"], "change_pct": s["change_pct"]} for s in top_gainers],
        })

    result.sort(key=lambda x: x["avg_change_pct"], reverse=True)
    return result


def get_stock_history(stock_code: str, limit: int = 120) -> list:
    """获取个股历史日线数据（用于图表展示）"""
    from db import query_stock_history
    rows = query_stock_history(stock_code, limit)
    # 按日期正序（ECharts 需要从左到右）
    rows.reverse()
    result = []
    for r in rows:
        result.append({
            "date": str(r["trade_date"])[:10],
            "price": float(r["latest_price"]) if r["latest_price"] else None,
            "open": float(r["open_price"]) if r["open_price"] else None,
            "high": float(r["highest"]) if r["highest"] else None,
            "low": float(r["lowest"]) if r["lowest"] else None,
            "change_pct": float(r["change_pct"]) if r["change_pct"] is not None else None,
            "volume": int(r["volume"]) if r["volume"] else 0,
            "turnover": float(r["turnover"]) if r["turnover"] else 0,
        })
    return result


def predict_trend(stock_code: str, days: int = 5) -> dict:
    """基于线性回归预测未来 N 天收盘价"""
    rows = get_stock_history(stock_code, 60)
    prices = [r["price"] for r in rows if r["price"] is not None]
    if len(prices) < 3:
        return {"error": "数据不足，无法预测", "predictions": []}

    x = np.arange(len(prices))
    y = np.array(prices)

    # 线性拟合
    coeffs = np.polyfit(x, y, 1)
    poly = np.poly1d(coeffs)

    last_date = datetime.strptime(rows[-1]["date"], "%Y-%m-%d") if rows else datetime.now()

    predictions = []
    current_date = last_date
    for i in range(1, days + 1):
        pred_idx = len(prices) + i - 1
        pred_date = current_date + timedelta(days=1)
        # 跳过周末
        while pred_date.weekday() >= 5:
            pred_date += timedelta(days=1)
        pred_price = round(float(poly(pred_idx)), 3)
        predictions.append({
            "date": pred_date.strftime("%Y-%m-%d"),
            "predicted_price": pred_price,
        })
        current_date = pred_date

    # 计算趋势方向
    last_price = prices[-1]
    trend = "up" if predictions[-1]["predicted_price"] > last_price else "down"
    change_pct = round((predictions[-1]["predicted_price"] - last_price) / last_price * 100, 2) if last_price else 0

    return {
        "last_price": last_price,
        "trend": trend,
        "change_pct": change_pct,
        "predictions": predictions,
        "history": [{"date": rows[i]["date"], "price": prices[i]} for i in range(len(prices))],
    }


def get_top_stocks(field: str = "total_market_cap", n: int = 10, asc: bool = False) -> list:
    """获取最新交易日排名前/后 N 的股票"""
    from db import query_latest_by_field
    rows = query_latest_by_field(field, n, asc)
    result = []
    for r in rows:
        result.append({
            "code": r["stock_code"],
            "name": r["stock_name"],
            "value": float(r[field]) if r[field] else 0,
            "price": float(r.get("latest_price", 0)) if r.get("latest_price") else 0,
        })
    return result


def get_market_overview() -> dict:
    """获取市场总览数据"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest = str(cur.fetchone()[0])

        cur.execute(
            "SELECT COUNT(*), AVG(change_pct), SUM(volume), SUM(turnover) "
            "FROM stock_daily WHERE trade_date = %s",
            (latest,),
        )
        row = cur.fetchone()
        total_stocks = row[0] or 0
        avg_change = float(row[1]) if row[1] else 0
        total_volume = float(row[2]) if row[2] else 0
        total_turnover = float(row[3]) if row[3] else 0

        cur.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE trade_date = %s AND change_pct > 0",
            (latest,),
        )
        up_count = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE trade_date = %s AND change_pct < 0",
            (latest,),
        )
        down_count = cur.fetchone()[0] or 0

        cur.execute("SELECT MIN(trade_date) FROM stock_daily")
        first_date = str(cur.fetchone()[0]) if row else ""

        cur.execute("SELECT COUNT(DISTINCT trade_date) FROM stock_daily")
        trading_days = cur.fetchone()[0] or 0

        return {
            "trade_date": latest,
            "total_stocks": total_stocks,
            "up_count": up_count,
            "down_count": down_count,
            "avg_change_pct": round(avg_change, 2),
            "total_volume": round(total_volume, 2),
            "total_turnover": round(total_turnover, 2),
            "first_date": first_date,
            "trading_days": trading_days,
        }
    finally:
        conn.close()
