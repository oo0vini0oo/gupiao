"""
股票分析 Web 应用 - Flask 主入口
"""

from flask import Flask, render_template, jsonify, request
from db import _get_connection
from analysis import (
    get_market_overview, get_top_stocks, get_sector_summary,
    get_stock_history,
)
from predictor import predict_trend_v2, save_predictions, get_accuracy_report, predict_top10, run_daily_predictions, get_prediction_details, get_all_prediction_records, get_tomorrow_picks, _get_cached_news, run_evening_predict
from trading_calendar import next_trading_day as _next_td
from prediction_config import get_config, save_config, reload_config
from news import get_news_analysis
from fetcher import fetch_all_stocks
from manager import save_daily_data, show_summary

app = Flask(__name__)

# ── 预测调度状态（供前端展示"收集信息中"）───────────
_next_pred_info = {
    "next_pred_time": None,
    "next_trading_day": None,
    "status": "idle",       # idle / scheduled / running
}


# ── 页面路由 ──────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/industry")
def industry():
    return render_template("industry.html")


@app.route("/stock/<code>")
def stock_detail(code):
    return render_template("stock.html", code=code)


@app.route("/news")
def news():
    return render_template("news.html")


@app.route("/check")
def prediction_check():
    return render_template("prediction_check.html")


@app.route("/predictions")
def predictions():
    return render_template("predictions.html")


# ── API 路由 ──────────────────────────────────────

@app.route("/api/overview")
def api_overview():
    try:
        data = get_market_overview()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/top/<field>/<int:n>")
def api_top(field, n):
    try:
        data = get_top_stocks(field, n)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/top_losers/<int:n>")
def api_top_losers(n):
    try:
        data = get_top_stocks("change_pct", n, asc=True)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/sectors")
def api_sectors():
    try:
        data = get_sector_summary()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/stock/<code>/history")
def api_stock_history(code):
    try:
        data = get_stock_history(code)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/stock/<code>/predict")
