"""
虚拟投资组合模块
模拟 5 万初始资金，买入/卖出/持仓/每日盈亏。
"""

import pymysql
from datetime import date, datetime
from config import MYSQL_CONFIG, DATABASE
from db import _get_connection


def _ensure_account():
    """确保账户存在，自动初始化 5 万资金"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, cash_balance FROM virtual_account LIMIT 1")
        row = cur.fetchone()
        if not row:
            cur.execute(
                "INSERT INTO virtual_account (cash_balance, init_balance, created_at, updated_at) "
                "VALUES (50000, 50000, NOW(), NOW())"
            )
            conn.commit()
            return 50000
        return float(row[1])
    finally:
        conn.close()


def get_portfolio():
    """获取当前持仓 + 账户概况"""
    _ensure_account()
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute("SELECT cash_balance, init_balance FROM virtual_account LIMIT 1")
        account = cur.fetchone()
        cash = float(account["cash_balance"])
        init_bal = float(account["init_balance"])

        cur.execute("SELECT * FROM virtual_holdings")
        holdings = cur.fetchall()

        # 获取最新行情
        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest_date = cur.fetchone()["MAX(trade_date)"]
        latest_date_str = str(latest_date) if latest_date else ""

        holdings_value = 0.0
        holding_list = []
        for h in holdings:
            code = h["stock_code"]
            qty = h["quantity"]
            buy_price = float(h["buy_price"])
            cur.execute(
                "SELECT latest_price, stock_name, change_pct FROM stock_daily "
                "WHERE stock_code = %s AND trade_date = %s",
                (code, latest_date_str),
            )
            row = cur.fetchone()
            if row:
                current_price = float(row["latest_price"])
                name = row["stock_name"] or h["stock_name"]
                change_pct = float(row["change_pct"]) if row["change_pct"] else 0
            else:
                current_price = buy_price
                name = h["stock_name"]
                change_pct = 0

            market_value = round(current_price * qty, 4)
            cost = round(buy_price * qty, 4)
            profit_pct = round((current_price - buy_price) / buy_price * 100, 2) if buy_price else 0
            holdings_value += market_value

            holding_list.append({
                "code": code,
                "name": name or "",
                "quantity": qty,
                "buy_price": buy_price,
                "current_price": current_price,
                "market_value": market_value,
                "cost": cost,
                "profit": round(market_value - cost, 2),
                "profit_pct": profit_pct,
                "change_pct": change_pct,
            })

        total_assets = round(cash + holdings_value, 2)
        total_pnl = round(total_assets - init_bal, 2)
        total_pnl_pct = round((total_assets - init_bal) / init_bal * 100, 2) if init_bal else 0

        return {
            "cash_balance": round(cash, 2),
            "init_balance": init_bal,
            "holdings_value": round(holdings_value, 2),
            "total_assets": total_assets,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "holdings_count": len(holding_list),
            "latest_trade_date": latest_date_str,
            "holdings": holding_list,
        }
    finally:
        conn.close()


def buy_stock(code: str, quantity: int) -> dict:
    """以最新价买入股票"""
    _ensure_account()
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()

        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest = cur.fetchone()[0]
        if not latest:
            return {"success": False, "error": "无行情数据"}

        latest_str = str(latest)
        cur.execute(
            "SELECT latest_price, stock_name FROM stock_daily "
            "WHERE stock_code = %s AND trade_date = %s",
            (code, latest_str),
        )
        row = cur.fetchone()
        if not row:
            return {"success": False, "error": f"未找到 {code} 的行情数据"}

        price = float(row[0])
        name = row[1] or ""
        amount = round(price * quantity, 4)

        cur.execute("SELECT cash_balance FROM virtual_account LIMIT 1")
        cash = float(cur.fetchone()[0])

        if amount > cash:
            return {"success": False, "error": f"余额不足，需 {amount:.2f} 元，当前余额 {cash:.2f} 元"}

        # 扣款
        cur.execute("UPDATE virtual_account SET cash_balance = cash_balance - %s, updated_at = NOW()",
                     (amount,))

        # 记录交易
        cur.execute(
            "INSERT INTO virtual_transactions "
            "(stock_code, stock_name, tx_type, quantity, price, amount, transaction_date, created_at) "
            "VALUES (%s, %s, 'buy', %s, %s, %s, %s, NOW())",
            (code, name, quantity, price, amount, latest_str),
        )

        # 更新持仓（已有则累加均价）
        cur.execute("SELECT quantity, buy_price FROM virtual_holdings WHERE stock_code = %s", (code,))
        exist = cur.fetchone()
        if exist:
            old_qty = int(exist[0])
            old_cost = float(exist[1]) * old_qty
            new_qty = old_qty + quantity
            new_avg_price = round((old_cost + amount) / new_qty, 3)
            cur.execute(
                "UPDATE virtual_holdings SET quantity = %s, buy_price = %s, stock_name = %s WHERE stock_code = %s",
                (new_qty, new_avg_price, name, code),
            )
        else:
            cur.execute(
                "INSERT INTO virtual_holdings (stock_code, stock_name, quantity, buy_price, buy_date) "
                "VALUES (%s, %s, %s, %s, %s)",
                (code, name, quantity, price, latest_str),
            )

        conn.commit()
        return {"success": True, "message": f"买入 {code} {quantity} 股，成交价 {price:.2f}，金额 {amount:.2f} 元"}
    except Exception as e:
        conn.rollback()
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def sell_stock(code: str, quantity: int = None) -> dict:
    """以最新价卖出持仓"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()

        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest = cur.fetchone()[0]
        if not latest:
            return {"success": False, "error": "无行情数据"}

        latest_str = str(latest)
        cur.execute(
            "SELECT latest_price, stock_name FROM stock_daily "
            "WHERE stock_code = %s AND trade_date = %s",
            (code, latest_str),
        )
        row = cur.fetchone()
        if not row:
            return {"success": False, "error": f"未找到 {code} 的行情数据"}
        price = float(row[0])
        name = row[1] or ""

        # 查持仓
        cur.execute("SELECT quantity, buy_price FROM virtual_holdings WHERE stock_code = %s", (code,))
        h = cur.fetchone()
        if not h:
            return {"success": False, "error": f"未持有 {code}"}

        hold_qty = int(h[0])
        buy_price = float(h[1])
        sell_qty = quantity if quantity else hold_qty
        if sell_qty > hold_qty:
            return {"success": False, "error": f"持仓不足，持有 {hold_qty} 股，想卖 {sell_qty} 股"}

        amount = round(price * sell_qty, 4)

        # 入账
        cur.execute("UPDATE virtual_account SET cash_balance = cash_balance + %s, updated_at = NOW()",
                     (amount,))

        # 记录交易
        cur.execute(
            "INSERT INTO virtual_transactions "
            "(stock_code, stock_name, tx_type, quantity, price, amount, transaction_date, created_at) "
            "VALUES (%s, %s, 'sell', %s, %s, %s, %s, NOW())",
            (code, name, sell_qty, price, amount, latest_str),
        )

        # 更新持仓
        remaining = hold_qty - sell_qty
        if remaining > 0:
            cur.execute("UPDATE virtual_holdings SET quantity = %s WHERE stock_code = %s",
                         (remaining, code))
        else:
            cur.execute("DELETE FROM virtual_holdings WHERE stock_code = %s", (code,))

        # 计算盈亏
        cost = round(buy_price * sell_qty, 4)
        profit = round(amount - cost, 2)
        profit_pct = round((price - buy_price) / buy_price * 100, 2)

        conn.commit()
        return {
            "success": True,
            "message": f"卖出 {code} {sell_qty} 股，成交价 {price:.2f}，金额 {amount:.2f} 元",
            "profit": profit,
            "profit_pct": profit_pct,
        }
    except Exception as e:
        conn.rollback()
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_transactions(limit: int = 50) -> list:
    """获取交易记录"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute(
            "SELECT * FROM virtual_transactions ORDER BY created_at DESC LIMIT %s", (limit,)
        )
        rows = cur.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "stock_code": r["stock_code"],
                "stock_name": r["stock_name"] or "",
                "tx_type": r["tx_type"],
                "quantity": r["quantity"],
                "price": float(r["price"]) if r["price"] else 0,
                "amount": float(r["amount"]) if r["amount"] else 0,
                "transaction_date": str(r["transaction_date"]),
                "created_at": str(r["created_at"]) if r["created_at"] else "",
            })
        return result
    finally:
        conn.close()


def settle_daily() -> dict:
    """每日收盘后结算，记录当日盈亏快照"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()

        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest = cur.fetchone()[0]
        if not latest:
            return {"error": "无行情数据"}

        latest_str = str(latest)

        # 检查是否已结算
        cur.execute("SELECT id FROM virtual_daily_pnl WHERE trade_date = %s", (latest_str,))
        if cur.fetchone():
            return {"message": f"{latest_str} 已结算"}

        cur.execute("SELECT cash_balance, init_balance FROM virtual_account LIMIT 1")
        acc = cur.fetchone()
        cash = float(acc[0])
        init_bal = float(acc[1])

        # 计算持仓市值
        cur.execute("SELECT stock_code, quantity, buy_price FROM virtual_holdings")
        holdings = cur.fetchall()

        holdings_value = 0.0
        yesterday_total = None

        # 获取昨日总资产（用于计算当日盈亏）
        cur.execute(
            "SELECT total_assets FROM virtual_daily_pnl ORDER BY trade_date DESC LIMIT 1"
        )
        prev = cur.fetchone()
        if prev:
            yesterday_total = float(prev[0])

        for h in holdings:
            code = h[0]
            qty = int(h[1])
            cur.execute(
                "SELECT latest_price FROM stock_daily WHERE stock_code = %s AND trade_date = %s",
                (code, latest_str),
            )
            row = cur.fetchone()
            if row:
                holdings_value += float(row[0]) * qty
            else:
                holdings_value += float(h[2]) * qty

        total_assets = round(cash + holdings_value, 4)
        if yesterday_total and yesterday_total > 0:
            daily_pnl = round(total_assets - yesterday_total, 4)
        else:
            daily_pnl = round(total_assets - init_bal, 4)

        total_pnl = round(total_assets - init_bal, 4)

        cur.execute(
            "INSERT INTO virtual_daily_pnl "
            "(trade_date, total_assets, cash_balance, holdings_value, daily_pnl, total_pnl) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (latest_str, total_assets, cash, holdings_value, daily_pnl, total_pnl),
        )
        conn.commit()

        return {
            "trade_date": latest_str,
            "total_assets": total_assets,
            "cash_balance": cash,
            "holdings_value": holdings_value,
            "daily_pnl": daily_pnl,
            "total_pnl": total_pnl,
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def get_daily_pnl() -> list:
    """获取每日盈亏曲线数据"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute(
            "SELECT * FROM virtual_daily_pnl ORDER BY trade_date ASC"
        )
        rows = cur.fetchall()
        result = []
        for r in rows:
            result.append({
                "date": str(r["trade_date"]),
                "total_assets": float(r["total_assets"]) if r["total_assets"] else 0,
                "cash_balance": float(r["cash_balance"]) if r["cash_balance"] else 0,
                "holdings_value": float(r["holdings_value"]) if r["holdings_value"] else 0,
                "daily_pnl": float(r["daily_pnl"]) if r["daily_pnl"] else 0,
                "total_pnl": float(r["total_pnl"]) if r["total_pnl"] else 0,
            })
        return result
    finally:
        conn.close()
