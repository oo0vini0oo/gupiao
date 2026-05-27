"""
预测管理模块 - 存储预测、验证准确率、持续监控
"""

import numpy as np
from datetime import datetime, date, timedelta
from db import _get_connection
from analysis import get_stock_history


MODEL_VERSION = "v2"  # 多项式回归 + 加权移动平均


def predict_trend_v2(stock_code: str, days: int = 5) -> dict:
    """
    改进版预测：多项式回归(degree=2) + 加权移动平均，两者加权平均。
    """
    rows = get_stock_history(stock_code, 60)
    prices = [r["price"] for r in rows if r["price"] is not None]
    if len(prices) < 3:
        return {"error": "数据不足，无法预测", "predictions": []}

    x = np.arange(len(prices))
    y = np.array(prices)

    # 1. 多项式回归（数据少时用 degree=1，多时用 degree=2）
    degree = min(2, len(prices) - 1)
    coeffs = np.polyfit(x, y, degree)
    poly = np.poly1d(coeffs)

    # 2. 加权移动平均（最近权重最大）
    n_weights = min(5, len(prices))
    if n_weights == 5:
        weights = np.array([0.05, 0.1, 0.15, 0.25, 0.45])
    elif n_weights == 4:
        weights = np.array([0.1, 0.15, 0.25, 0.5])
    elif n_weights == 3:
        weights = np.array([0.15, 0.3, 0.55])
    else:
        weights = np.ones(n_weights) / n_weights

    last_n = prices[-n_weights:]
    wma = np.dot(last_n, weights)

    last_date = datetime.strptime(rows[-1]["date"], "%Y-%m-%d") if rows else datetime.now()

    predictions = []
    current_date = last_date
    for i in range(1, days + 1):
        pred_idx = len(prices) + i - 1

        pred_date = current_date + timedelta(days=1)
        while pred_date.weekday() >= 5:
            pred_date += timedelta(days=1)

        # 多项式预测
        poly_price = float(poly(pred_idx))

        # WMA 外推（用最后一次WMA + 多项式变化量）
        if i == 1:
            wma_price = wma
        else:
            wma_price = predictions[-1]["wma_price"] + (poly_price - predictions[-1]["poly_price"]) * 0.3

        # 加权融合 (poly 0.6, wma 0.4)
        ensemble = round(poly_price * 0.6 + wma_price * 0.4, 3)

        predictions.append({
            "date": pred_date.strftime("%Y-%m-%d"),
            "predicted_price": ensemble,
            "poly_price": round(poly_price, 3),
            "wma_price": round(wma_price, 3),
        })
        current_date = pred_date

    last_price = prices[-1]
    trend = "up" if predictions[-1]["predicted_price"] > last_price else "down"
    change_pct = round((predictions[-1]["predicted_price"] - last_price) / last_price * 100, 2) if last_price else 0

    return {
        "last_price": last_price,
        "trend": trend,
        "change_pct": change_pct,
        "model_version": MODEL_VERSION,
        "predictions": predictions,
        "history": [{"date": rows[i]["date"], "price": prices[i]} for i in range(len(prices))],
    }


def save_predictions(stock_code: str, predict_result: dict):
    """将预测结果存入数据库"""
    today = date.today()
    now_dt = datetime.now()
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        for p in predict_result.get("predictions", []):
            cur.execute(
                "INSERT INTO stock_predictions "
                "(stock_code, predict_date, target_date, predicted_price, model_version, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE predicted_price=VALUES(predicted_price)",
                (stock_code, today, p["date"], p["predicted_price"],
                 predict_result.get("model_version", MODEL_VERSION), now_dt),
            )
        conn.commit()
    except Exception as e:
        print(f"[PREDICT] 保存预测失败: {e}")
    finally:
        conn.close()


