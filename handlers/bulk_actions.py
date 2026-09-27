import datetime
import re

USER_ID_RE = re.compile(r"^[1-9][0-9]*$")


def parse_user_ids(text: str) -> list[int]:
    raw = re.split(r"[\s,;]+", (text or "").strip())
    user_ids = []
    seen = set()
    for item in raw:
        if not item:
            continue
        if not USER_ID_RE.match(item):
            continue
        key = int(item)
        if key in seen:
            continue
        seen.add(key)
        user_ids.append(key)
    return user_ids


def parse_user_ids_strict(text: str) -> list[int]:
    tokens = [item for item in re.split(r"[\s,;]+", (text or "").strip()) if item]
    if not tokens or any(not USER_ID_RE.fullmatch(item) for item in tokens):
        raise ValueError("请输入有效的面板数值用户 ID，不能包含其他内容")
    return list(dict.fromkeys(int(item) for item in tokens))


def parse_extend_days_and_user_ids(text: str) -> tuple[int, list[int]]:
    parts = (text or "").strip().splitlines()
    if len(parts) < 2 or not USER_ID_RE.fullmatch(parts[0].strip()):
        raise ValueError("第一行必须是 1–9999 的续期天数，后续为用户 ID")
    days = int(parts[0].strip())
    if days > 9999:
        raise ValueError("续期天数不能超过 9999")
    return days, parse_user_ids_strict("\n".join(parts[1:]))


def parse_expire_days_and_user_ids(text: str):
    parts = (text or "").strip().splitlines()
    if len(parts) < 2:
        raise ValueError("格式错误，应为：第一行天数，后续为用户ID列表")
    days = int(parts[0].strip())
    if days <= 0:
        raise ValueError("天数必须 > 0")
    user_ids = parse_user_ids("\n".join(parts[1:]))
    expire_at = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return expire_at, user_ids


def parse_traffic_and_user_ids(text: str):
    parts = (text or "").strip().splitlines()
    if len(parts) < 2:
        raise ValueError("格式错误，应为：第一行GB，后续为用户ID列表")
    gb = float(parts[0].strip())
    if gb <= 0:
        raise ValueError("流量必须 > 0")
    user_ids = parse_user_ids("\n".join(parts[1:]))
    traffic_bytes = int(gb * 1024 * 1024 * 1024)
    return traffic_bytes, user_ids
