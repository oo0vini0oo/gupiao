# A股分析平台 — 项目总结

*最后更新: 2026-05-31*

## 项目概述

全栈 A 股股票分析平台，集数据采集、价格预测、新闻监控、虚拟投资于一体。MySQL 后端 + Flask Web 前端，全自动运行。

## 项目规模

| 维度 | 数据 |
|------|------|
| Python 模块 | 12 个 |
| 前端模板 | 7 个（base + 6 个子页面） |
| 数据库表 | 8 张 |
| 代码总量 | ~4000 行 |

## 系统架构

```
┌─────────────────────────────────────────────────┐
│                 数据采集层                        │
│  fetcher.py → manager.py → MySQL (stock_daily)   │
│  新浪财经 API → 分页获取 → 批量 Upsert            │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────┐
│              分析预测层                            │
│  analysis.py   - 板块分类、市场总览                │
│  predictor.py  - 多项式回归 + WMA 集成预测         │
│  virtual.py    - 虚拟投资组合（5万初始资金）        │
│  news.py       - 新闻抓取 + 情感分析               │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────┐
│              Web 展示层 (Flask)                   │
│  首页         - 准确率趋势 + 明日预测 + 热点词云   │
│  个股详情     - K线历史 + 实时预测                 │
│  行业分析     - 板块汇总 + 龙头股                  │
│  新闻热点     - 新闻联播 + 东方财富 + 新浪财经      │
│  预测校验     - 准确率 + 逐只对比                  │
│  预测查询     - 支持代码/名称/热词三种输入          │
│  投资组合     - 模拟交易 + 每日盈亏                │
└─────────────────────────────────────────────────┘
```

## 模块说明

### 数据采集（fetcher.py → manager.py）

- 新浪财经 API 分页拉取全 A 股行情（80 只/页）
- 反爬策略：随机 UA 轮换 + 5-8 秒页间隔 + HTTP 456 限流自动退避
- 每日增量写入 `stock_daily`，同一天同一代码幂等（ON DUPLICATE KEY UPDATE）
- 同步维护 `stock_list` 表（代码↔名称映射）

### 预测系统（predictor.py）

- **模型**：多项式回归(degree=2, 权重0.6) + 加权移动平均(权重0.4) 集成
- **WMA 权重（5日）**：`[0.05, 0.1, 0.15, 0.25, 0.45]`（最近一天权重最高）
- **每日扫描**：成交额前 30 只活跃股自动预测
- **高涨幅扫描**：额外扫描 300 只，筛选预估涨幅 >3% 的候选股（30 分钟缓存）
- **分析维度**：均线形态(MA5/MA10/MA20) → 量价关系 → 板块热度 → PE估值 → 新闻关键词匹配 → 短期动量
- **验证机制**：每日验证方向准确率（基于 pre_close 比较预测/实际涨跌方向）
- **板块级错误分析**：按板块统计准确率、误差分布，生成改进建议

### 新闻监控（news.py）

- 3 个数据源：
  - 东方财富：财经要闻列表
  - 新浪财经：滚动新闻 feed API
  - 新闻联播：cn.govopendata.com 文字版
- jieba TF-IDF 提取热点关键词（可配置 topN/权重阈值）
- SnowNLP 情感分析（积极/中性/消极）
- 关键词关联个股，写入预测分析理由

### 虚拟投资组合（virtual.py）

- 初始资金 50,000 元（首次访问自动创建账户）
- 买入：按最新价成交，支持多次买入摊薄均价
- 卖出：按最新价成交，支持部分卖出
- 持仓管理：实时市值、盈亏金额/百分比
- 交易记录：完整历史
- 每日结算：记录总资产/现金/持仓市值/当日盈亏/累计盈亏快照

### Web 页面一览

| 路由 | 功能 | API |
|------|------|-----|
| `/` | 首页：准确率趋势 + 明日预测列表 + 热点词云 | `/api/overview`, `/api/predict/tomorrow`, `/api/news/keywords`, `/api/predict/accuracy` |
| `/stock/<code>` | 个股详情：历史走势 + 预测 | `/api/stock/<code>/history`, `/api/stock/<code>/predict` |
| `/industry` | 行业分析：各板块汇总 + 龙头/涨幅榜 | `/api/sectors` |
| `/news` | 新闻热点：新闻列表 + 情感 + 词云 | `/api/news` |
| `/check` | 预测校验：逐只对比/误差/方向 | `/api/predict/check`, `/api/predict/records` |
| `/predictions` | 预测记录：按日期分组历史 | `/api/predict/records` |
| `/settings` | 预测查询：代码/名称/热词查询 + 行业分类编辑 | `/api/predict/by_input`, `/api/settings` |
| `/portfolio` | 投资组合：模拟买卖 + 持仓 + 盈亏曲线 | `/api/portfolio`, `/api/portfolio/buy`, `/api/portfolio/sell`, `/api/portfolio/transactions`, `/api/portfolio/pnl` |
| 全局搜索 | 首页搜索框模糊匹配代码/名称 | `/api/stock/search` |

