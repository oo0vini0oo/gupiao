"""
股票分析项目 - 查询与分析模块
提供按股票代码查历史、市值排行等常用查询。
"""


def query_history_by_code(stock_code: str, limit: int = 200):
    """查询某只股票的历史日线数据（默认最近 200 条）"""
    from db import query_stock_history

    rows = query_stock_history(stock_code, limit)
    if not rows:
        print(f"未找到 {stock_code} 的数据")
        return

    print(f"\n[{stock_code}] 最近 {len(rows)} 个交易日行情")
    print(f"{'日期':<12} {'名称':<8} {'最新价':>8} {'涨跌幅%':>8} {'成交量(手)':>12} {'成交额':>12} {'最高':>8} {'最低':>8} {'市盈率':>8}")
    print("-" * 96)
    for r in rows:
        trade_date = str(r["trade_date"])[:10]
        name = (r["stock_name"] or "-")[:6]
        price = f"{float(r['latest_price']):.2f}" if r["latest_price"] else "-"
        pct   = f"{float(r['change_pct']):.2f}"      if r["change_pct"] is not None else "-"
        vol   = f"{int(r['volume']):,}"              if r["volume"] else "-"
        amt   = f"{float(r['turnover'])/1e4:,.1f}万" if r["turnover"] else "-"
        high  = f"{float(r['highest']):.2f}"          if r["highest"] else "-"
        low   = f"{float(r['lowest']):.2f}"            if r["lowest"] else "-"
        pe    = f"{float(r['pe_ratio_dynamic']):.2f}" if r["pe_ratio_dynamic"] else "-"
        print(f"  {trade_date:<10} {name:<8} {price:>8} {pct:>8} {vol:>12} {amt:>12} {high:>8} {low:>8} {pe:>8}")

    return rows


def query_top_market_cap(n: int = 10):
    """查询最新交易日市值最大的 N 只股票"""
    from db import query_latest_by_field

    rows = query_latest_by_field("total_market_cap", n)
    print(f"\n[市值 TOP{n}]（最新交易日）")
    print(f"{'日期':<12} {'代码':<10} {'名称':<10} {'总市值(万元)':>14}")
    print("-" * 50)
    for r in rows:
        date = str(r["trade_date"])[:10]
        cap_s = f"{float(r['total_market_cap'])/1e4:,.0f}" if r["total_market_cap"] else "-"
        print(f"  {date:<10} {r['stock_code']:<10} {r['stock_name'] or '-':<10} {cap_s:>14}")


def query_top_change_pct(n: int = 10):
    """查询最新交易日涨幅最大的 N 只股票"""
    from db import query_latest_by_field

    rows = query_latest_by_field("change_pct", n)
    print(f"\n[涨幅 TOP{n}]（最新交易日）")
    print(f"{'日期':<12} {'代码':<10} {'名称':<10} {'涨跌幅%':>8} {'最新价':>8}")
    print("-" * 55)
    for r in rows:
        date = str(r["trade_date"])[:10]
        pct_s  = f"{float(r['change_pct']):.2f}"      if r["change_pct"] is not None else "-"
        price_s = f"{float(r['latest_price']):.2f}" if r["latest_price"] else "-"
        print(f"  {date:<10} {r['stock_code']:<10} {r['stock_name'] or '-':<10} {pct_s:>8} {price_s:>8}")


def show_days_count():
    """打印数据覆盖的交易日数"""
    from config import DATABASE
    from db import _get_connection
    conn = _get_connection(with_db=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT trade_date) FROM stock_daily")
            days = cur.fetchone()[0]
            cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM stock_daily")
            min_d, max_d = cur.fetchone()
        print(f"\n[统计] 数据覆盖 {days} 个交易日，范围 {min_d} ~ {max_d}")
    finally:
        conn.close()
