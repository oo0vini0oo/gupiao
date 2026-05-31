"""
预测管理模块 - 存储预测、验证准确率、持续监控
"""

import numpy as np
from datetime import datetime, date, timedelta
from db import _get_connection
from analysis import get_stock_history
from prediction_config import get_config
from trading_calendar import next_trading_day

MODEL_VERSION = "v2"  # 多项式回归 + 加权移动平均


def predict_trend_v2(stock_code: str, days: int = None, _skip_analysis: bool = False) -> dict:
    """
    改进版预测：多项式回归(degree=2) + 加权移动平均，两者加权平均。
    _skip_analysis: 为 True 时跳过板块/PE/新闻/分析理由（批量调用时避免耗时）
    """
    _cfg = get_config()
    if days is None:
        days = _cfg['analysis']['predict_days']

    rows = get_stock_history(stock_code, _cfg['analysis']['history_days'])
    prices = [r["price"] for r in rows if r["price"] is not None]
    if len(prices) < 3:
        return {"error": "数据不足，无法预测", "predictions": []}

    x = np.arange(len(prices))
    y = np.array(prices)

    # 1. 多项式回归（数据少时用 degree=1，多时用 degree=2）
    degree = min(_cfg['model']['poly_degree'], len(prices) - 1)
    coeffs = np.polyfit(x, y, degree)
    poly = np.poly1d(coeffs)

    # 2. 加权移动平均（最近权重最大）
    n_weights = min(5, len(prices))
    if n_weights == 5:
        weights = np.array(_cfg['model']['wma_weights_5'])
    elif n_weights == 4:
        weights = np.array(_cfg['model']['wma_weights_4'])
    elif n_weights == 3:
        weights = np.array(_cfg['model']['wma_weights_3'])
    else:
        weights = np.ones(n_weights) / n_weights

    last_n = prices[-n_weights:]
    wma = np.dot(last_n, weights)

    last_date = datetime.strptime(rows[-1]["date"], "%Y-%m-%d") if rows else datetime.now()

    predictions = []
    current_date = last_date
    last_price = prices[-1]
    for i in range(1, days + 1):
        pred_idx = len(prices) + i - 1

        pred_date = datetime.combine(next_trading_day(current_date.date()), datetime.min.time())

        # 多项式预测
        poly_price = float(poly(pred_idx))

        # WMA 外推（用最后一次WMA + 多项式变化量）
        if i == 1:
            wma_price = wma
        else:
            wma_price = predictions[-1]["wma_price"] + (poly_price - predictions[-1]["poly_price"]) * _cfg['model']['damping_factor']

        # 加权融合
        ensemble_raw = poly_price * _cfg['model']['ensemble_poly_weight'] + wma_price * _cfg['model']['ensemble_wma_weight']

        # 硬限幅：预测价不超过最新价的 ±10%，防止任何离谱预测
        max_step = _cfg['model']['max_step_change_pct']
        step_max = round(last_price * (1 + max_step / 100), 3)
        step_min = round(last_price * (1 - max_step / 100), 3)
        ensemble = round(max(step_min, min(step_max, ensemble_raw)), 3)

        predictions.append({
            "date": pred_date.strftime("%Y-%m-%d"),
            "predicted_price": ensemble,
            "poly_price": round(poly_price, 3),
            "wma_price": round(wma_price, 3),
        })
        current_date = pred_date

    trend = "up" if predictions[-1]["predicted_price"] > last_price else "down"
    change_pct = round((predictions[-1]["predicted_price"] - last_price) / last_price * 100, 2) if last_price else 0

    # ── 统一分析逻辑（批量场景通过 _skip_analysis 跳过） ────
    reason = "数据有限，参考模型预测"
    if not _skip_analysis:
        volumes = [r["volume"] for r in rows if r.get("volume") is not None]
        today_chg = rows[-1]["change_pct"] if rows and rows[-1].get("change_pct") is not None else 0

        # 板块热度
        try:
            from analysis import classify_sector, get_sector_summary
            sector = classify_sector(stock_code)
            sectors = get_sector_summary()
            s_trend = 0
            for s in sectors:
                if s["sector"] == sector:
                    s_trend = s["avg_change_pct"]
                    break
        except Exception:
            sector = ""
            s_trend = 0

        # 估值
        pe_val = 0
        try:
            conn = _get_connection(with_db=True)
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT pe_ratio_dynamic FROM stock_daily "
                    "WHERE stock_code = %s ORDER BY trade_date DESC LIMIT 1",
                    (stock_code,)
                )
                pe_row = cur.fetchone()
                if pe_row and pe_row[0]:
                    pe_val = float(pe_row[0])
            finally:
                conn.close()
        except Exception:
            pass

        # 新闻 + 名称
        news_keywords = _get_cached_news()
        name = rows[-1].get("name", "") if rows else ""

        summary_parts, risk_parts = _analyze_stock(
            prices, volumes, today_chg, s_trend, pe_val,
            sector, name, news_keywords,
            pred_change_pct=change_pct,
        )

        # 组装理由
        all_parts = []
        all_parts.extend(summary_parts)
        if risk_parts:
            all_parts.append("风险提示：" + "；".join(risk_parts))
        reason = "；".join(all_parts) if all_parts else "数据有限，参考模型预测"

    return {
        "last_price": last_price,
        "trend": trend,
        "change_pct": change_pct,
        "reason": reason,
        "model_version": MODEL_VERSION,
        "predictions": predictions,
        "history": [{"date": rows[i]["date"], "price": prices[i]} for i in range(len(prices))],
    }