所有 API 遵循统一响应格式：`{"success": true/false, "data": ...}` 或 `{"error": "..."}`。

### 数据库（8 张表）

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| `stock_daily` | 每日行情快照 | trade_date, stock_code, latest_price, change_pct, volume, turnover, highest/lowest/open/pre_close, total_market_cap, pe_ratio_dynamic, pb_ratio, turnover_rate |
| `stock_list` | 代码↔名称 | stock_code, stock_name |
| `stock_predictions` | 预测记录 | predict_date, target_date, predicted_price, actual_price, error_pct, model_version |
| `prediction_accuracy` | 每日准确率 | check_date, total_predictions, correct_predictions, accuracy_pct |
| `virtual_account` | 虚拟账户 | cash_balance, init_balance |
| `virtual_holdings` | 持仓 | stock_code, quantity, buy_price, buy_date |
| `virtual_transactions` | 交易记录 | tx_type(buy/sell), quantity, price, amount |
| `virtual_daily_pnl` | 每日盈亏快照 | total_assets, cash_balance, holdings_value, daily_pnl, total_pnl |

### 板块分类（代码前缀映射）

| 板块 | 代码前缀 |
|------|---------|
| 沪市主板 | 600, 601, 603 |
| 深市主板 | 000, 001, 002 |
| 创业板 | 300 |
| 科创板 | 688 |
| 北交所 | 830~839, 870~889, 920 |

### 预测配置系统（prediction_config.py）

- JSON 配置文件：`config/prediction_settings.json`
- Web 界面可编辑（settings 页面）
- 可配置项：模型参数（多项式阶数、WMA权重、融合比例）、评分权重、扫描数量、均线周期、PE阈值、新闻匹配参数
- 深拷贝合并逻辑：新增配置键自动补充默认值
- 行业分类映射：关键词→多只股票，支持 12 个行业分类

### 交易日历（trading_calendar.py）

- 判断交易日：周一~五 + 非节假日
- 2026 年节假日已内置（证监办发〔2025〕130 号）
- 每年只需更新 `HOLIDAYS` 集合即可

## 部署运维

### 启动命令

```bash
# Web 服务（生产后台）
pythonw app.py              # http://127.0.0.1:5000

# 手动数据拉取+预测
python main.py              # 全量拉取 + 验证 + 预测
python main.py fetch        # 仅拉取 + 验证
python main.py predict      # 仅预测

# 查询工具
python main.py history 600519 120    # 查历史
python main.py summary              # 数据覆盖摘要
```

### 自动调度

- 开机自启：Windows 计划任务注册（`setup_startup_web.ps1`）
- 后台服务：`pythonw.exe` 无控制台窗口运行
- Flask 启动自动执行管道：数据拉取 → 预测验证 → 动态调度
- 自动预测时间：下一交易日的前一天 20:00
- 错过时间自动补采：启动后立即执行 + 扩大新闻范围
- 进程管理：`stop_web.bat` 停止服务

### 环境依赖

```
Python 3.12+ (D:\Program Files\Python12\python.exe)
MySQL 8.0 (localhost:3306, root/root, database: stock_analysis)
依赖: pymysql, requests, flask, jieba, snownlp, numpy, beautifulsoup4
```

### 使用端口

- Web: 5000
- MySQL: 3306

## 重要约定

### A股颜色规范

- **红涨绿跌**：上涨 `#e74c3c`（红色），下跌 `#27ae60`（绿色）
- CSS 类：`.up {}` / `.down {}`，`.badge-up {}` / `.badge-down {}`

### 股票代码格式

- 全部使用 6 位字符串，`.zfill(6)` 补齐前导零
- 深市代码（000/001/002/300 开头）从 API 获取时可能丢失前导零

### Flask 注意事项

- `use_reloader=False` 不会自动重载 Python 模块
- 修改 `.py` 后必须杀掉旧进程再重启

### 预测方向验证规则

`verify_yesterday_predictions()` 使用 `(predicted_price - pre_close)` 的方向与 `change_pct` 的方向比较来判断正确性（以 target_date 的 pre_close 为基准）。

## 开发环境

- OS: Windows 11
- Python: 3.12 (D:\Program Files\Python12)
- 终端编码: GBK（.py 文件中避免 print emoji）
- Web 模板中使用 emoji 不受影响（浏览器 UTF-8 渲染）
