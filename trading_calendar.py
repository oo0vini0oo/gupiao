"""
A股交易日历工具
判断交易日、计算下一/上一交易日。
节假日数据来自沪深北交易所官方安排（2025年12月22日发布）。
每年需更新 HOLIDAYS 集合。
"""
from datetime import date, timedelta

# ── 2026 年 A 股法定节假日（仅含工作日休市，周末自然休市不计入）──
# 数据来源：证监办发〔2025〕130 号，沪/深/北交易所 2025-12-22 发布
# 每年更新：替换此集合即可，函数逻辑无需改动
HOLIDAYS_2026 = {
    date(2026, 1, 1),    # 元旦
    date(2026, 1, 2),    # 元旦
    date(2026, 2, 16),   # 春节
    date(2026, 2, 17),   # 春节（除夕）
    date(2026, 2, 18),   # 春节（初一）
    date(2026, 2, 19),   # 春节
    date(2026, 2, 20),   # 春节
    date(2026, 2, 23),   # 春节
    date(2026, 4, 6),    # 清明节
    date(2026, 5, 1),    # 劳动节
    date(2026, 5, 4),    # 劳动节
    date(2026, 5, 5),    # 劳动节
    date(2026, 6, 19),   # 端午节
    date(2026, 9, 25),   # 中秋节
    date(2026, 10, 1),   # 国庆节
    date(2026, 10, 2),   # 国庆节
    date(2026, 10, 5),   # 国庆节
    date(2026, 10, 6),   # 国庆节
    date(2026, 10, 7),   # 国庆节
}

# 按年份聚合，方便逐年扩展
_HOLIDAY_BY_YEAR = {2026: HOLIDAYS_2026}


def _get_holidays(d: date) -> set:
    """获取 date 所在年份的节假日集合，无配置时返回空集。"""
    return _HOLIDAY_BY_YEAR.get(d.year, set())


def is_trading_day(d: date) -> bool:
    """判断 d 是否为 A 股交易日（周一~五 且 非节假日）。"""
    if d.weekday() >= 5:  # 周六、日
        return False
    return d not in _get_holidays(d)


def next_trading_day(d: date) -> date:
    """返回 d 之后的第一个交易日。"""
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def previous_trading_day(d: date) -> date:
    """返回 d 之前的第一个交易日。"""
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