def save_predictions(stock_code: str, predict_result: dict):
    """将预测结果存入数据库（预测的是未来交易日，不受今天是否交易日限制）"""
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
    """验证上一交易日预测的准确率（基于涨跌幅方向判定）"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()

        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest_trade = cur.fetchone()[0]
        if not latest_trade:
            return {"error": "无交易数据"}

        # 查找待验证预测：target_date = latest_trade, actual_price IS NULL
        cur.execute(
            """SELECT sp.id, sp.stock_code, sp.predicted_price,
                      sd_act.latest_price AS actual_price,
                      sd_act.pre_close AS baseline_price,
                      sd_act.change_pct, sp.predict_date
               FROM stock_predictions sp
               JOIN stock_daily sd_act
                   ON sp.stock_code = sd_act.stock_code
                   AND sd_act.trade_date = sp.target_date
               WHERE sp.target_date = %s AND sp.actual_price IS NULL""",
            (latest_trade,),
        )
        rows = cur.fetchall()

        if not rows:
            return {"checked": 0, "message": "无待验证的预测"}

        total = 0
        correct = 0
        pred_date_set = set()
        for row in rows:
            pred_id, code, pred_price, actual_price, baseline_price, change_pct, pred_date = row
            if actual_price is None or pred_price is None:
                continue

            if pred_date:
                pred_date_set.add(str(pred_date))

            pred_price = float(pred_price)
            actual_price = float(actual_price)
            change_pct = float(change_pct) if change_pct else 0
            baseline_price = float(baseline_price) if baseline_price else None

            # 必须要有基准价才能判断方向（使用 target_date 的 pre_close 作为基准）
            if not baseline_price or baseline_price <= 0:
                continue

            # 预测涨跌幅（基于 pre_close = 前一日收盘价）
            pred_change_pct = (pred_price - baseline_price) / baseline_price * 100
            predicted_up = pred_change_pct > 0
            actual_up = change_pct > 0

            is_correct = predicted_up == actual_up
            if is_correct:
                correct += 1
            total += 1

            # 保存实际价和误差
            error_pct = round((actual_price - pred_price) / pred_price * 100, 4)
            cur.execute(
                "UPDATE stock_predictions SET actual_price=%s, error_pct=%s WHERE id=%s",
                (actual_price, error_pct, pred_id),
            )

        conn.commit()

        sorted_pred_dates = sorted(pred_date_set)
        result = {
            "check_date": str(latest_trade),
            "predict_date_min": sorted_pred_dates[0] if sorted_pred_dates else str(latest_trade),
            "total": total,
            "verified": total,
            "correct_direction": correct,
            "accuracy_pct": round(correct / total * 100, 2) if total else 0,
        }

        cur.execute(
            "INSERT INTO prediction_accuracy "
            "(check_date, total_predictions, correct_predictions, accuracy_pct, model_version, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE total_predictions=VALUES(total_predictions), "
            "correct_predictions=VALUES(correct_predictions), accuracy_pct=VALUES(accuracy_pct)",
            (latest_trade, total, correct, result["accuracy_pct"], MODEL_VERSION, datetime.now()),
        )
        conn.commit()

        # 附加详细分析
        try:
            analysis = analyze_prediction_errors(str(latest_trade))
            result["analysis"] = analysis
        except Exception:
            pass

        return result
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def analyze_prediction_errors(check_date: str) -> dict:
    """
    对指定交易日进行详细的预测误差分析：
    - 按板块分组统计准确率
    - 误差分布（过高/过低/方向错误）
    - 生成改进建议
    """
    from collections import defaultdict
    from analysis import classify_sector

    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT sp.stock_code, sd.stock_name, sp.predicted_price,
                   sp.actual_price, sp.error_pct, sd.change_pct,
                   sd.pre_close
            FROM stock_predictions sp
            JOIN stock_daily sd ON sp.stock_code = sd.stock_code
                AND sd.trade_date = sp.target_date
            WHERE sp.target_date = %s AND sp.actual_price IS NOT NULL
        """, (check_date,))
        rows = cur.fetchall()

        if not rows:
            return {"date": check_date, "total": 0, "message": "无验证数据"}

        total = len(rows)
        sector_stats = defaultdict(lambda: {"total": 0, "correct": 0, "errors": []})
        error_freq = {"overly_optimistic": 0, "overly_pessimistic": 0, "wrong_direction": 0}

        for row in rows:
            code, name, pred_price, actual_price, error_pct, change_pct, pre_close = row
            pred_price = float(pred_price) if pred_price else 0
            actual_price = float(actual_price) if actual_price else 0
            error_pct = float(error_pct) if error_pct else 0
            change_pct = float(change_pct) if change_pct else 0
            pre_close = float(pre_close) if pre_close else 0

            sector = classify_sector(code)
            s = sector_stats[sector]
            s["total"] += 1

            if pre_close > 0:
                pred_change = (pred_price - pre_close) / pre_close * 100
                is_correct = (pred_change > 0) == (change_pct > 0)
                if is_correct:
                    s["correct"] += 1
                else:
                    s["errors"].append({
                        "code": code, "name": name or "",
                        "pred_change": round(pred_change, 2),
                        "actual_change": round(change_pct, 2),
                        "error_pct": round(error_pct, 2),
                    })
                    error_freq["wrong_direction"] += 1

                if error_pct > 5:
                    error_freq["overly_optimistic"] += 1
                elif error_pct < -5:
                    error_freq["overly_pessimistic"] += 1

        # 板块准确率
        sector_accuracy = {}
        for sec, st in sorted(sector_stats.items()):
            acc = round(st["correct"] / st["total"] * 100, 2) if st["total"] else 0
            st["errors"].sort(key=lambda x: abs(x["error_pct"]), reverse=True)
            sector_accuracy[sec] = {
                "total": st["total"],
                "correct": st["correct"],
                "accuracy": acc,
                "worst_errors": st["errors"][:3],
            }

        overall = round(sum(s["correct"] for s in sector_stats.values()) / total * 100, 2) if total else 0

        # 改进建议
        suggestions = []
        low_acc = [(sec, info) for sec, info in sector_accuracy.items() if info["accuracy"] < 50 and info["total"] >= 3]
        if low_acc:
            worst = min(low_acc, key=lambda x: x[1]["accuracy"])
            suggestions.append(f"板块 {worst[0]} 准确率仅 {worst[1]['accuracy']}%（共{worst[1]['total']}只），建议调整个股预测参数")

        if error_freq["overly_optimistic"] > total * 0.3:
            suggestions.append("超过30%的预测偏乐观（误差 >5%），建议增大阻尼系数或降低多项式权重")
        if error_freq["overly_pessimistic"] > total * 0.3:
            suggestions.append("超过30%的预测偏保守（误差 < -5%），建议提高模型敏感度")
        if overall < 60:
            suggestions.append("整体准确率偏低（< 60%），建议增大历史数据窗口或调整融合权重")
        if not suggestions:
            suggestions.append("模型表现正常，建议持续监控")

        return {
            "date": check_date,
            "total": total,
            "overall_accuracy": overall,
            "sector_breakdown": sector_accuracy,
            "error_distribution": error_freq,
            "suggestions": suggestions,
        }
    except Exception as e:
        return {"date": check_date, "error": str(e)}
    finally:
        conn.close()


