"""
Prediction settings configuration module.
Load/save/validate settings from config/prediction_settings.json.
"""

import json
import os
import copy

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config", "prediction_settings.json")

_DEFAULTS = {
    "model": {
        "poly_degree": 2,
        "wma_weights_5": [0.05, 0.1, 0.15, 0.25, 0.45],
        "wma_weights_4": [0.1, 0.15, 0.25, 0.5],
        "wma_weights_3": [0.15, 0.3, 0.55],
        "ensemble_poly_weight": 0.6,
        "ensemble_wma_weight": 0.4,
        "damping_factor": 0.3,
        "max_step_change_pct": 10.0,
        "max_total_change_pct": 20.0,
    },
    "scoring": {
        "pred_change_high": 3.0,
        "pred_change_mid": 1.0,
        "pred_change_low": 0.5,
        "score_pred_high": 3,
        "score_pred_mid": 2,
        "score_pred_low": 1,
        "today_chg_high": 2.0,
        "today_chg_low": 0.0,
        "score_today_high": 2,
        "score_today_low": 1,
        "sector_trend_high": 1.0,
        "sector_trend_low": 0.3,
        "score_sector_high": 2,
        "score_sector_low": 1,
        "score_news_match": 1,
    },
    "confidence": {
        "high_min": 6,
        "medium_min": 4,
    },
    "filters": {
        "scan_top_n": 30,
        "scan_top10_n": 30,
        "tomorrow_scan_n": 30,
        "tomorrow_return_n": 30,
        "batch_predict_fallback": 5,
    },
    "analysis": {
        "history_days": 60,
        "predict_days": 5,
        "ma_periods": {"ma5": 5, "ma10": 10, "ma20": 20},
        "volume_periods": {"vol5": 5, "vol10": 10},
        "volume_surge_ratio": 1.3,
        "momentum_lookback": 4,
        "pe_low_min": 5,
        "pe_low_max": 15,
        "pe_medium_min": 15,
        "pe_medium_max": 30,
        "pe_high_min": 80,
        "trend_strong": 10.0,
        "trend_good": 5.0,
        "trend_steady": 2.0,
        "trend_risk_drop": -5.0,
        "today_surge": 5.0,
        "today_strong": 3.0,
        "today_rise": 1.0,
        "today_weak": -3.0,
        "sector_active": 1.0,
        "sector_rise": 0.3,
        "sector_weak": -0.5,
    },
    "news": {
        "cache_ttl": 300,
        "hot_topics_n": 40,
        "min_topic_weight": 0.005,
        "display_keywords": 10,
        "matched_keywords": 2,
    },
    "stock_mapping": {
        "白酒": "600519,贵州茅台;000858,五粮液;000568,泸州老窖;600809,山西汾酒;000596,古井贡酒",
        "芯片": "688981,中芯国际;002371,北方华创;688012,中微公司;603986,兆易创新;300661,圣邦股份",
        "新能源": "300750,宁德时代;002594,比亚迪;601012,隆基绿能;300274,阳光电源;600438,通威股份",
        "医药": "600276,恒瑞医药;300760,迈瑞医疗;603259,药明康德;300015,爱尔眼科;000538,云南白药",
        "军工": "600893,航发动力;600760,中航沈飞;600879,航天电子;600118,中国卫星;002179,中航光电",
        "贵金属": "601899,紫金矿业;600547,山东黄金;600489,中金黄金;600988,赤峰黄金;000975,银泰黄金",
        "金融": "601398,工商银行;600036,招商银行;600030,中信证券;601318,中国平安;601628,中国人寿",
        "证券": "600030,中信证券;600999,招商证券;601688,华泰证券;300059,东方财富;601211,国泰君安",
        "银行": "601398,工商银行;601939,建设银行;600036,招商银行;601166,兴业银行;002142,宁波银行",
        "保险": "601318,中国平安;601628,中国人寿;601601,中国太保;601336,新华保险;601319,中国人保",
        "航天": "600879,航天电子;600118,中国卫星;600893,航发动力;600760,中航沈飞;000768,中航西飞",
        "房地产": "000002,万科A;600048,保利发展;001979,招商蛇口;600383,金地集团;600325,华发股份"
    },
    "reason": {
        "momentum_strong": 1.0,
        "momentum_mild": 0.0,
        "momentum_range": -1.0,
        "sector_hot": 0.5,
        "pe_reasonable_min": 10,
        "pe_reasonable_max": 30,
        "pred_large_gain": 10.0,
        "pred_steady_rise": 5.0,
    },
}

_config_cache = None


def _deep_merge(base, overlay):
    """Recursively merge overlay into base, preserving base keys not in overlay."""
    result = copy.deepcopy(base)
    for key, val in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def get_config():
    """Load config from JSON file, merged with defaults for any missing keys."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if not os.path.exists(_CONFIG_PATH):
        _config_cache = copy.deepcopy(_DEFAULTS)
        return _config_cache
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        _config_cache = _deep_merge(_DEFAULTS, loaded)
    except (json.JSONDecodeError, IOError):
        _config_cache = copy.deepcopy(_DEFAULTS)
    return _config_cache


def reload_config():
    """Force re-read from disk on next get_config() call."""
    global _config_cache
    _config_cache = None


def save_config(updates):
    """Validate and save config updates. Returns (success, error_msg)."""
    errors = _validate(updates)
    if errors:
        return False, "; ".join(errors)

    current = get_config()

    def _apply(cfg, u):
        for key, val in u.items():
            if isinstance(val, dict) and key in cfg and isinstance(cfg[key], dict):
                _apply(cfg[key], val)
            else:
                cfg[key] = val

    _apply(current, updates)

    try:
        os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
    except IOError as e:
        return False, f"写入文件失败: {e}"

    reload_config()
    return True, "配置已保存"


def _validate(updates):
    """Validate config updates. Returns list of error strings."""
    errors = []
    _validate_recursive(updates, "", errors)
    return errors


def _validate_recursive(obj, path, errors):
    if isinstance(obj, dict):
        for key, val in obj.items():
            # stock_mapping 是字符串映射，跳过数字验证
            if path == "" and key == "stock_mapping":
                continue
            _validate_recursive(val, f"{path}.{key}" if path else key, errors)
        return
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            _validate_recursive(item, f"{path}[{i}]", errors)
        return
    if isinstance(obj, (int, float)):
        if isinstance(obj, float) and (obj != obj):  # NaN check
            errors.append(f"{path} 不是有效数字")
        return
    if isinstance(obj, bool):
        errors.append(f"{path} 应为数字")
        return
    if obj is None:
        errors.append(f"{path} 不能为空")
        return
    # strings, other types not expected in numeric config
    if isinstance(obj, str):
        errors.append(f"{path} 应为数字，当前为文本")
