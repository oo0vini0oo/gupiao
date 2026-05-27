"""
股票分析项目 - 数据库管理模块
负责连接 MySQL、创建表、查询历史数据。
"""

import pymysql
from config import MYSQL_CONFIG, DATABASE


# ── 建表 SQL ──────────────────────────────────────────────

CREATE_STOCK_DAILY_SQL = """
CREATE TABLE IF NOT EXISTS stock_daily (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    trade_date    DATE        NOT NULL COMMENT '交易日期',
    stock_code    VARCHAR(10) NOT NULL COMMENT '股票代码',
    stock_name    VARCHAR(50) COMMENT '股票名称',
    latest_price  DECIMAL(10,3)   COMMENT '最新价',
    change_pct    DECIMAL(10,4)   COMMENT '涨跌幅(%%)',
    change_amount DECIMAL(10,3)   COMMENT '涨跌额',
    volume        BIGINT          COMMENT '成交量(手)',
    turnover      DECIMAL(20,2)   COMMENT '成交额',
    highest       DECIMAL(10,3)   COMMENT '最高价',
    lowest        DECIMAL(10,3)   COMMENT '最低价',
    open_price    DECIMAL(10,3)   COMMENT '今开价',
    pre_close     DECIMAL(10,3)   COMMENT '昨收价',
    total_market_cap   DECIMAL(20,2) COMMENT '总市值(万元)',
    circulating_market_cap DECIMAL(20,2) COMMENT '流通市值(万元)',
    pe_ratio_dynamic DECIMAL(15,4) COMMENT '市盈率(动态)',
    pb_ratio        DECIMAL(10,4) COMMENT '市净率',
    turnover_rate   DECIMAL(10,4) COMMENT '换手率(%%)',
    update_time     DATETIME      COMMENT '入库时间',
    UNIQUE KEY uk_date_stock (trade_date, stock_code),
    INDEX idx_stock_code (stock_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='A股每日行情快照';
"""

CREATE_STOCK_LIST_SQL = """
CREATE TABLE IF NOT EXISTS stock_list (
    stock_code  VARCHAR(10) PRIMARY KEY COMMENT '股票代码',
    stock_name  VARCHAR(50)         COMMENT '股票名称'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='A股股票列表';
"""

CREATE_PREDICTIONS_SQL = """
CREATE TABLE IF NOT EXISTS stock_predictions (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    stock_code      VARCHAR(10)   NOT NULL COMMENT '股票代码',
    predict_date    DATE          NOT NULL COMMENT '预测日期',
    target_date     DATE          NOT NULL COMMENT '预测目标日期',
    predicted_price DECIMAL(10,3) COMMENT '预测收盘价',
    actual_price    DECIMAL(10,3) COMMENT '实际收盘价',
    error_pct       DECIMAL(10,4) COMMENT '误差百分比',
    model_version   VARCHAR(20)   DEFAULT 'v1' COMMENT '模型版本',
    created_at      DATETIME      COMMENT '创建时间',
    UNIQUE KEY uk_pred (stock_code, predict_date, target_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票预测记录';
"""

CREATE_ACCURACY_SQL = """
CREATE TABLE IF NOT EXISTS prediction_accuracy (
    id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
    check_date         DATE          NOT NULL COMMENT '校验日期',
    total_predictions  INT           COMMENT '总预测数',
    correct_predictions INT          COMMENT '正确数（方向准确）',
    accuracy_pct       DECIMAL(10,4) COMMENT '方向准确率',
    model_version      VARCHAR(20)   COMMENT '模型版本',
    created_at         DATETIME      COMMENT '创建时间',
    UNIQUE KEY uk_check (check_date, model_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='预测准确率统计';
"""


def _get_connection(with_db: bool = True) -> pymysql.Connection:
    """获取 MySQL 连接"""
    cfg = MYSQL_CONFIG.copy()
    if with_db:
        cfg["database"] = DATABASE
    return pymysql.connect(**cfg)


def init_tables():
    """创建数据库和数据表（幂等）"""
    print("[DB] 初始化数据库...")

    # 创建数据库
    conn = _get_connection(with_db=False)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DATABASE}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()

    # 创建所有表
    conn = _get_connection(with_db=True)
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_STOCK_DAILY_SQL)
            cur.execute(CREATE_STOCK_LIST_SQL)
            cur.execute(CREATE_PREDICTIONS_SQL)
            cur.execute(CREATE_ACCURACY_SQL)
        conn.commit()
        print("    stock_daily / stock_list / stock_predictions / prediction_accuracy 已就绪")
    finally:
        conn.close()


def count_total_records() -> int:
    """返回 stock_daily 总行数"""
    conn = _get_connection(with_db=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stock_daily")
            return cur.fetchone()[0]
    finally:
        conn.close()


def query_stock_history(stock_code: str, limit: int = 100) -> list:
    """查询某只股票的历史日线数据（按日期倒序，默认最近 N 条）"""
    conn = _get_connection(with_db=True)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """SELECT trade_date, stock_name, latest_price, change_pct,
                          volume, turnover, highest, lowest, open_price, pre_close,
                          total_market_cap, pe_ratio_dynamic, turnover_rate
                   FROM stock_daily
                   WHERE stock_code = %s
                   ORDER BY trade_date DESC
                   LIMIT %s""",
                (stock_code, limit),
            )
            return cur.fetchall()
    finally:
        conn.close()


def query_latest_by_field(field: str, n: int = 10, asc: bool = False) -> list:
    """按某字段排序查询最新一天各股票的数据"""
    extra_field = "" if field == "latest_price" else f", d.latest_price"
    direction = "ASC" if asc else "DESC"
    sql = f"""
        SELECT d.trade_date, d.stock_code, d.stock_name, d.{field}{extra_field}
        FROM (SELECT trade_date FROM stock_daily ORDER BY trade_date DESC LIMIT 1) t
        JOIN stock_daily d ON d.trade_date = t.trade_date
        ORDER BY CAST({field} AS DECIMAL(20,2)) {direction}
        LIMIT {n}
    """
    conn = _get_connection(with_db=True)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql)
            return cur.fetchall()
    finally:
        conn.close()


def get_stock_count() -> int:
    """返回 stock_list 中不同股票的个数"""
    conn = _get_connection(with_db=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT stock_code) FROM stock_daily")
            return cur.fetchone()[0]
    finally:
        conn.close()
