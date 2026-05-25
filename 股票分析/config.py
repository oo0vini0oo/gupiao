"""
股票分析项目 - 全局配置常量
所有模块共享的配置（MySQL、API、字段映射）都放在这里。
"""

# MySQL 连接配置
MYSQL_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "root",
    "port": 3306,
    "charset": "utf8mb4",
}

DATABASE = "stock_analysis"

# 新浪财经 API 地址
SINA_API_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

# User-Agent 列表（反爬轮换）
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
]

# 新浪财经 API 字段 -> 数据库字段名映射
FIELD_MAP = {
    "code":       "stock_code",
    "name":       "stock_name",
    "trade":      "latest_price",
    "changepercent": "change_pct",
    "pricechange":   "change_amount",
    "volume":     "volume",
    "amount":     "turnover",
    "high":       "highest",
    "low":        "lowest",
    "open":       "open_price",
    "settlement": "pre_close",
    "mktcap":     "total_market_cap",
    "nmc":        "circulating_market_cap",
    "per":        "pe_ratio_dynamic",
    "pb":         "pb_ratio",
    "turnoverratio": "turnover_rate",
}

# 每日行情表 stock_daily 的列定义（不含 trade_date / update_time）
DAILY_COLUMNS = [
    "stock_code", "stock_name", "latest_price", "change_pct", "change_amount",
    "volume", "turnover", "highest", "lowest",
    "open_price", "pre_close", "total_market_cap", "circulating_market_cap",
    "pe_ratio_dynamic", "pb_ratio", "turnover_rate",
]