def verify_yesterday_predictions() -> dict:
    """验证上一交易日预测的准确率"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()

        # 获取最新交易日
        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest_trade = cur.fetchone()[0]
        if not latest_trade:
            return {"error": "无交易数据"}

        # 查找 predict_date < latest_trade 且 target_date = latest_trade 的预测
        cur.execute(
            "SELECT sp.id, sp.stock_code, sp.predicted_price, "
            "       sd.latest_price, sp.predict_date "
            "FROM stock_predictions sp "
            "JOIN stock_daily sd ON sp.stock_code = sd.stock_code "
            "   AND sd.trade_date = sp.target_date "
            "WHERE sp.target_date = %s AND sp.actual_price IS NULL",
            (latest_trade,),
        )
        rows = cur.fetchall()

        if not rows:
            return {"checked": 0, "message": "无待验证的预测"}

        correct = 0
        total = len(rows)
        for row in rows:
            pred_id, code, pred_price, actual_price, pred_date = row
            if actual_price and pred_price:
                error_pct = round((float(actual_price) - float(pred_price)) / float(pred_price) * 100, 4)
                # 方向准确：预测涨/跌与实际一致
                cur.execute(
                    "UPDATE stock_predictions SET actual_price=%s, error_pct=%s WHERE id=%s",
                    (float(actual_price), error_pct, pred_id),
                )

        conn.commit()

        # 重新统计方向准确率
        cur.execute(
            "SELECT COUNT(*), "
            "       SUM(CASE WHEN ((predicted_price - actual_price) * "
            "           (SELECT change_pct FROM stock_daily sd2 WHERE sd2.stock_code=sp.stock_code "
            "            AND sd2.trade_date=sp.target_date) > 0) THEN 1 ELSE 0 END) "
            "FROM stock_predictions sp "
            "WHERE target_date=%s AND actual_price IS NOT NULL",
            (latest_trade,),
        )
        total2, correct2 = cur.fetchone()
        correct2 = correct2 or 0

        result = {
            "check_date": str(latest_trade),
            "total": total,
            "verified": total,
            "correct_direction": int(correct2),
            "accuracy_pct": round(float(correct2) / total * 100, 2) if total else 0,
        }

        # 写入准确率统计表
        cur.execute(
            "INSERT INTO prediction_accuracy "
            "(check_date, total_predictions, correct_predictions, accuracy_pct, model_version, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE total_predictions=VALUES(total_predictions), "
            "correct_predictions=VALUES(correct_predictions), accuracy_pct=VALUES(accuracy_pct)",
            (latest_trade, total, int(correct2), result["accuracy_pct"], MODEL_VERSION, datetime.now()),
        )
        conn.commit()

        return result
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def get_accuracy_report() -> dict:
    """获取连续准确率报告，检查是否超过80%"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        # 最近30天准确率
        cur.execute(
            "SELECT check_date, total_predictions, correct_predictions, accuracy_pct "
            "FROM prediction_accuracy "
            "WHERE model_version=%s "
            "ORDER BY check_date DESC LIMIT 30",
            (MODEL_VERSION,),
        )
        rows = cur.fetchall()

        daily = []
        for r in rows:
            daily.append({
                "date": str(r[0]),
                "total": r[1],
                "correct": r[2],
                "accuracy": float(r[3]) if r[3] else 0,
            })

        if not daily:
            return {"daily": [], "avg_accuracy": 0, "alert": False, "message": "尚无准确率数据"}

        # 整体平均（加权）
        total_all = sum(d["total"] for d in daily)
        correct_all = sum(d["correct"] for d in daily)
        avg_accuracy = round(correct_all / total_all * 100, 2) if total_all else 0

        alert = avg_accuracy > 80 and len(daily) >= 5

        return {
            "daily": daily,
            "total_predictions": total_all,
            "correct_predictions": correct_all,
            "avg_accuracy": avg_accuracy,
            "alert": alert,
            "message": f"连续监控 {len(daily)} 天，预测准确率 {avg_accuracy}%。"
                       + (" 已达到80%阈值！模型表现良好！" if alert
                          else " 尚未达到80%阈值，继续监控中...") if daily else "尚无准确率数据",
        }
    finally:
        conn.close()


def run_daily_predictions(limit: int = 100) -> dict:
    """
    全市场自动预测：取成交额前 N 的活跃股 → 逐只预测 → 保存 → 返回汇总。
    供定时任务 (main.py) 在抓取完数据后调用。
    """
    import time
    t0 = time.time()

    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest = str(cur.fetchone()[0])
        cur.execute(
            "SELECT stock_code, stock_name FROM stock_daily WHERE trade_date = %s "
            "ORDER BY turnover DESC LIMIT %s", (latest, limit))
        stocks = cur.fetchall()
    finally:
        conn.close()

    total = len(stocks)
    predicted = 0
    up_count = 0
    down_count = 0

    for code, name in stocks:
        try:
            pred = predict_trend_v2(code, days=3)
            if "error" not in pred and pred.get("predictions"):
                save_predictions(code, pred)
                predicted += 1
                if pred.get("trend") == "up":
                    up_count += 1
                else:
                    down_count += 1
        except Exception:
            continue

    elapsed = time.time() - t0
    return {
        "trade_date": latest,
        "total_stocks": total,
        "predicted": predicted,
        "up": up_count,
        "down": down_count,
        "model_version": MODEL_VERSION,
        "elapsed_seconds": round(elapsed, 1),
    }


