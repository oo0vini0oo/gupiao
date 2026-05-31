#!/usr/bin/env python3
"""
A股股票数据抓取与入库 - 主入口
拆分模块化后，只需导入各模块的函数即可完成所有操作。

使用方式：
    python main.py          # 全量拉取 + 显示市值排行
    python analyzer.py       # 进入交互式查询
    pip install pymysql requests   # 首次安装依赖
"""

import time
from datetime import date

from config import DATABASE
from db import init_tables, count_total_records
from fetcher import fetch_all_stocks
from manager import save_daily_data, show_summary
from analyzer import query_history_by_code, query_top_market_cap
from predictor import verify_yesterday_predictions, get_accuracy_report, run_daily_predictions
from trading_calendar import is_trading_day


def main(mode="all"):
    """
    模式:
      all    — 拉取数据 + 验证 + 预测（默认）
      fetch  — 拉取数据 + 验证（收盘后执行）
      predict — 新闻 + 预测（晚8点执行）
    """
    print("=" * 60)
    print("  A股股票数据抓取工具 (模块化)")
    print(f"  {date.today()}  模式: {mode}")
    print("=" * 60)

    t0 = time.time()

    if mode in ("all", "fetch"):
        # 1. 初始化数据库 & 表
        init_tables()

        # 2. 拉取数据（非交易日跳过）
        if is_trading_day(date.today()):
            records = fetch_all_stocks()
            if not records:
                print("没有获取到任何数据。")
            else:
                trade_date = date.today().isoformat()
                save_daily_data(records, trade_date)
                show_summary(len(records))

                total_records = count_total_records()
                stocks_count = 0
                try:
                    from db import get_stock_count
                    stocks_count = get_stock_count()
                except Exception:
                    pass
                print(f"\n[INFO] 今日共覆盖 {stocks_count} 只股票，"
                      f"累计 {total_records:,} 条历史记录")
                query_top_market_cap(10)
        else:
            print(f"  {date.today()} 非交易日，跳过数据获取")

        # ── 验证上一交易日预测 ──
        print("\n" + "=" * 60)
        print("  [验证] 预测验证")
        verify_yesterday_predictions()
        report = get_accuracy_report()
        if report.get("daily"):
            print(f"  累计监控 {len(report['daily'])} 天")
            print(f"  累计准确率: {report['avg_accuracy']}%")

    if mode in ("all", "predict"):
        # ── 获取新闻 + 预测 ──
        from predictor import run_evening_predict
        print("\n" + "=" * 60)
        print("  [预测] 获取新闻联播后自动预测")
        pred_result = run_evening_predict()
        print(f"  看涨: {pred_result['up']} 只 | 看跌: {pred_result['down']} 只")

    elapsed = time.time() - t0
    print(f"\n完成! 耗时 {elapsed:.0f}s")


# ── 快速查询命令（命令行传参调用）────────────────────
if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    if "history" in args:
        code = args[args.index("history") + 1] if len(args) > args.index("history") else "600519"
        limit = int(args[args.index("history") + 2]) if len(args) > args.index("history") + 1 and args[-1].isdigit() else 100
        query_history_by_code(code, limit)

    elif "summary" in args:
        from analyzer import show_days_count
        show_days_count()

    elif "fetch" in args and "predict" not in args:
        main(mode="fetch")
    elif "predict" in args and "fetch" not in args:
        main(mode="predict")
    else:
        main()
