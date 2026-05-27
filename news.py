"""
新闻抓取与热点分析模块
从东方财富、新浪财经抓取新闻，提取热点关键词并做情感分析。
"""

import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from config import NEWS_SOURCES


def fetch_eastmoney_news() -> list:
    """抓取东方财富-财经要闻"""
    url = NEWS_SOURCES["eastmoney"]["url"]
    source = NEWS_SOURCES["eastmoney"]["name"]
    news_list = []
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.eastmoney.com/",
        }, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 东方财富要闻列表
        for item in soup.select("ul.news_list li a"):
            title = item.get_text(strip=True)
            href = item.get("href", "")
            if title and len(title) > 5:
                news_list.append({
                    "title": title,
                    "url": href if href.startswith("http") else f"https:{href}",
                    "source": source,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                })
    except Exception as e:
        print(f"[NEWS] 东方财富抓取失败: {e}")

    return news_list


def fetch_sina_news() -> list:
    """抓取新浪财经滚动新闻"""
    url = NEWS_SOURCES["sina"]["url"]
    source = NEWS_SOURCES["sina"]["name"]
    news_list = []
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        }, timeout=15)
        data = resp.json()
        for item in data.get("result", {}).get("data", []):
            title = item.get("title", "").strip()
            if title:
                news_list.append({
                    "title": title,
                    "url": item.get("url", ""),
                    "source": source,
                    "time": item.get("ctime", datetime.now().strftime("%Y-%m-%d %H:%M")),
                })
    except Exception as e:
        print(f"[NEWS] 新浪财经抓取失败: {e}")

    return news_list


def fetch_xinwenlianbo() -> list:
    """抓取新闻联播文字版（cn.govopendata.com）"""
    url = NEWS_SOURCES["xinwenlianbo"]["url"]
    source = NEWS_SOURCES["xinwenlianbo"]["name"]
    news_list = []
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        # 提取今日新闻条目
        for a in soup.select("article a"):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if title and len(title) > 5 and "/xinwenlianbo/" in href and "#" in href:
                news_list.append({
                    "title": title,
                    "url": f"https://cn.govopendata.com{href}" if href.startswith("/") else href,
                    "source": source,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                })
        if not news_list:
            # 降级：取所有含 # 的链接
            for a in soup.find_all("a", href=True):
                href = a["href"]
                title = a.get_text(strip=True)
                if title and len(title) > 5 and "#" in href and "/xinwenlianbo/" in href:
                    news_list.append({
                        "title": title,
                        "url": f"https://cn.govopendata.com{href}" if href.startswith("/") else href,
                        "source": source,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    })
    except Exception as e:
        print(f"[NEWS] 新闻联播抓取失败: {e}")

    return news_list


def fetch_all_news() -> list:
    """从所有新闻源抓取"""
    all_news = []
    all_news.extend(fetch_eastmoney_news())
    all_news.extend(fetch_sina_news())
    all_news.extend(fetch_xinwenlianbo())
    # 去重（基于标题相似度）
    seen = set()
    unique = []
    for n in all_news:
        key = n["title"][:20]
        if key not in seen:
            seen.add(key)
            unique.append(n)
    return unique


def extract_hot_topics(news_list: list, top_n: int = 30) -> list:
    """使用 jieba 分词提取新闻热点关键词"""
    try:
        import jieba.analyse
    except ImportError:
        return [{"word": "请安装 jieba 库", "weight": 0}]

    text = " ".join([n["title"] for n in news_list])
    keywords = jieba.analyse.extract_tags(text, topK=top_n, withWeight=True)
    return [
        {"word": w, "weight": round(float(weight), 4)}
        for w, weight in keywords
    ]


def analyze_sentiment(news_list: list) -> dict:
    """对新闻标题做情感分析"""
    try:
        from snownlp import SnowNLP
    except ImportError:
        return {"avg_sentiment": 0.5, "total": 0, "positive": 0, "negative": 0, "neutral": 0}

    scores = []
    for n in news_list:
        try:
            s = SnowNLP(n["title"])
            scores.append(s.sentiments)
        except Exception:
            scores.append(0.5)

    if not scores:
        return {"avg_sentiment": 0.5, "total": 0, "positive": 0, "negative": 0, "neutral": 0}

    avg = float(np.mean(scores)) if len(scores) > 1 else scores[0]
    positive = sum(1 for s in scores if s > 0.6)
    negative = sum(1 for s in scores if s < 0.4)
    neutral = len(scores) - positive - negative

    sentiment_label = "积极" if avg > 0.6 else ("消极" if avg < 0.4 else "中性")

    return {
        "avg_sentiment": round(avg, 4),
        "label": sentiment_label,
        "total": len(scores),
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
    }


def get_news_analysis() -> dict:
    """获取完整的新闻分析结果（供 API 调用）"""
    news = fetch_all_news()
    topics = extract_hot_topics(news)
    sentiment = analyze_sentiment(news)
    xwlb_count = sum(1 for n in news if n["source"] == "新闻联播")
    return {
        "news": news,
        "hot_topics": topics,
        "sentiment": {**sentiment, "xwlb_count": xwlb_count},
    }


# 缺失的 import 补充
import numpy as np
