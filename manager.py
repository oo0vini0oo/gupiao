"""
股票分析项目 - 数据入库管理模块
将 fetcher 获取的数据写入 MySQL，自动更新 stock_daily 和 stock_list。
"""

from config import DATABASE, DAILY_COLUMNS


def save_daily_data(records: list, trade_date: str):
    """
    批量写入 stock_daily（幂等：同一天同一代码已存在则覆盖）。

    Parameters
    ----------
    records : list[dict]
        fetcher 返回的每一行 dict，键为 DB 字段名。
    trade_date : str
        交易日期，格式 'YYYY-MM-DD'。
    """
    print(f"[SAVE] 正在写入 stock_daily ({len(records)} 条)...")

    col_str = ", ".join([f"`{c}`" for c in DAILY_COLUMNS]) + ", `trade_date`, `update_time`"
    placeholders = ", ".join(["%s"] * len(DAILY_COLUMNS))
    all_placeholders = f"{placeholders}, %s, %s"

    # 除 trade_date 外的所有列在冲突时都更新
    update_cols = [c for c in DAILY_COLUMNS if c != "stock_code"]
    update_str = ", ".join([f"`{c}`=VALUES(`{c}`)" for c in update_cols]) + ", trade_date=VALUES(trade_date)"

    sql = (
        f"INSERT INTO stock_daily ({col_str}) "
        f"VALUES ({all_placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_str}"
    )

    now_str = trade_date + " " + __import__("datetime").datetime.now().strftime("%H:%M:%S")
    tuples = []
    for rec in records:
        row = tuple(rec.get(c) for c in DAILY_COLUMNS) + (trade_date, now_str)
        tuples.append(row)

    from db import _get_connection          # avoid circular import

    conn = _get_connection(with_db=True)
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, tuples)
        conn.commit()

        # ── 同时维护 stock_list ─────────────────────
        code_name_set = set()
        for r in records:
            sc = r.get("stock_code")
            sn = r.get("stock_name")
            if sc:
                code_name_set.add((sc, sn))
        _upsert_stock_list(code_name_set, conn)

        conn.commit()
        print(f"[SAVE] 成功写入 {len(tuples)} 条记录")
    finally:
        conn.close()


def _upsert_stock_list(items: set, conn):
    """批量写入 / 更新 stock_list"""
    sql = ("INSERT INTO stock_list (stock_code, stock_name) VALUES (%s, %s) "
           "ON DUPLICATE KEY UPDATE stock_name=VALUES(stock_name)")
    with conn.cursor() as cur:
        cur.executemany(sql, items)


def show_summary(today_count: int, days_ago: int = 0):
    """打印入库汇总"""
    from db import count_total_records, get_stock_count

    total = count_total_records()
    stocks = get_stock_count()
    print(f"\n[SUMMARY]")
    print(f"    今日写入   : {today_count} 条")
    print(f"    累计天数   : {days_ago} 个交易日")
    print(f"    涉及股票   : {stocks} 只")
    print(f"    表总行数   : {total:,} 条")
