"""
股票分析项目 - 数据获取模块
从新浪财经 API 分页拉取全部 A 股实时行情，带反爬策略。
"""

import time
import random
import requests
from config import SINA_API_URL, USER_AGENTS, FIELD_MAP


def _clean_value(val, is_name: bool = False):
    """清洗单个值：处理 None / '-' / NaN"""
    if val is None or val == "-" or val == "":
        return None
    if str(val).lower() == "nan":
        return None
    if is_name:
        return val  # 名称保持字符串
    try:
        return float(val) if isinstance(val, str) else val
    except (ValueError, TypeError):
        return None


def fetch_all_stocks():
    """
    分页拉取全部 A 股数据。

    Returns
    -------
    list[dict]   每行是一个 dict，键为 stock_code, latest_price 等 DB 字段名。
    """
    print("[FETCH] 正在从新浪财经获取 A 股数据（每页间隔 5-8 秒防封）...")

    max_retries = 5
    all_records = []

    for attempt in range(1, max_retries + 1):
        try:
            session = requests.Session()
            page_size = 80
            current_page = 1
            start_time = time.time()

            while True:
                # 每页请求前随机延时模拟人类操作
                delay = random.uniform(5, 8)
                if current_page > 1:
                    elapsed = time.time() - start_time
                    print(f"    第 {current_page} 页 | 累计 {len(all_records)} 条 | 已用 {elapsed:.0f}s")
                    print(f"    等待 {delay:.1f}s ...")
                    time.sleep(delay)

                session.headers.update({
                    "User-Agent": random.choice(USER_AGENTS),
                    "Referer": "https://finance.sina.com.cn/",
                    "Accept": "*/*",
                })

                params = {
                    "page":  current_page,
                    "num":   page_size,
                    "sort":  "symbol",
                    "asc":   1,
                    "node":  "hs_a",
                    "symbol": "",
                    "_s_r_a": "page",
                }

                r = session.get(SINA_API_URL, params=params, timeout=30)

                # HTTP 456 = 新浪限流，大幅延长时间
                if r.status_code == 456:
                    wait = attempt * 30 + random.randint(10, 30)
                    print(f"    HTTP 456 限流，等待 {wait}s 后重试...")
                    time.sleep(wait)
                    raise requests.exceptions.HTTPError(f"HTTP {r.status_code}", response=r)

                r.raise_for_status()
                data = r.json()

                if not data:
                    break

                for item in data:
                    record = {}
                    for api_field, db_field in FIELD_MAP.items():
                        is_name = (db_field == "stock_name")
                        record[db_field] = _clean_value(item.get(api_field), is_name)
                    all_records.append(record)

                if len(data) < page_size:
                    break
                current_page += 1

            elapsed = time.time() - start_time
            print(f"[FETCH] 共获取 {len(all_records)} 条记录，耗时 {elapsed:.0f}s")
            return all_records

        except requests.exceptions.HTTPError as e:
            print(f"[FETCH] 第 {attempt}/{max_retries} 次失败: {e}")
            if attempt < max_retries:
                wait = attempt * 30
                print(f"    等待 {wait}s 后重试...")
                time.sleep(wait)
                all_records.clear()
            else:
                if all_records:
                    print(f"[FETCH] 保留已有 {len(all_records)} 条记录继续...")
                    return all_records
                raise RuntimeError("多次 HTTP 错误后仍失败") from e
        except Exception as e:
            print(f"[FETCH] 第 {attempt}/{max_retries} 次失败: {e}")
            if attempt < max_retries:
                wait = attempt * 10
                print(f"    等待 {wait}s 后重试...")
                time.sleep(wait)
            else:
                if all_records:
                    return all_records
                raise RuntimeError("多次重试失败") from e