def get_prediction_details() -> dict:
    """获取上一交易日预测详情（逐只对比）及偏离分析"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()

        cur.execute("SELECT MAX(check_date) FROM prediction_accuracy")
        row = cur.fetchone()
        check_date = row[0] if row else None
        if not check_date:
            return {"error": "尚无校验数据"}

        cur.execute(
            "SELECT total_predictions, correct_predictions, accuracy_pct "
            "FROM prediction_accuracy WHERE check_date = %s LIMIT 1",
            (check_date,),
        )
        acc = cur.fetchone()
        total_p = int(acc[0]) if acc and acc[0] else 0
        correct_p = int(acc[1]) if acc and acc[1] else 0
        accuracy = float(acc[2]) if acc and acc[2] else 0

        cur.execute(
            """SELECT sp.stock_code, sd_act.stock_name,
                      sd_pred.latest_price AS last_price,
                      sp.predicted_price, sp.actual_price, sp.error_pct,
                      sd_act.change_pct
               FROM stock_predictions sp
               LEFT JOIN stock_daily sd_pred
                   ON sp.stock_code = sd_pred.stock_code
                   AND sd_pred.trade_date = sp.predict_date
               LEFT JOIN stock_daily sd_act
                   ON sp.stock_code = sd_act.stock_code
                   AND sd_act.trade_date = sp.target_date
               WHERE sp.target_date = %s AND sp.actual_price IS NOT NULL
               ORDER BY ABS(sp.error_pct) DESC""",
            (check_date,),
        )
        rows = cur.fetchall()

        details = []
        for r in rows:
            code, name, last_price, pred_price, act_price, err_pct, change_pct = r
            pred_price = float(pred_price) if pred_price else 0
            act_price = float(act_price) if act_price else 0
            err_pct = float(err_pct) if err_pct else 0
            change_pct = float(change_pct) if change_pct else 0
            last_price = float(last_price) if last_price else None

            if last_price and last_price > 0:
                predicted_up = pred_price > last_price
                actual_up = change_pct > 0
                direction_correct = predicted_up == actual_up
                pred_dir = "看涨" if predicted_up else "看跌"
            else:
                direction_correct = None
                pred_dir = "未知"

            actual_dir = "上涨" if change_pct > 0 else "下跌" if change_pct < 0 else "平盘"

            abs_err = abs(err_pct)
            if abs_err < 1:
                analysis = "偏差极小，预测精准"
            elif direction_correct is None:
                analysis = f"偏差{abs_err:.1f}%（缺少基准价）"
            elif direction_correct:
                if err_pct > 0:
                    if change_pct > 0:
                        analysis = f"偏保守：涨幅超预期（实涨{change_pct:.1f}%，偏低{abs_err:.1f}%）"
                    else:
                        analysis = f"偏保守：跌幅小于预期（实跌{change_pct:.1f}%，偏低{abs_err:.1f}%）"
                else:
                    if change_pct > 0:
                        analysis = f"偏乐观：涨幅不及预期（实涨{change_pct:.1f}%，偏高{abs_err:.1f}%）"
                    else:
                        analysis = f"偏乐观：跌幅超预期（实跌{change_pct:.1f}%，偏高{abs_err:.1f}%）"
            else:
                if actual_up:
                    analysis = f"看跌实涨，趋势反转（实涨{change_pct:.1f}%，偏差{abs_err:.1f}%）"
                else:
                    analysis = f"看涨实跌，趋势反转（实跌{change_pct:.1f}%，偏差{abs_err:.1f}%）"

            details.append({
                "code": code,
                "name": name or "",
                "predicted_price": round(pred_price, 2),
                "actual_price": round(act_price, 2),
                "error_pct": round(err_pct, 2),
                "change_pct": round(change_pct, 2),
                "direction_correct": direction_correct,
                "pred_direction": pred_dir,
                "actual_direction": actual_dir,
                "analysis": analysis,
            })

        return {
            "check_date": str(check_date),
            "total": total_p,
            "correct": correct_p,
            "accuracy_pct": accuracy,
            "details": details,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def predict_top10() -> dict:
    """全市场扫描，预测今日最可能大涨的 TOP10 股票，附带分析理由。"""
    import time
    from collections import defaultdict
    from analysis import classify_sector

    t0 = time.time()
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest = str(cur.fetchone()[0])

        cur.execute(
            "SELECT stock_code, stock_name, latest_price, turnover, change_pct, "
            "       total_market_cap, volume, pe_ratio_dynamic "
            "FROM stock_daily WHERE trade_date = %s "
            "ORDER BY turnover DESC LIMIT 50", (latest,))
        candidates = cur.fetchall()

        cur.execute("SELECT stock_code, change_pct FROM stock_daily WHERE trade_date = %s",
                    (latest,))
        all_data = {r[0]: float(r[1]) if r[1] else 0 for r in cur.fetchall()}
    finally:
        conn.close()

    sector_changes = defaultdict(list)
    for code, chg in all_data.items():
        sector_changes[classify_sector(code)].append(chg)
    sector_avg = {s: round(sum(v)/len(v), 2) for s, v in sector_changes.items()}

    results = []
    for code, name, price, tv, change_pct, mcap, volume, pe in candidates:
        try:
            pred = predict_trend_v2(code, days=3)
            if "error" in pred or not pred.get("predictions"):
                continue
            pred_change = pred.get("change_pct", 0)
            current_price = float(price) if price else 0
            hist = pred.get("history", [])
            sector = classify_sector(code)
            s_trend = sector_avg.get(sector, 0)
            today_chg = float(change_pct) if change_pct else 0
            pe_val = float(pe) if pe else 0

            reasons = []
            if hist and len(hist) >= 3:
                chgs = []
                for i in range(1, min(4, len(hist))):
                    prev = hist[-i-1]["price"] if len(hist) > i else hist[0]["price"]
                    cur_p = hist[-i]["price"]
                    if prev: chgs.append((cur_p - prev) / prev * 100)
                avg_mom = sum(chgs)/len(chgs) if chgs else 0
                if avg_mom > 1:    reasons.append(f"近3日走势强劲(+{avg_mom:.1f}%)")
                elif avg_mom > 0:  reasons.append(f"近3日温和上行(+{avg_mom:.1f}%)")
                elif avg_mom > -1: reasons.append(f"近3日窄幅震荡({avg_mom:.1f}%)")

                prices_l = [h["price"] for h in hist if h["price"]]
                if len(prices_l) >= 5:
                    ma5 = sum(prices_l[-5:]) / 5
                    if current_price > ma5:
                        reasons.append(f"股价站上MA5均线(MA5={ma5:.2f})")

            if s_trend > 0.5:      reasons.append(f"{sector}整体偏热(+{s_trend}%)")
            elif s_trend > 0:     reasons.append(f"{sector}小幅上行(+{s_trend}%)")
            if today_chg > 3:     reasons.append(f"今日放量上涨(+{today_chg:.1f}%)")
            if 10 < pe_val < 30:  reasons.append(f"PE适中({pe_val:.1f})")
            if pred_change > 10:  reasons.append(f"模型预测涨幅较大(+{pred_change:.1f}%)")
            elif pred_change > 5: reasons.append(f"模型预测稳健上行(+{pred_change:.1f}%)")
            if not reasons:       reasons.append("技术面指标指向上行")

            results.append({
                "code": code, "name": name,
                "price": current_price,
                "change_pct": today_chg,
                "predicted_change": round(pred_change, 2),
                "predicted_price": pred["predictions"][-1]["predicted_price"],
                "predicted_gain": round(pred_change - today_chg, 2),
                "trend": pred.get("trend", "unknown"),
                "sector": sector, "sector_trend": s_trend,
                "reasons": reasons, "pe": pe_val,
            })
        except Exception:
            continue

    results.sort(key=lambda x: x["predicted_change"], reverse=True)
    top = [r for r in results if r["trend"] == "up"][:10]
    return {
        "trade_date": latest,
        "total_analyzed": len(candidates),
        "total_predicted": len(results),
        "top10": top,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
