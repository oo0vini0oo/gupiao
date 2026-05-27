"""
股票分析 Web 应用 - Flask 主入口
"""

from flask import Flask, render_template, jsonify
from db import _get_connection
from analysis import (
    get_market_overview, get_top_stocks, get_sector_summary,
    get_stock_history,
)
from predictor import predict_trend_v2, save_predictions, get_accuracy_report, predict_top10, run_daily_predictions, get_prediction_details
from news import get_news_analysis

app = Flask(__name__)


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
    try:
        data = predict_trend_v2(code)
        if "predictions" in data and data.get("predictions"):
            save_predictions(code, data)
        return jsonify({"success": True, "data": data})
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


@app.route("/api/predict/top10")
def api_predict_top10():
    try:
        data = predict_top10()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/news")
def api_news():
    try:
        data = get_news_analysis()
        return jsonify({"success": True, "data": data})
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


if __name__ == "__main__":
    print("=" * 50)
    print("  A股股票分析平台")
    print(f"  启动: http://127.0.0.1:5000")
    print("=" * 50)
    app.run(debug=True)
