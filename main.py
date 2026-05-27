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


def main():
    print("=" * 60)
    print("  A股股票数据抓取工具 (模块化)")
    print(f"  {date.today()}")
    print("=" * 60)

    t0 = time.time()

    # 1. 初始化数据库 & 表
    init_tables()

    # 2. 拉取数据
    records = fetch_all_stocks()
    if not records:
        print("没有获取到任何数据，退出。")
        return

    # 3. 写入 MySQL（按日期累计）
    trade_date = date.today().isoformat()
    save_daily_data(records, trade_date)
    show_summary(len(records))

    # 4. 展示行情概览
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

    # 验证上一交易日预测
    print("\n" + "=" * 60)
    print("  [验证] 预测验证")
    verify_result = verify_yesterday_predictions()
    if "accuracy_pct" in verify_result:
        print(f"  校验日期: {verify_result['check_date']}")
        print(f"  总预测数: {verify_result['total']}")
        print(f"  方向准确: {verify_result['correct_direction']}/{verify_result['total']}")
        print(f"  准确率: {verify_result['accuracy_pct']}%")
    else:
        print(f"  {verify_result.get('message', verify_result.get('error', '无数据'))}")

    # 累计准确率
    report = get_accuracy_report()
    if report.get("daily"):
        print(f"\n  累计监控 {len(report['daily'])} 天")
        print(f"  累计准确率: {report['avg_accuracy']}%")
        if report["alert"]:
            print(f"  [OK] 准确率 {report['avg_accuracy']}% > 80%，模型表现良好！")
    else:
        print(f"  尚无累计准确率数据")

    # 自动生成未来预测
    print("\n" + "=" * 60)
    print("  [预测] 自动预测")
    pred_result = run_daily_predictions(limit=100)
    print(f"  交易日: {pred_result['trade_date']}")
    print(f"  扫描 {pred_result['total_stocks']} 只活跃股")
    print(f"  成功预测: {pred_result['predicted']} 只")
    print(f"  看涨: {pred_result['up']} 只 | 看跌: {pred_result['down']} 只")
    print(f"  模型: {pred_result['model_version']} | 耗时 {pred_result['elapsed_seconds']}s")
    if pred_result['up'] > pred_result['down']:
        print(f"  [乐观] 市场预测偏乐观")
    else:
        print(f"  [谨慎] 市场预测偏谨慎")

    elapsed = time.time() - t0
    print(f"\n完成! 耗时 {elapsed:.0f}s")


# ── 快速查询命令（命令行传参调用）────────────────────
if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    if "history" in args:
        # python main.py history <code> [limit]
        code = args[args.index("history") + 1] if len(args) > args.index("history") else "600519"
        limit = int(args[args.index("history") + 2]) if len(args) > args.index("history") + 1 and args[-1].isdigit() else 100
        query_history_by_code(code, limit)

    elif "summary" in args:
        from analyzer import show_days_count
        show_days_count()

    else:
        main()