def api_stock_predict(code):
    """从已保存的预测中获取下一交易日数据（与首页/记录页同一套源数据）"""
    try:
        from db import _get_connection
        from datetime import date, datetime
        from trading_calendar import next_trading_day
        from analysis import get_stock_history, classify_sector
        from predictor import _analyze_stock, _get_cached_news

        today = date.today()
        ntd = next_trading_day(today)
        ntd_str = str(ntd)

        conn = _get_connection(with_db=True)
        try:
            cur = conn.cursor()
            # 查已保存的预测
            cur.execute(
                "SELECT sp.predicted_price "
                "FROM stock_predictions sp "
                "WHERE sp.stock_code = %s AND sp.target_date = %s "
                "ORDER BY sp.predict_date DESC LIMIT 1",
                (code, ntd_str),
            )
            row = cur.fetchone()
        finally:
            conn.close()

        if row:
            pred_price = float(row[0]) if row[0] else 0
            # 拿最新行情数据
            history = get_stock_history(code, 60)
            prices = [r["price"] for r in history if r["price"] is not None]
            if not prices:
                return jsonify({"success": True, "data": {"error": "暂无行情数据", "predictions": []}})

            last_price = prices[-1]
            change_pct = round((pred_price - last_price) / last_price * 100, 2) if last_price else 0
            trend = "up" if change_pct > 0 else "down"

            # 生成分析理由
            volumes = [r["volume"] for r in history if r.get("volume") is not None]
            today_chg = history[-1]["change_pct"] if history and history[-1].get("change_pct") is not None else 0

            # 板块热度
            try:
                from analysis import get_sector_summary
                sector = classify_sector(code)
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
                conn2 = _get_connection(with_db=True)
                try:
                    cur2 = conn2.cursor()
                    cur2.execute(
                        "SELECT pe_ratio_dynamic FROM stock_daily "
                        "WHERE stock_code = %s ORDER BY trade_date DESC LIMIT 1",
                        (code,),
                    )
                    pe_row = cur2.fetchone()
                    if pe_row and pe_row[0]:
                        pe_val = float(pe_row[0])
                finally:
                    conn2.close()
            except Exception:
                pass

            news_keywords = _get_cached_news()
            name = history[-1].get("name", "") if history else ""

            summary_parts, risk_parts = _analyze_stock(
                prices, volumes, today_chg, s_trend, pe_val,
                sector, name, news_keywords,
                pred_change_pct=change_pct,
            )
            all_parts = list(summary_parts)
            if risk_parts:
                all_parts.append("风险提示：" + "；".join(risk_parts))
            reason = "；".join(all_parts) if all_parts else "数据有限，参考模型预测"

            return jsonify({"success": True, "data": {
                "last_price": last_price,
                "trend": trend,
                "change_pct": change_pct,
                "reason": reason,
                "model_version": "v2",
                "predictions": [{
                    "date": ntd_str,
                    "predicted_price": round(pred_price, 3),
                }],
                "history": [{"date": r["date"], "price": r["price"]} for r in history],
            }})

        # 没有已保存预测 → 现场预测
        try:
            pred = predict_trend_v2(code, days=1)
            if "error" not in pred and pred.get("predictions"):
                # 保存以备后续复用
                from predictor import save_predictions
                save_predictions(code, pred)
                return jsonify({"success": True, "data": {
                    "last_price": pred["last_price"],
                    "trend": pred["trend"],
                    "change_pct": pred["change_pct"],
                    "reason": "实时预测（当日轮扫描未覆盖此股）",
                    "model_version": "v2",
                    "predictions": pred["predictions"],
                    "history": pred.get("history", []),
                }})
        except Exception:
            pass
        return jsonify({"success": True, "data": {"error": "暂无预测数据", "predictions": []}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/predict/accuracy")
def api_predict_accuracy():
    try:
        data = get_accuracy_report()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/predict/check")
def api_predict_check():
    try:
        data = get_prediction_details()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/predict/records")
def api_predict_records():
    """预测校验历史记录（按日期分组）"""
    try:
        date = __import__("flask").request.args.get("date", None)
        data = get_all_prediction_records(date)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/predict/top10")
def api_predict_top10():
    try:
        data = predict_top10()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/predict/tomorrow")
def api_predict_tomorrow():
    """明日看涨股票（附带详细分析理由 + 新闻佐证）"""
    try:
        data = get_tomorrow_picks()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/predict/surge")
def api_predict_surge():
    """全量扫描找高涨幅候选（涨幅>10%目标）"""
    try:
        from predictor import scan_surge_candidates
        candidates = scan_surge_candidates(scan_n=300, min_change=3.0)
        # 按涨幅降序返回
        return jsonify({"success": True, "data": {
            "total_scanned": 300,
            "candidates": candidates[:30],
            "total_found": len(candidates),
        }})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/news")
def api_news():
    try:
        data = get_news_analysis()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/news/keywords")
def api_news_keywords():
    """热点关键词（缓存）"""
    try:
        keywords = _get_cached_news()
        return jsonify({"success": True, "data": keywords.get("keywords", [])})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/stock/search")
def api_stock_search():
    """搜索股票（按代码或名称模糊匹配）"""
    q = __import__("flask").request.args.get("q", "")
    if len(q) < 1:
        return jsonify({"success": True, "data": []})
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT stock_code, stock_name FROM stock_daily "
            "WHERE stock_code LIKE %s OR stock_name LIKE %s "
            "LIMIT 20",
            (f"{q}%", f"%{q}%"),
        )
        rows = cur.fetchall()
        return jsonify({
            "success": True,
            "data": [{"code": r[0], "name": r[1]} for r in rows],
        })
    finally:
        conn.close()


@app.route("/api/predict/next")
def api_predict_next():
    """下一次预测时间（供前端展示收集信息中）"""
    return jsonify({"success": True, "data": dict(_next_pred_info)})


@app.route("/api/predict/by_input", methods=["POST"])
def api_predict_by_input():
    """根据用户输入（股票代码/名称/热词）预测涨跌"""
    query = request.get_json(force=True).get("query", "").strip()
    if not query:
        return jsonify({"success": False, "error": "请输入股票代码、名称或热词"})

    # 1. 尝试精确匹配股票（代码精确匹配或名称精确匹配才走单股票预测）
    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT stock_code, stock_name FROM stock_daily "
            "WHERE stock_code = %s OR stock_name = %s LIMIT 1",
            (query, query),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    if row:
        # ── 股票匹配成功 → 预测涨跌 ──
        code, name = row
        result = predict_trend_v2(code, days=1)
        if "error" in result:
            return jsonify({"success": False, "error": result["error"]})

        p = result["predictions"][0] if result.get("predictions") else None
        if not p:
            return jsonify({"success": False, "error": "预测失败，无结果"})

        from analysis import classify_sector
        sector = classify_sector(code)

        return jsonify({"success": True, "data": {
            "type": "stock",
            "code": code,
            "name": name,
            "sector": sector,
            "prediction": {
                "price": result["last_price"],
                "predicted_change": result["change_pct"],
                "target_price": p["predicted_price"],
                "ensemble_price": p["predicted_price"],
                "poly_price": p["poly_price"],
                "wma_price": p["wma_price"],
                "trend": result["trend"],
                "recent_trend": "",
                "reason": "",
                "trade_date": "",
                "target_date": p["date"],
            },
            "history": result.get("history", []),
        }})

    # ── 未精确匹配股票 → 查行业分类映射或按名称搜关联股票 ──
    from prediction_config import get_config
    cfg = get_config()
    stock_mapping = cfg.get("stock_mapping", {})

    matched_codes = set()
    stocks = []

    conn = _get_connection(with_db=True)
    try:
        cur = conn.cursor()
        latest_td = None

        # 1. 从行业分类映射中取股票（只保留数据库里有数据的）
        if query in stock_mapping:
            mapping_codes = []
            mapping_entries = []
            for entry in stock_mapping[query].split(";"):
                parts = entry.strip().split(",")
                if len(parts) >= 2:
                    mapping_codes.append(parts[0].strip())
                    mapping_entries.append((parts[0].strip(), parts[1].strip()))

            if mapping_codes:
                placeholders = ",".join(["%s"] * len(mapping_codes))
                cur.execute(
                    f"SELECT DISTINCT stock_code, latest_price, change_pct FROM stock_daily "
                    f"WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily) "
                    f"AND stock_code IN ({placeholders})",
                    mapping_codes,
                )
                db_data = {r[0]: (float(r[1]) if r[1] else 0, float(r[2]) if r[2] else 0) for r in cur.fetchall()}
                for code, name in mapping_entries:
                    if code in db_data:
                        matched_codes.add(code)
                        price, chg = db_data[code]
                        stocks.append({"code": code, "name": name, "price": price, "change_pct": chg})

        # 2. 从数据库按名称模糊搜索（排除已在映射中的）
        cur.execute(
            "SELECT DISTINCT stock_code, stock_name, latest_price, change_pct, turnover "
            "FROM stock_daily WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily) "
            "AND stock_name LIKE %s "
            "ORDER BY turnover DESC LIMIT 10",
            (f"%{query}%",),
        )
        for r in cur.fetchall():
            code = r[0]
            if code not in matched_codes:
                matched_codes.add(code)
                stocks.append({
                    "code": code, "name": r[1],
                    "price": float(r[2]) if r[2] else 0,
                    "change_pct": float(r[3]) if r[3] else 0,
                })
    finally:
        conn.close()

    # 3. 对关联股票做预测分析（最多5只）
    from predictor import predict_trend_v2 as _predict_stock
    stocks = stocks[:5]
    for s in stocks:
        try:
            if not s.get("price"):
                s["price"] = 0
            pred = _predict_stock(s["code"], days=1, _skip_analysis=True)
            if "error" not in pred and pred.get("predictions"):
                p = pred["predictions"][0]
                s["predicted_change"] = round(pred.get("change_pct", 0), 2)
                s["predicted_price"] = round(p["predicted_price"], 2)
                s["trend"] = pred.get("trend", "unknown")
        except Exception:
            pass

    return jsonify({"success": True, "data": {
        "type": "keyword",
        "keyword": query,
        "stocks": stocks,
    }})


# ── 配置页面路由 ─────────────────────────────────

@app.route("/settings")
def settings_page():
    return render_template("settings.html")


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    try:
        return jsonify({"success": True, "data": get_config()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    try:
        updates = request.get_json(force=True)
        ok, msg = save_config(updates)
        if ok:
            reload_config()
            action = updates.pop("_action", None)
            if action == "reanalyze":
                from predictor import run_daily_predictions
                result = run_daily_predictions()
            return jsonify({"success": True, "message": msg})
        else:
            return jsonify({"success": False, "error": msg})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ── 虚拟投资组合路由 ─────────────────────────────

@app.route("/portfolio")
def portfolio():
    return render_template("portfolio.html")


@app.route("/api/portfolio")
def api_portfolio():
    try:
        from virtual import get_portfolio
        data = get_portfolio()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/portfolio/buy", methods=["POST"])
def api_portfolio_buy():
    try:
        from virtual import buy_stock
        body = request.get_json(force=True)
        code = body.get("code", "").strip()
        quantity = int(body.get("quantity", 0))
        if not code or quantity <= 0:
            return jsonify({"success": False, "error": "参数错误"})
        result = buy_stock(code, quantity)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/portfolio/sell", methods=["POST"])
def api_portfolio_sell():
    try:
        from virtual import sell_stock
        body = request.get_json(force=True)
        code = body.get("code", "").strip()
        quantity = body.get("quantity")
        if not code:
            return jsonify({"success": False, "error": "参数错误"})
        result = sell_stock(code, quantity)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/portfolio/transactions")
def api_portfolio_transactions():
    try:
        from virtual import get_transactions
        data = get_transactions()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/portfolio/pnl")
def api_portfolio_pnl():
    try:
        from virtual import get_daily_pnl, settle_daily
        data = get_daily_pnl()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/portfolio/settle", methods=["POST"])
def api_portfolio_settle():
    try:
        from virtual import settle_daily
        result = settle_daily()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


def _schedule_prediction():
    """动态调度预测：下个交易日的前一天 20:00 执行。
    若错过预定时间（设备未开机），启动后立即执行并扩大新闻收集范围。"""
    import threading
    from datetime import date, datetime, timedelta
    import pymysql
    from config import MYSQL_CONFIG, DATABASE

    today = date.today()
    ntd = _next_td(today)
    pred_day = ntd - timedelta(days=1)
    pred_time = datetime(pred_day.year, pred_day.month, pred_day.day, 20, 0, 0)
    now = datetime.now()

    is_catch_up = False
    if now >= pred_time:
        # 错过预定时间，检查是否需要补执行
        conn = pymysql.connect(**MYSQL_CONFIG, database=DATABASE)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM stock_predictions WHERE target_date = %s AND actual_price IS NULL",
                (ntd.isoformat(),),
            )
            has_preds = cur.fetchone()[0] > 0
        finally:
            conn.close()

        if not has_preds:
            is_catch_up = True
            delay_sec = 30
            print(f"  [预测] 错过预定时间 {pred_time.strftime('%m-%d %H:%M')}，30秒后补执行", flush=True)
        else:
            ntd = _next_td(ntd)
            pred_day = ntd - timedelta(days=1)
            pred_time = datetime(pred_day.year, pred_day.month, pred_day.day, 20, 0, 0)
            delay_sec = (pred_time - now).total_seconds()
            print(f"  [预测] 已有预测数据，跳到下个周期", flush=True)
    else:
        delay_sec = (pred_time - now).total_seconds()

    # 更新全局状态供前端展示
    _next_pred_info["next_pred_time"] = pred_time.isoformat()
    _next_pred_info["next_trading_day"] = ntd.isoformat()
    _next_pred_info["status"] = "running" if is_catch_up else "scheduled"

    if is_catch_up:
        print(f"  [预测] 下一交易日: {ntd.isoformat()}", flush=True)
        print(f"  [预测] 补采模式：扩大新闻收集范围", flush=True)
    else:
        print(f"  [预测] 下一交易日: {ntd.isoformat()}", flush=True)
        print(f"  [预测] 已安排在 {pred_time.strftime('%m-%d %H:%M')} 执行预测", flush=True)
        print(f"        等待 {int(delay_sec // 3600)} 小时 {int(delay_sec % 3600 // 60)} 分钟", flush=True)

    def _do_predict():
        _next_pred_info["status"] = "running"
        try:
            run_evening_predict(catch_up=is_catch_up)
        finally:
            _next_pred_info["status"] = "idle"
            _schedule_prediction()

    timer = threading.Timer(delay_sec, _do_predict)
    timer.daemon = True
    timer.start()


def _startup_pipeline():
    """启动时自动执行：拉取数据 → 验证预测 → 动态调度下一预测"""
    import time
    import sys
    from datetime import date
    from db import init_tables, count_total_records
    from analyzer import query_top_market_cap
    from predictor import verify_yesterday_predictions, get_accuracy_report
    from trading_calendar import is_trading_day

    today = date.today()

    print("\n" + "=" * 50, flush=True)
    print("  [启动管道] 自动获取数据与分析", flush=True)
    print(f"  {today}", flush=True)
    print("=" * 50, flush=True)
    t0 = time.time()

    # 1. 初始化数据库
    init_tables()

    # 2. 拉取数据（非交易日跳过）
    if is_trading_day(today):
        records = fetch_all_stocks()
        if records:
            trade_date = today.isoformat()
            save_daily_data(records, trade_date)
            show_summary(len(records))
            query_top_market_cap(10)
        else:
            print("  未获取到新数据，跳过入库", flush=True)
    else:
        print(f"  {today} 非交易日，跳过数据获取", flush=True)

    # 3. 验证昨日预测
    print("\n  [验证] 预测验证", flush=True)
    verify_result = verify_yesterday_predictions()
    if "accuracy_pct" in verify_result:
        pd_ = verify_result.get('predict_date_min', '?')
        cd_ = verify_result['check_date']
        print(f"  时间: 分析日 {pd_} → 目标日 {cd_}", flush=True)
        print(f"  准确率: {verify_result['accuracy_pct']}%", flush=True)
        # 显示详细分析
        analysis = verify_result.get("analysis")
        if analysis and analysis.get("sector_breakdown"):
            print(f"  板块分析:", flush=True)
            for sec, info in sorted(analysis["sector_breakdown"].items()):
                print(f"    {sec}: {info['accuracy']}% ({info['correct']}/{info['total']})", flush=True)
            if analysis.get("suggestions"):
                for s in analysis["suggestions"]:
                    print(f"  [建议] {s}", flush=True)
    else:
        print(f"  {verify_result.get('message', verify_result.get('error', '无数据'))}", flush=True)

    report = get_accuracy_report()
    if report.get("daily"):
        print(f"  累计监控 {len(report['daily'])} 天, 累计准确率: {report['avg_accuracy']}%", flush=True)

    # 4. 动态调度预测（下个交易日的前一天 20:00）
    _schedule_prediction()

    elapsed = time.time() - t0
    print(f"\n  管道完成, 耗时 {elapsed:.0f}s", flush=True)
    print("=" * 50 + "\n", flush=True)


if __name__ == "__main__":
    print("=" * 50)
    print("  A股股票分析平台")
    print(f"  启动: http://127.0.0.1:5000")
    print("=" * 50)

    import threading
    t = threading.Thread(target=_startup_pipeline, daemon=True)
    t.start()

    app.run(debug=True, use_reloader=False)