def fix_actual_prices(check_date: str = None):
    """修复因基准数据问题导致的 actual_price = pre_close 错误"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        where = "AND sp.target_date = %s" if check_date else ""
        params = (check_date,) if check_date else ()
        cur.execute(f"""
            UPDATE stock_predictions sp
            JOIN stock_daily sd ON sp.stock_code = sd.stock_code AND sd.trade_date = sp.target_date
            SET sp.actual_price = sd.latest_price,
                sp.error_pct = ROUND((sd.latest_price - sp.predicted_price) / sp.predicted_price * 100, 4)
            WHERE sp.actual_price IS NOT NULL {where}
              AND sp.actual_price != sd.latest_price
        """, params)
        affected = cur.rowcount
        conn.commit()

        # 清除对应的 accuracy 记录，下次 verify 会重建
        if check_date:
            cur.execute("DELETE FROM prediction_accuracy WHERE check_date = %s", (check_date,))
        else:
            cur.execute("DELETE FROM prediction_accuracy")
        conn.commit()

        print(f"[FIX] 已修正 {affected} 条 actual_price，已清除 accuracy 记录请重新验证")
        return affected
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def get_all_prediction_records(check_date: str = None) -> dict:
    """获取所有预测校验历史记录，可选按日期筛选"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()

        # 获取所有校验日期（直接从原始数据统计，确保与明细一致）
        cur.execute("""
            SELECT sp.target_date,
                   COUNT(*) AS total,
                   SUM(CASE WHEN ((sp.predicted_price-sd.pre_close)/sd.pre_close > 0 AND sd.change_pct > 0)
                              OR ((sp.predicted_price-sd.pre_close)/sd.pre_close < 0 AND sd.change_pct < 0)
                        THEN 1 ELSE 0 END) AS correct
            FROM stock_predictions sp
            JOIN stock_daily sd ON sp.stock_code=sd.stock_code AND sd.trade_date=sp.target_date
            WHERE sp.actual_price IS NOT NULL
            GROUP BY sp.target_date
            ORDER BY sp.target_date DESC
        """)
        dates = []
        for r in cur.fetchall():
            total = int(r[1])
            correct = int(r[2] or 0)
            dates.append({
                "date": str(r[0]),
                "total": total,
                "correct": correct,
                "accuracy": round(correct / total * 100, 2) if total else 0,
            })

        # 如果指定日期，获取该日明细
        details = []
        if check_date or (dates and not check_date):
            target = check_date or dates[0]["date"]
            cur.execute("""
                SELECT sp.stock_code, sd_act.stock_name,
                       sp.predicted_price, sp.actual_price,
                       sd_act.latest_price,
                       sd_act.change_pct,
                       sd_act.pre_close,
                       sp.predict_date
                FROM stock_predictions sp
                JOIN stock_daily sd_act
                    ON sp.stock_code = sd_act.stock_code
                    AND sd_act.trade_date = sp.target_date
                WHERE sp.target_date = %s AND sp.actual_price IS NOT NULL
                ORDER BY sp.stock_code
            """, (target,))
            for r in cur.fetchall():
                code, name, pred_price, act_price, latest_price, chg, pre_close, predict_date = r
                pred_price = float(pred_price) if pred_price else 0
                act_price = float(act_price) if act_price else 0
                latest_price = float(latest_price) if latest_price else 0
                pre_close = float(pre_close) if pre_close else 0
                chg = float(chg) if chg else 0

                # 预估涨幅以预测时的收盘价（pre_close）为基准
                if pre_close > 0:
                    pred_chg = (pred_price - pre_close) / pre_close * 100
                    predicted_up = pred_chg > 0
                else:
                    pred_chg = None
                    predicted_up = None

                actual_up = chg > 0

                if predicted_up is not None:
                    direction_correct = predicted_up == actual_up
                    pred_dir = "看涨" if predicted_up else "看跌"
                else:
                    direction_correct = None
                    pred_dir = "未知"

                actual_dir = "上涨" if chg > 0 else "下跌" if chg < 0 else "平盘"

                details.append({
                    "code": code,
                    "name": name or "",
                    "predict_date": str(predict_date),
                    "last_price": round(latest_price, 2) if latest_price else None,
                    "predicted_price": round(pred_price, 2),
                    "actual_price": round(act_price, 2),
                    "pred_change_pct": round(pred_chg, 2) if pred_chg is not None else None,
                    "change_pct": round(chg, 2),
                    "direction_correct": direction_correct,
                    "pred_direction": pred_dir,
                    "actual_direction": actual_dir,
                })

            # 汇总统计
            total_d = len(details)
            correct_d = sum(1 for d in details if d["direction_correct"] is True)
            accuracy_d = round(correct_d / total_d * 100, 2) if total_d else 0

            pd_dates = sorted(set(d["predict_date"] for d in details if d.get("predict_date")))
            return {
                "dates": dates,
                "selected_date": target,
                "predict_date_min": pd_dates[0] if pd_dates else target,
                "details": details,
                "total": total_d,
                "correct": correct_d,
                "accuracy_pct": accuracy_d,
            }

        return {"dates": dates, "details": []}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def get_accuracy_report() -> dict:
    """获取连续准确率报告，检查是否超过80%（直接从原始数据计算，与记录页口径一致）"""
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        # 按目标日期分组统计（INNER JOIN 确保只有有行情数据的记录）
        cur.execute(
            """SELECT sp.target_date,
                      COUNT(*) AS total,
                      SUM(CASE WHEN ((sp.predicted_price-sd.pre_close)/sd.pre_close > 0 AND sd.change_pct > 0)
                                OR ((sp.predicted_price-sd.pre_close)/sd.pre_close < 0 AND sd.change_pct < 0)
                           THEN 1 ELSE 0 END) AS correct
               FROM stock_predictions sp
               JOIN stock_daily sd ON sp.stock_code=sd.stock_code AND sd.trade_date=sp.target_date
               WHERE sp.actual_price IS NOT NULL
               GROUP BY sp.target_date
               ORDER BY sp.target_date DESC
               LIMIT 30"""
        )
        rows = cur.fetchall()

        daily = []
        for r in rows:
            total = int(r[1])
            correct = int(r[2] or 0)
            acc = round(correct / total * 100, 2) if total else 0
            daily.append({
                "date": str(r[0]),
                "total": total,
                "correct": correct,
                "accuracy": acc,
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


def run_daily_predictions(limit: int = None) -> dict:
    """
    全市场自动预测：取成交额前 N 的活跃股 → 逐只预测 → 保存 → 返回汇总。
    供定时任务 (main.py) 在抓取完数据后调用。
    """
    _cfg = get_config()
    if limit is None:
        limit = _cfg['filters']['scan_top_n']
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
            pred = predict_trend_v2(code, days=1, _skip_analysis=True)
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


def _fill_gap_trading_dates():
    """补采模式：检查 stock_daily 是否包含最新交易日数据，缺失则补采"""
    from datetime import date
    from trading_calendar import is_trading_day, previous_trading_day
    from config import MYSQL_CONFIG, DATABASE
    import pymysql

    today = date.today()
    conn = pymysql.connect(**MYSQL_CONFIG, database=DATABASE)
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest = cur.fetchone()[0]

        if is_trading_day(today):
            # 今天是交易日：检查是否有今天数据
            if not latest or latest < today:
                print(f"  [补采] 缺失今日交易数据 {today.isoformat()}，正在采集...", flush=True)
                from fetcher import fetch_all_stocks
                from manager import save_daily_data
                records = fetch_all_stocks()
                if records:
                    save_daily_data(records, today.isoformat())
                    print(f"  [补采] 今日数据采集完成 ({len(records)} 只)", flush=True)
        else:
            # 非交易日：检查最近交易日数据是否存在
            last_td = previous_trading_day(today)
            if not latest or latest < last_td:
                print(f"  [补采] DB最新为 {latest}，缺失 {last_td} 数据", flush=True)
                # 非交易日调用API获取的是最近交易日快照，可作补充
                from fetcher import fetch_all_stocks
                from manager import save_daily_data
                records = fetch_all_stocks()
                if records:
                    save_daily_data(records, last_td.isoformat())
                    print(f"  [补采] 补充 {last_td} 数据完成 ({len(records)} 只)", flush=True)
    finally:
        conn.close()


def run_evening_predict(limit: int = None, catch_up: bool = False) -> dict:
    """晚8点（或补采）执行：刷新新闻缓存 → 预测下一交易日。
    catch_up=True 时先填补缺失的交易日数据再执行。"""
    _cfg = get_config()
    if limit is None:
        limit = _cfg['filters']['scan_top_n']
    import time, sys
    t0 = time.time()

    # ── 补采模式：先填补缺失的交易日数据 ──
    if catch_up:
        print(f"  [补采] 检测到数据可能不完整，先检查日期连续性...", flush=True)
        _fill_gap_trading_dates()

    # 强制刷新新闻缓存（确保获取当日新闻联播信息）
    _news_cache["time"] = 0
    keywords_data = _get_cached_news(max_age=0)
    keywords_list = keywords_data.get("keywords", [])
    print(f"  [新闻] 已获取 {len(keywords_list)} 个热点关键词用于预测", flush=True)

    # 执行预测
    mode = "补采预测" if catch_up else "晚8点自动预测"
    print(f"  [预测] {mode}（扫描 {limit} 只活跃股）", flush=True)
    result = run_daily_predictions(limit=limit)
    result["news_keywords"] = len(keywords_list)
    result["elapsed_seconds"] = round(time.time() - t0, 1)
    print(f"  成功预测: {result['predicted']} 只", flush=True)
    print(f"  看涨: {result['up']} 只 | 看跌: {result['down']} 只", flush=True)
    print(f"  耗时 {result['elapsed_seconds']}s", flush=True)
    return result


# ── 高涨幅候选扫描 ─────────────────────────────────
_surge_cache = {"time": 0, "data": None}

def scan_surge_candidates(scan_n: int = 300, min_change: float = 3.0) -> list:
    """
    扫描更多股票寻找高涨幅候选。
    scan_n: 按成交额取前 N 只（默认 300）
    min_change: 最小预估涨幅阈值（默认 3%，避免全是 0）
    结果按预估涨幅降序排列。
    """
    import time
    from collections import defaultdict
    from analysis import classify_sector

    # 缓存有效期 30 分钟
    if time.time() - _surge_cache["time"] < 1800 and _surge_cache["data"] is not None:
        return _surge_cache["data"]

    _cfg = get_config()
    t0 = time.time()
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest = str(cur.fetchone()[0])

        # 取成交额前 scan_n 只股票
        cur.execute(
            "SELECT stock_code, stock_name, latest_price, change_pct, "
            "       total_market_cap, volume, pe_ratio_dynamic, pre_close, turnover "
            "FROM stock_daily WHERE trade_date = %s "
            "ORDER BY turnover DESC LIMIT %s",
            (latest, scan_n),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    # 板块热度
    sector_changes = defaultdict(list)
    _all_conn = _get_connection(with_db=True)
    try:
        _cur = _all_conn.cursor()
        _cur.execute(
            "SELECT stock_code, change_pct FROM stock_daily WHERE trade_date = %s",
            (latest,),
        )
        all_data = {r[0]: float(r[1]) if r[1] else 0 for r in _cur.fetchall()}
    finally:
        _all_conn.close()

    for code, chg in all_data.items():
        sector_changes[classify_sector(code)].append(chg)
    sector_avg = {s: round(sum(v) / len(v), 2) for s, v in sector_changes.items()}

    news_keywords = _get_cached_news()
    candidates = []

    for r in rows:
        code, name, price, change_pct, mcap, volume, pe, pre_close, turnover = r
        try:
            pred = predict_trend_v2(code, days=1, _skip_analysis=True)
            if "error" in pred or not pred.get("predictions"):
                continue
            p = pred["predictions"][0]
            pred_price = float(p["predicted_price"])
            current_price = float(price) if price else 0
            if current_price <= 0:
                continue
            pred_change = round((pred_price - current_price) / current_price * 100, 2)

            if pred_change < min_change:
                continue

            sector = classify_sector(code)
            s_trend = sector_avg.get(sector, 0)
            prices_l, volumes_l = _get_history_data(code)
            today_chg = float(change_pct) if change_pct else 0
            pe_val = float(pe) if pe else 0
            pre_close_val = float(pre_close) if pre_close else None

            summary_parts, risk_parts = _analyze_stock(
                prices_l, volumes_l, today_chg, s_trend, pe_val,
                sector, name or "", news_keywords, pred_change_pct=pred_change,
            )

            # 保存预测
            save_predictions(code, pred)

            score = _calc_score(pred_change, today_chg, s_trend, summary_parts, news_keywords)
            summary = "；".join(summary_parts) if summary_parts else ""
            risk = "风险提示：" + "；".join(risk_parts) if risk_parts else ""

            candidates.append({
                "code": code,
                "name": name or "",
                "price": current_price,
                "sector": sector,
                "change_pct": today_chg,
                "predicted_price": pred_price,
                "predicted_change": pred_change,
                "trend": "up" if pred_change > 0 else "down",
                "score": score,
                "summary": summary,
                "risk_warning": risk,
                "turnover": float(turnover) if turnover else 0,
            })
        except Exception:
            continue

    candidates.sort(key=lambda x: -x["predicted_change"])
    _surge_cache["time"] = time.time()
    _surge_cache["data"] = candidates
    return candidates


def _calc_score(pred_change, today_chg, s_trend, summary_parts, news_keywords) -> int:
    """简化的评分，用于排序列"""
    score = 0
    if pred_change > 3:
        score += 3
    elif pred_change > 1:
        score += 2
    else:
        score += 1
    if today_chg > 2:
        score += 2
    elif today_chg > 0:
        score += 1
    if s_trend > 1:
        score += 2
    elif s_trend > 0.3:
        score += 1
    news_kw = news_keywords.get("keywords", []) if isinstance(news_keywords, dict) else news_keywords
    for sp in summary_parts:
        for kw in news_kw:
            if kw in sp:
                score += 1
                break
    return score


def get_prediction_details() -> dict:
    """获取上一交易日预测详情（逐只对比），包含模型成分和依据"""
    import time
    t0 = time.time()

    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()

        cur.execute("SELECT MAX(check_date) FROM prediction_accuracy")
        row = cur.fetchone()
        check_date = row[0] if row else None
        if not check_date:
            return {"error": "尚无校验数据"}

        cur.execute(
            """SELECT sp.stock_code, sd_act.stock_name,
                      sd_act.pre_close AS baseline_price,
                      sp.predicted_price, sp.actual_price,
                      sd_act.change_pct, sd_act.latest_price,
                      sp.predict_date
               FROM stock_predictions sp
               JOIN stock_daily sd_act
                   ON sp.stock_code = sd_act.stock_code
                   AND sd_act.trade_date = sp.target_date
               WHERE sp.target_date = %s AND sp.actual_price IS NOT NULL""",
            (check_date,),
        )
        rows = cur.fetchall()
        total_p = len(rows)

        # 统计预测日期分布
        pred_dates = set()
        details = []
        for r in rows:
            code, name, baseline_price, pred_price, act_price, change_pct, latest_price, predict_date = r
            if predict_date:
                pred_dates.add(str(predict_date))
            pred_price = float(pred_price) if pred_price else 0
            act_price = float(act_price) if act_price else 0
            change_pct = float(change_pct) if change_pct else 0
            baseline_price = float(baseline_price) if baseline_price else None
            latest_price = float(latest_price) if latest_price else None

            # 收盘价始终显示目标日的实际收盘
            last_price = round(latest_price, 2) if latest_price else None

            # 预估涨幅以预测时的收盘价（pre_close）为基准，避免因实际涨跌而扭曲
            if baseline_price and baseline_price > 0:
                pred_change_pct = round((pred_price - baseline_price) / baseline_price * 100, 2)
                predicted_up = pred_change_pct > 0
                change_error = round((pred_price - act_price) / act_price * 100, 2) if act_price else None
            else:
                pred_change_pct = None
                predicted_up = None
                change_error = None

            actual_up = change_pct > 0

            if predicted_up is not None:
                direction_correct = predicted_up == actual_up
                pred_dir = "看涨" if predicted_up else "看跌"
            else:
                direction_correct = None
                pred_dir = "未知"

            actual_dir = "上涨" if change_pct > 0 else "下跌" if change_pct < 0 else "平盘"

            abs_err = abs(change_error) if change_error is not None else None

            if abs_err is None:
                analysis = "缺少基准价，无法分析"
            elif not direction_correct:
                if actual_up:
                    analysis = f"方向错误：看跌实涨（预{pred_change_pct:+.1f}% 实{change_pct:+.1f}%）"
                else:
                    analysis = f"方向错误：看涨实跌（预{pred_change_pct:+.1f}% 实{change_pct:+.1f}%）"
            elif abs_err < 2:
                analysis = "偏差极小，预测精准"
            elif change_error > 0:
                analysis = f"方向正确，偏乐观（预{pred_change_pct:+.1f}% 实{change_pct:+.1f}% 差{abs_err:.1f}%）"
            else:
                analysis = f"方向正确，偏保守（预{pred_change_pct:+.1f}% 实{change_pct:+.1f}% 差{abs_err:.1f}%）"

            # 重新预测获取模型成分（poly / wma）和近期走势
            basis_parts = []
            poly_price = wma_price = None
            recent_trend = ""
            try:
                pred = predict_trend_v2(code, days=1, _skip_analysis=True)
                if "error" not in pred and pred.get("predictions"):
                    p0 = pred["predictions"][0]
                    poly_price = p0.get("poly_price")
                    wma_price = p0.get("wma_price")

                    if poly_price:
                        basis_parts.append(f"多项式{poly_price:.2f}")
                    if wma_price:
                        basis_parts.append(f"WMA{wma_price:.2f}")
                    if poly_price and wma_price:
                        ew = get_config()['model']
                        ensemble_calc = poly_price * ew['ensemble_poly_weight'] + wma_price * ew['ensemble_wma_weight']
                        basis_parts.append(f"综合{poly_price*ew['ensemble_poly_weight']:.2f}+{wma_price*ew['ensemble_wma_weight']:.2f}")

                    # 近期走势
                    hist = pred.get("history", [])
                    if hist and len(hist) >= 3:
                        recent = hist[-5:]
                        trend_chgs = []
                        for i in range(1, len(recent)):
                            if recent[i]["price"] and recent[i-1]["price"]:
                                trend_chgs.append((recent[i]["price"] - recent[i-1]["price"]) / recent[i-1]["price"] * 100)
                        if trend_chgs:
                            avg_t = sum(trend_chgs) / len(trend_chgs)
                            recent_trend = f"近5日日均{'涨' if avg_t > 0 else '跌'}{abs(avg_t):.2f}%"
                            basis_parts.append(recent_trend)

                        # 收盘价 vs MA5
                        prices_l = [h["price"] for h in hist if h["price"]]
                        if len(prices_l) >= 5:
                            ma5 = sum(prices_l[-5:]) / 5
                            pos = "上" if prices_l[-1] >= ma5 else "下"
                            basis_parts.append(f"股价在MA5{pos}方(MA5={ma5:.2f})")
            except Exception:
                pass

            analysis_basis = " | ".join(basis_parts) if basis_parts else ""

            details.append({
                "code": code,
                "name": name or "",
                "predict_date": str(predict_date),
                "last_price": round(last_price, 2) if last_price else None,
                "predicted_price": round(pred_price, 2),
                "actual_price": round(act_price, 2),
                "pred_change_pct": pred_change_pct,
                "change_pct": round(change_pct, 2),
                "change_error": change_error,
                "direction_correct": direction_correct,
                "pred_direction": pred_dir,
                "actual_direction": actual_dir,
                "analysis": analysis,
                "analysis_basis": analysis_basis,
            })

        details.sort(key=lambda x: (
            0 if x["direction_correct"] is False else 1,
            -(abs(x["change_error"] or 0)) if x["change_error"] is not None else 0,
        ))

        correct_p = sum(1 for d in details if d["direction_correct"] is True)
        accuracy = round(correct_p / total_p * 100, 2) if total_p else 0

        elapsed = time.time() - t0
        sorted_pred_dates = sorted(pred_dates)
        return {
            "check_date": str(check_date),
            "predict_dates": [str(d) for d in sorted_pred_dates],
            "predict_date_min": str(sorted_pred_dates[0]) if sorted_pred_dates else str(check_date),
            "total": total_p,
            "correct": correct_p,
            "accuracy_pct": accuracy,
            "elapsed_seconds": round(elapsed, 1),
            "details": details,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def predict_top10() -> dict:
    """全市场扫描，预测今日最可能大涨的 TOP10 股票，附带分析理由。"""
    _cfg = get_config()
    import time
    from collections import defaultdict
    from analysis import classify_sector

    t0 = time.time()
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest = str(cur.fetchone()[0])

        # 没有已保存的预测时直接返回空
        cur.execute("SELECT COUNT(*) FROM stock_predictions WHERE actual_price IS NULL")
        if cur.fetchone()[0] == 0:
            return {"trade_date": latest, "picks": [], "total_predicted": 0, "elapsed_seconds": 0}

        cur.execute(
            "SELECT stock_code, stock_name, latest_price, turnover, change_pct, "
            "       total_market_cap, volume, pe_ratio_dynamic "
            "FROM stock_daily WHERE trade_date = %s "
            "ORDER BY turnover DESC LIMIT %s", (latest, _cfg['filters']['scan_top10_n']))
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
    news_keywords = _get_cached_news()
    for code, name, price, tv, change_pct, mcap, volume, pe in candidates:
        try:
            pred = predict_trend_v2(code, days=1, _skip_analysis=True)
            if "error" in pred or not pred.get("predictions"):
                continue
            pred_change = pred.get("change_pct", 0)
            current_price = float(price) if price else 0
            hist = pred.get("history", [])
            sector = classify_sector(code)
            s_trend = sector_avg.get(sector, 0)
            today_chg = float(change_pct) if change_pct else 0
            pe_val = float(pe) if pe else 0

            # 统一分析逻辑（通过 _analyze_stock）
            prices_l = [h["price"] for h in hist if h["price"]]
            summary_parts, risk_parts = _analyze_stock(
                prices_l, [], today_chg, s_trend, pe_val,
                sector, name or "", news_keywords,
                pred_change_pct=pred_change,
            )
            reasons = list(summary_parts)
            if risk_parts:
                reasons.append("风险：" + "；".join(risk_parts))
            if not reasons:
                reasons.append("技术面指标指向上行")

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
    top = [r for r in results if r["trend"] == "up"][:_cfg['filters']['tomorrow_return_n']]
    return {
        "trade_date": latest,
        "total_analyzed": len(candidates),
        "total_predicted": len(results),
        "top10": top,
        "elapsed_seconds": round(time.time() - t0, 1),
    }


_news_cache = {"time": 0, "keywords": [], "titles": []}


def _get_cached_news(max_age: int = None):
    """缓存新闻关键词和原标题，返回 {"keywords": [...], "titles": [...]}"""
    _cfg = get_config()
    if max_age is None:
        max_age = _cfg['news']['cache_ttl']
    import time
    now = time.time()
    if now - _news_cache["time"] > max_age or not _news_cache["keywords"]:
        try:
            from news import fetch_all_news, extract_hot_topics
            news_list = fetch_all_news()
            topics = extract_hot_topics(news_list, top_n=_cfg['news']['hot_topics_n'])
            _news_cache["keywords"] = [t["word"] for t in topics if t["weight"] > _cfg['news']['min_topic_weight']]
            _news_cache["titles"] = [
                {"title": n["title"], "source": n["source"]}
                for n in news_list if len(n["title"]) > 8
            ]
            _news_cache["time"] = now
        except Exception:
            pass
    return {
        "keywords": _news_cache["keywords"],
        "titles": _news_cache["titles"],
    }


def _get_history_data(code: str, days: int = None):
    """快速获取个股历史收盘价列表（最近 N 天，按时间正序）"""
    _cfg_local = get_config()
    if days is None:
        days = _cfg_local['analysis']['history_days']
    from db import query_stock_history
    rows = query_stock_history(code, days)
    rows.reverse()
    prices = []
    volumes = []
    for r in rows:
        p = float(r["latest_price"]) if r["latest_price"] else None
        if p:
            prices.append(p)
            volumes.append(int(r["volume"]) if r["volume"] else 0)
    return prices, volumes


def get_tomorrow_picks(limit: int = None) -> dict:
    """
    基于已保存的预测，快速分析次日上涨潜力 TOP 股票，
    附带通俗技术面 + 板块 + 新闻佐证。
    """
    _cfg = get_config()
    if limit is None:
        limit = _cfg['filters']['tomorrow_scan_n']
    import time
    from datetime import timedelta
    from collections import defaultdict
    from analysis import classify_sector

    t0 = time.time()
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM stock_daily")
        latest = str(cur.fetchone()[0])

        # 估算下一交易日（使用交易日历跳过周末/节假日）
        from datetime import datetime as dt
        latest_dt = dt.strptime(latest, "%Y-%m-%d").date()
        next_day = next_trading_day(latest_dt)
        next_str = str(next_day)

        # 没有已保存的预测时直接返回空（前端显示"收集信息中"）
        cur.execute("SELECT COUNT(*) FROM stock_predictions WHERE target_date = %s AND actual_price IS NULL",
                    (next_str,))
        if cur.fetchone()[0] == 0:
            news_kw = _get_cached_news()
            return {
                "trade_date": latest,
                "next_trade_date": next_str,
                "total_analyzed": 0,
                "total_predicted": 0,
                "picks": [],
                "news_keywords": news_kw.get("keywords", [])[:_cfg['news']['display_keywords']],
                "elapsed_seconds": 0,
            }

        # 查询已保存的预测（按成交额排序取前 limit 只）
        # 用子查询取每个 stock_code 最新预测，避免重复
        cur.execute("""
            SELECT d.stock_code, d.stock_name, d.latest_price, d.turnover,
                   d.change_pct, d.total_market_cap, d.volume, d.pe_ratio_dynamic,
                   d.pre_close, COALESCE(sp.predicted_price, 0) AS pred_price
            FROM stock_daily d
            LEFT JOIN (
                SELECT sp1.stock_code, sp1.predicted_price
                FROM stock_predictions sp1
                INNER JOIN (
                    SELECT stock_code, MAX(predict_date) AS max_pd
                    FROM stock_predictions
                    WHERE target_date = %s
                    GROUP BY stock_code
                ) sp2 ON sp1.stock_code = sp2.stock_code
                    AND sp1.predict_date = sp2.max_pd
                    AND sp1.target_date = %s
            ) sp ON d.stock_code = sp.stock_code
            WHERE d.trade_date = %s
            ORDER BY d.turnover DESC
            LIMIT %s
        """, (next_str, next_str, latest, limit))
        rows = cur.fetchall()

        cur.execute("SELECT stock_code, change_pct FROM stock_daily WHERE trade_date = %s",
                    (latest,))
        all_data = {r[0]: float(r[1]) if r[1] else 0 for r in cur.fetchall()}
    finally:
        conn.close()

    # 板块热度
    sector_changes = defaultdict(list)
    for code, chg in all_data.items():
        sector_changes[classify_sector(code)].append(chg)
    sector_avg = {s: round(sum(v)/len(v), 2) for s, v in sector_changes.items()}

    # 新闻热词（缓存）
    news_keywords = _get_cached_news()

    results = []
    need_predict = []

    for r in rows:
        code, name, price, tv, change_pct, mcap, volume, pe, pre_close, pred_price = r
        current_price = float(price) if price else 0
        pre_close_val = float(pre_close) if pre_close else None
        pe_val = float(pe) if pe else 0
        today_chg = float(change_pct) if change_pct else 0
        pred_price_val = float(pred_price) if pred_price else 0

        if pred_price_val <= 0:
            # 无保存预测，后续统一批量补算
            need_predict.append((code, name, current_price, tv, today_chg, pre_close_val, pe_val))
            continue

        sector = classify_sector(code)
        s_trend = sector_avg.get(sector, 0)
        prices_l, volumes_l = _get_history_data(code)
        summary_parts, risk_parts = _analyze_stock(
            prices_l, volumes_l, today_chg, s_trend, pe_val, sector, name or "", news_keywords
        )

        _append_result(results, code, name or "", sector, current_price, pre_close_val,
                       today_chg, pred_price_val, s_trend, pe_val, summary_parts, risk_parts)

    # 对没有保存预测的股票，批量预测（只预测第一只）
    if need_predict and results:
        # 已经有不少结果了，跳过补算避免慢
        pass
    elif need_predict:
        # 一条预测都没有时才现场算几条
        for item in need_predict[:_cfg['filters']['batch_predict_fallback']]:
            try:
                code = item[0]
                pred = predict_trend_v2(code, days=1, _skip_analysis=True)
                if "error" in pred or not pred.get("predictions"):
                    continue
                pred_price_val = pred["predictions"][0]["predicted_price"]
                sector = classify_sector(code)
                s_trend = sector_avg.get(sector, 0)
                prices_l, volumes_l = _get_history_data(code)
                summary_parts, risk_parts = _analyze_stock(
                    prices_l, volumes_l, item[4], s_trend, item[6], sector, item[1] or "", news_keywords
                )
                _append_result(results, code, item[1] or "", sector, item[2], item[5],
                               item[4], pred_price_val, s_trend, item[6], summary_parts, risk_parts)
            except Exception:
                continue

    # 排序：看涨优先，同方向按综合评分降序
    results.sort(key=lambda x: (
        0 if x["trend"] == "up" else 1,
        -x["score"],
        -abs(x["predicted_change"] or 0),
    ))

    top_picks = results[:_cfg['filters']['tomorrow_return_n']]
    for pick in top_picks:
        s = pick["score"]
        if s >= _cfg['confidence']['high_min']:
            pick["confidence"] = "高"
        elif s >= _cfg['confidence']['medium_min']:
            pick["confidence"] = "中"
        else:
            pick["confidence"] = "低"

    elapsed = time.time() - t0
    return {
        "trade_date": latest,
        "next_trade_date": next_str,
        "total_analyzed": len(rows),
        "total_predicted": len(results),
        "picks": top_picks,
        "news_keywords": news_keywords.get("keywords", [])[:_cfg['news']['display_keywords']],
        "elapsed_seconds": round(elapsed, 1),
    }


def _analyze_stock(prices_l, volumes_l, today_chg, s_trend, pe_val, sector, name, news_data, pred_change_pct=None):
    """
    生成通俗的涨跌原因分析和风险提示（统一分析入口）。
    news_data: {"keywords": [...], "titles": [{"title": "...", "source": "..."}]}
    所有页面的分析逻辑均由此函数生成，保证一致。
    """
    _cfg = get_config()
    an = _cfg['analysis']
    summary_parts = []
    risk_parts = []

    # 兼容旧格式（直接传关键词列表）
    if isinstance(news_data, list):
        news_keywords = news_data
        news_titles = []
    else:
        news_keywords = news_data.get("keywords", [])
        news_titles = news_data.get("titles", [])

    if len(prices_l) >= an['ma_periods']['ma20']:
        ma5 = sum(prices_l[-an['ma_periods']['ma5']:]) / an['ma_periods']['ma5']
        ma10 = sum(prices_l[-an['ma_periods']['ma10']:]) / an['ma_periods']['ma10']
        ma20 = sum(prices_l[-an['ma_periods']['ma20']:]) / an['ma_periods']['ma20']
        last_p = prices_l[-1]

        if last_p > ma5 > ma10 > ma20:
            summary_parts.append(f"均线多头排列，股价站稳5日/10日均线，上升趋势良好")
        elif last_p > ma5:
            summary_parts.append(f"股价在5日均线({ma5:.2f})上方，短线偏强")
        elif last_p > ma10:
            summary_parts.append(f"股价在10日均线附近，处于震荡区间")
        else:
            risk_parts.append(f"股价跌破10日均线({ma10:.2f})，短期走势偏弱")

        chg_5d = (prices_l[-1] - prices_l[-5]) / prices_l[-5] * 100 if prices_l[-5] else 0
        if chg_5d > an['trend_strong']:
            summary_parts.append(f"最近5天大涨{chg_5d:.1f}%，走势强劲")
        elif chg_5d > an['trend_good']:
            summary_parts.append(f"最近5天涨了{chg_5d:.1f}%，表现不错")
        elif chg_5d > an['trend_steady']:
            summary_parts.append(f"最近5天稳步上涨{chg_5d:.1f}%")
        elif chg_5d < an['trend_risk_drop']:
            risk_parts.append(f"最近5天跌了{chg_5d:.1f}%，注意回调风险")

        if len(volumes_l) >= an['volume_periods']['vol10'] and volumes_l:
            avg_v10 = sum(volumes_l[-an['volume_periods']['vol10']:]) / an['volume_periods']['vol10'] if any(volumes_l[-an['volume_periods']['vol10']:]) else 1
            avg_v5 = sum(volumes_l[-an['volume_periods']['vol5']:]) / an['volume_periods']['vol5'] if any(volumes_l[-an['volume_periods']['vol5']:]) else 1
            if avg_v5 > avg_v10 * an['volume_surge_ratio']:
                summary_parts.append("最近几天成交量放大，资金关注度提高")

    elif len(prices_l) >= an['ma_periods']['ma5']:
        ma5 = sum(prices_l[-an['ma_periods']['ma5']:]) / an['ma_periods']['ma5']
        if prices_l[-1] >= ma5:
            summary_parts.append(f"股价站在5日均线({ma5:.2f})上方，短线偏强")
        else:
            risk_parts.append(f"股价在5日均线({ma5:.2f})下方，短线偏弱")

        chg_5d = (prices_l[-1] - prices_l[-5]) / prices_l[-5] * 100 if prices_l[-5] else 0
        if chg_5d > 5:
            summary_parts.append(f"近5天涨了{chg_5d:.1f}%")

    # 今日表现
    if today_chg > an['today_surge']:
        summary_parts.append(f"今天大涨{today_chg:.1f}%，市场情绪高涨")
    elif today_chg > an['today_strong']:
        summary_parts.append(f"今天涨了{today_chg:.1f}%，表现强势")
    elif today_chg > an['today_rise']:
        summary_parts.append(f"今天小幅上涨{today_chg:.1f}%")
    elif today_chg < an['today_weak']:
        risk_parts.append(f"今天跌了{today_chg:.1f}%，走势较弱")

    # 板块
    if s_trend > an['sector_active']:
        summary_parts.append(f"所属{sector}整体活跃(涨{s_trend:.1f}%)，板块效应积极")
    elif s_trend > an['sector_rise']:
        summary_parts.append(f"所属{sector}小幅上涨(涨{s_trend:.1f}%)")
    elif s_trend < an['sector_weak']:
        risk_parts.append(f"所属{sector}整体偏弱(跌{abs(s_trend):.1f}%)")

    # 估值
    if an['pe_low_min'] < pe_val < an['pe_low_max']:
        summary_parts.append(f"市盈率{pe_val:.1f}倍，估值偏低有修复空间")
    elif an['pe_medium_min'] <= pe_val <= an['pe_medium_max']:
        summary_parts.append(f"市盈率{pe_val:.1f}倍，估值合理")
    elif pe_val > an['pe_high_min']:
        risk_parts.append(f"市盈率{pe_val:.1f}倍，估值偏高需谨慎")

    # 新闻匹配 — 关键词 + 具体新闻内容
    import re
    clean_name = re.sub(r'^(XD|XR|DR)', '', name).strip()
    name_lower = name.lower()
    clean_name_lower = clean_name.lower()
    matched_kw = []
    for kw in news_keywords:
        if kw.lower() in name_lower or kw.lower() in clean_name_lower or kw.lower() in sector:
            matched_kw.append(kw)
    if matched_kw:
        summary_parts.append(f"新闻热词\"{'、'.join(matched_kw[:_cfg['news']['matched_keywords']])}\"关联该股")

    # 匹配具体新闻标题（取与板块/行业/股票名称相关的）
    sector_keywords = [s.lower() for s in sector.replace('/', ' ').split()] + [clean_name_lower]
    # 从 stock_mapping 中查找股票归属的行业，加入匹配关键词
    for ind_name, stocks_str in _cfg.get('stock_mapping', {}).items():
        names_in_mapping = [s.split(',')[1] for s in stocks_str.split(';') if ',' in s]
        if any(clean_name in n for n in names_in_mapping):
            sector_keywords.append(ind_name.lower())
            break
    matched_news = []
    for news_item in news_titles:
        title_lower = news_item["title"].lower()
        for sk in sector_keywords:
            if sk and len(sk) > 1 and sk in title_lower:
                matched_news.append(news_item)
                break
    if matched_news:
        # 去重后取前2条
        seen_titles = set()
        unique_news = []
        for n in matched_news:
            short = n["title"][:30]
            if short not in seen_titles:
                seen_titles.add(short)
                unique_news.append(n)
        for n in unique_news[:_cfg['news']['matched_keywords']]:
            summary_parts.append(f"新闻\"{n['title']}\"（{n['source']}）")

    # 模型预测摘要（从 predict_trend_v2 / predict_top10 统一过来）
    if pred_change_pct is not None and abs(pred_change_pct) > 1:
        direction = '上涨' if pred_change_pct > 0 else '下跌'
        summary_parts.append(f"模型预测{direction}{abs(pred_change_pct):.1f}%")

    # 短期动量分析（从 predict_top10 统一过来）
    if len(prices_l) >= 3:
        rsn_cfg = _cfg.get('reason', {})
        lookback = min(rsn_cfg.get('momentum_lookback', 3), len(prices_l) - 1)
        chgs = []
        for i in range(1, lookback + 1):
            prev = prices_l[-i - 1] if len(prices_l) > i else prices_l[0]
            cur = prices_l[-i]
            if prev:
                chgs.append((cur - prev) / prev * 100)
        if chgs:
            avg_mom = sum(chgs) / len(chgs)
            if avg_mom > rsn_cfg.get('momentum_strong', 2):
                summary_parts.append(f"近3日走势强劲(+{avg_mom:.1f}%)")
            elif avg_mom > rsn_cfg.get('momentum_mild', 0.5):
                summary_parts.append(f"近3日温和上行(+{avg_mom:.1f}%)")
            elif avg_mom > rsn_cfg.get('momentum_range', -0.5):
                summary_parts.append(f"近3日窄幅震荡({avg_mom:.1f}%)")

    return summary_parts, risk_parts


def _append_result(results, code, name, sector, price, pre_close, today_chg,
                   pred_price, s_trend, pe_val, summary_parts, risk_parts):
    """计算评分并追加到结果列表"""
    _cfg = get_config()
    sc = _cfg['scoring']
    pred_chg_pct = 0
    if price and price > 0 and pred_price:
        pred_chg_pct = (pred_price - price) / price * 100

    trend = "up" if pred_chg_pct > 0 else "down"

    score = 0
    if pred_chg_pct > sc['pred_change_high']: score += sc['score_pred_high']
    elif pred_chg_pct > sc['pred_change_mid']: score += sc['score_pred_mid']
    elif pred_chg_pct > sc['pred_change_low']: score += sc['score_pred_low']
    if today_chg > sc['today_chg_high']: score += sc['score_today_high']
    elif today_chg > sc['today_chg_low']: score += sc['score_today_low']
    if s_trend > sc['sector_trend_high']: score += sc['score_sector_high']
    elif s_trend > sc['sector_trend_low']: score += sc['score_sector_low']
    if len([p for p in summary_parts if "新闻" in p]) > 0: score += sc['score_news_match']

    results.append({
        "code": code,
        "name": name,
        "sector": sector,
        "price": round(price, 2),
        "pre_close": round(pre_close, 2) if pre_close else None,
        "change_pct": round(today_chg, 2),
        "predicted_price": round(pred_price, 2) if pred_price else None,
        "predicted_change": round(pred_chg_pct, 2),
        "trend": trend,
        "score": score,
        "summary": "；".join(summary_parts) if summary_parts else "技术面指标指向积极",
        "risk_warning": "；".join(risk_parts) if risk_parts else None,
    })
