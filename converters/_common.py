"""_common.py - converters 跨文件复用的工具函数（消除重复定义与内联 try/except）。"""


def safe_int(val, default=0):
    """安全转换字符串为整数，失败时返回默认值。"""
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def safe_float(val, default=0.0):
    """安全转换字符串为浮点数，失败时返回默认值。"""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _hms_to_seconds(h, m, s):
    """将时分秒转换为当天秒数（h*3600 + m*60 + s）。"""
    return h * 3600 + m * 60 + s
