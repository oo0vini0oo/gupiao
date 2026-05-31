# A股分析平台 — 需求清单复盘

> 最终更新: 2026-05-30
> 本文档汇总所有已确认的需求，新增需求前需逐条核对，有冲突时提示用户选择。

> ⚠️ 待确认：R12、R13 后续可能修改，暂按当前实现执行。

---

## 一、基础设施

### R1: MySQL 数据库
- 数据库名 `stock_analysis`，MySQL on localhost:3306（root/root）
- 4 张核心表：`stock_daily`、`stock_list`、`stock_predictions`、`prediction_accuracy`
- `stock_daily` 唯一键 `(trade_date, stock_code)`，幂等 upsert
- 所有股票代码保持 6 位字符串，使用 `.zfill(6)` 补零

### R2: 服务启动自动化
- `app.py` 启动时在后台线程自动执行完整管道：拉取全量 A 股数据 → 入库 → 验证昨日预测 → 对活跃股生成预测
- Flask 服务立即可用，不等待管道完成
- `use_reloader=False`，修改 .py 文件后需手动重启进程

### R3: Web 服务
- Flask debug 模式，`http://127.0.0.1:5000`
- 前端 ECharts + Bootstrap 5，红涨绿降
- 所有 API 响应格式统一：`{"success": true/false, "data": ...}` 或 `{"error": "..."}`

### R4: Windows 环境适配
- Python 3.12 路径 `D:\Program Files\Python12\python.exe`
- 命令行（GBK 终端）输出避免 emoji，防止 `UnicodeEncodeError`
- Web 模板可自由使用 emoji

---

## 二、交易日历（R5~R7）

### R5: 交易日判断模块
- 新增 `trading_calendar.py`，提供三个函数：
  - `is_trading_day(d)` — 判断是否交易日（周一~五 且 非法定节假日）
  - `next_trading_day(d)` — 下一个交易日
  - `previous_trading_day(d)` — 上一个交易日
- 节假日数据以集合硬编码，注释标明来源（证监办发〔2025〕130 号）
- 每年需手动更新节假日集合

### R6: 非交易日跳过数据抓取
- `app.py` 的 `_startup_pipeline()` 调用 `is_trading_day()`，非交易日跳过 `fetch_all_stocks()`
- `main.py` 同样加判断
- 日志显示"今日非交易日，跳过数据获取"

### R7: 预测目标日期使用交易日历
- `predict_trend_v2()` 和 `get_tomorrow_picks()` 使用 `next_trading_day()` 替代原有的仅跳过周末逻辑
- 确保 `target_date` 始终是真实交易日

---

## 三、预测系统（R8~R13）

### R8: 预测模型
- v2 模型：多项式回归(degree=2) + 加权移动平均，加权融合（poly 0.6, WMA 0.4）
- WMA 权重（最近 5 天）：[0.05, 0.1, 0.15, 0.25, 0.45]
- `predict_trend_v2()` 支持 `_skip_analysis` 参数，批量场景跳过耗时分析

### R9: 预测涨跌幅智能钳制
- 检测多项式与 WMA 的偏离度，偏离 > 10% 时认为模型不稳定
- 不稳定时将预测价格钳制在 ±10%（相对于最新价）
- 正常预测不受影响

### R10: 每日预测数量
- `scan_top_n: 30`（`run_daily_predictions` 扫描数）
- `scan_top10_n: 30`（`predict_top10` 扫描数）
- `tomorrow_scan_n: 30`（`get_tomorrow_picks` 扫描数）

### R11: 预测误差分析
- `verify_yesterday_predictions()` 按板块分组统计准确率
- 统计误差分布：偏乐观/偏保守/方向错误
- 自动生成改进建议
- 方向正确判定：`(predicted_price - actual_price) * change_pct > 0`

### R12: 非交易日不保存预测 ⚠️待确认
- `save_predictions()` 调用 `is_trading_day()`，非交易日直接返回
- 防止 settings 页面 "reanalyze" 按钮在非交易日生成脏数据

### R13: 明日关注过滤异常暴跌 ⚠️待确认
- `get_tomorrow_picks()` 过滤当日跌幅超过 -8% 的股票
- 防止暴跌股出现在"明日关注"列表中

---

## 四、数据一致性（R14~R16）

### R14: 所有页面同一套分析逻辑
- `_analyze_stock()` 作为统一分析入口，所有页面的涨跌原因和分析理由都由该函数生成
- 首页、个股详情页、预测校验页、TOP10 页共用

### R15: 预测数据源统一
- 个股详情页 `/api/stock/<code>/predict` 从 `stock_predictions` 表读取已保存的预测
- 首页 `get_tomorrow_picks()` 从 `stock_predictions` 表读取
- 所有页面不各自生成预测数据

### R16: 查询一致性（INNER JOIN）
- 所有预测相关查询使用 `INNER JOIN` + `stock_daily`，不因 `LEFT JOIN` 导致不同页面计数不一致
- 首页准确率从 `stock_predictions` + `stock_daily` 实时计算，不从 `prediction_accuracy` 表读取

---

## 五、前端展示（R17~R18）

### R17: 数据展示格式统一
- 所有页面统一三列：收盘价（今日最新价）、预估涨幅（今日收盘→目标价）、目标价
- 涨跌幅以最新收盘价为基准计算
- 偏差（change_error）为预测价与实际价的百分比误差

### R18: 个股详情页预测模块条件显示
- 有预测数据时正常展示方向标签、目标日期、预测价格、涨幅、分析理由
- 无预测数据时显示"暂无预测数据"，不展示旧数据
- 预测卡片位于趋势图右侧（col-md-4）

---

## 六、性能与体验（R19~R20）

### R19: 预测校验页面性能
- `get_prediction_details()` 循环中调用 `predict_trend_v2()` 时传 `_skip_analysis=True`
- 避免每只股票重复执行板块查询、PE 查询、新闻匹配等耗时操作
- 页面加载时间控制在 5 秒以内

### R20: 后端模块化架构
- 分层：`fetcher.py`（数据获取）→ `manager.py`（入库）→ `predictor.py`（预测）→ `analysis.py`（分析）
- 独立的 `news.py`（新闻抓取与情感分析）
- 配置集中管理：`config.py` + `prediction_config.py`
- 交易日历独立模块：`trading_calendar.py`

---

## 七、行为规范（协作约定）

### C0: 收到需求后，必须先确认再动手
- 任何需求（无论大小、看起来多简单），必须先做确认
- 确认格式：「理解：要做XXX，方案是XXX，涉及文件XXX。可以开始吗？」
- 必须等到用户回复"可以"或"好的"后才能开始实施
- 用户回复"可以"之前，不得编写任何代码或修改任何文件
- 违反一次，立即停止当前工作并通知用户

### C1: 修改后必须验证，修复要全面
- JS 大括号平衡检查（`{` 和 `}` 数量一致）
- SQL DISTINCT + ORDER BY 列必须在 SELECT 中
- 修复 bug 时 grep 全项目查找同类问题，全部修复后再验证
- 服务重启验证：先杀旧进程 → 确认端口释放 → 启动新进程 → 调用 API 验证
- 端到端验证：页面正常渲染、按钮可点击
