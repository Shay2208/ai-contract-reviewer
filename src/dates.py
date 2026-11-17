"""自然语言日期解析：把「下周三」「月底」「3 天内」这类说法转成时间戳。

用在 `task-add --due` 上——给人建待办时不想让他们记 ISO 格式。
只处理中文口语里最常见的几种说法，解析不出来就返回 None，由调用方决定怎么提示。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _end_of_day(day: date, hour: int) -> float:
    """把某天的指定小时转成时间戳。"""
    return datetime(day.year, day.month, day.day, hour, 0, 0).timestamp()


def parse_due(text: str, base_ts: float) -> float | None:
    """从口语文本里解析截止时间，解析不出来返回 None。

    支持：今天/明天/后天、本周/这周、下周、周X/星期X、下周一、N天内、N个工作日、
    月底、X月X日、X点前；解析出的时间统一落在当天指定小时（默认 18:00）。

    Args:
        text: 用户输入的原始描述，如「下周三下午 3 点前」。
        base_ts: 以哪个时刻为「现在」，便于测试固定时间。

    Returns:
        截止时间戳；无法识别时返回 None。
    """
    if not text:
        return None
    now = datetime.fromtimestamp(base_ts)
    today = now.date()
    hour = 18

    m = re.search(r"(下午|晚上|上午|凌晨|中午)?\s*(\d{1,2})[点时]", text)
    if m:
        period, h = m.group(1), int(m.group(2))
        if period in ("下午", "晚上") and h < 12:
            h += 12
        if 0 <= h <= 23:
            hour = h

    # 下周X（必须放在「下周」前面，否则「下周三」会被「下周」先吃掉）
    m = re.search(r"下(?:周|星期)([一二三四五六日天])", text)
    if m:
        idx = _WEEKDAY[m.group(1)]
        monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return _end_of_day(monday + timedelta(days=idx), hour)

    # 本周X / 这周X
    m = re.search(r"(?:本|这)(?:周|星期)([一二三四五六日天])", text)
    if m:
        idx = _WEEKDAY[m.group(1)]
        monday = today - timedelta(days=today.weekday())
        return _end_of_day(monday + timedelta(days=idx), hour)

    # 裸的 周X / 星期X：取最近的一个未来日期
    m = re.search(r"(?:周|星期)([一二三四五六日天])(?![一二三四五六日天])", text)
    if m:
        idx = _WEEKDAY[m.group(1)]
        day = today + timedelta(days=(idx - today.weekday()) % 7)
        return _end_of_day(day, hour)

    if re.search(r"(本周|这周)", text):
        monday = today - timedelta(days=today.weekday())
        return _end_of_day(monday + timedelta(days=4), hour)
    if re.search(r"下周", text):
        monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return _end_of_day(monday + timedelta(days=4), hour)

    if re.search(r"明天|明日", text):
        return _end_of_day(today + timedelta(days=1), hour)
    if re.search(r"后天", text):
        return _end_of_day(today + timedelta(days=2), hour)
    if re.search(r"今天|今日|当天", text):
        return _end_of_day(today, hour)
    if "下班" in text:
        return _end_of_day(today, 18)

    m = re.search(r"(\d+)\s*个?工作日", text)
    if m:
        return _end_of_day(today + timedelta(days=int(m.group(1))), hour)
    m = re.search(r"(\d+)\s*天内", text)
    if m:
        return _end_of_day(today + timedelta(days=int(m.group(1))), hour)

    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text)
    if m:
        month, day_num = int(m.group(1)), int(m.group(2))
        try:
            target = date(today.year, month, day_num)
        except ValueError:
            return None
        if target < today:
            target = date(today.year + 1, month, day_num)
        return _end_of_day(target, hour)

    if re.search(r"月底", text):
        next_month = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
        return _end_of_day(next_month - timedelta(days=1), hour)

    return None
