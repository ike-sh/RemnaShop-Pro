"""Bounded plain-text Telegram views for the 3.4.4 operations."""


def fit_message(text: str, limit: int = 3000) -> str:
    units = 0
    chars = []
    for char in str(text):
        cost = 2 if ord(char) > 0xFFFF else 1
        if units + cost > limit - 1:
            return "".join(chars) + "…"
        chars.append(char)
        units += cost
    return "".join(chars)


def mask_hwid(value: str) -> str:
    value = str(value or "")
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def device_summary(payload: dict, limit, *, admin=False) -> str:
    devices = payload.get("devices") or []
    total = int(payload.get("total") or 0)
    lines = ["📱 设备管理" if admin else "📱 我的设备", f"设备数：{total}",
             f"设备上限：{limit if limit is not None else '未设置'}"]
    if not devices:
        lines.append("暂无设备。")
    for i, device in enumerate(devices[:8], 1):
        platform = str(device.get("platform") or "未知平台")[:30]
        model = str(device.get("deviceModel") or "未知设备")[:40]
        lines.append(f"{i}. {platform} / {model} / {mask_hwid(device.get('hwid'))}")
    if total > 8:
        lines.append(f"仅显示前 8 台，另有 {total - 8} 台。")
    return fit_message("\n".join(lines))


def dashboard_summary(stats: dict, recap: dict, digest: dict) -> str:
    users = stats.get("users") or {}
    counts = users.get("statusCounts") or {}
    nodes = stats.get("nodes") or {}
    month = recap.get("thisMonth") or {}
    changes = digest.get("users") or {}
    traffic = digest.get("traffic") or {}
    lines = [
        "📊 Remnawave 数据",
        f"用户总数：{users.get('totalUsers', '-')}",
        f"ACTIVE：{counts.get('ACTIVE', '-')}  EXPIRED：{counts.get('EXPIRED', '-')}",
        f"LIMITED：{counts.get('LIMITED', '-')}  DISABLED：{counts.get('DISABLED', '-')}",
        f"近 7 天新增：{changes.get('createdCount', '-')}  到期：{changes.get('expiredCount', '-')}",
        f"近 7 天流量字节：{traffic.get('totalBytes', '-')}",
        f"本月用户：{month.get('users', '-')}  流量字节：{month.get('traffic', '-')}",
        f"在线节点：{nodes.get('totalOnline', '-')}",
    ]
    return fit_message("\n".join(lines))


def node_metrics_summary(payload: dict) -> str:
    nodes = payload.get("nodes") or []
    lines = [f"🌐 节点指标（显示 {min(len(nodes), 8)}/{len(nodes)}）"]
    for node in nodes[:8]:
        name = str(node.get("nodeName") or "未命名")[:50]
        lines.append(f"- {name}：在线用户 {node.get('usersOnline', '-')}")
    if not nodes:
        lines.append("暂无节点指标。")
    return fit_message("\n".join(lines))


def http_stats_summary(payload: dict) -> str:
    routes = payload.get("routes") or []
    lines = [f"📈 HTTP 统计：总请求 {payload.get('total', '-')}（显示 {min(len(routes), 10)}/{len(routes)}）"]
    for route in routes[:10]:
        method = str(route.get("method") or "")[:8]
        path = str(route.get("route") or "")[:70]
        lines.append(f"- {method} {path}：{route.get('count', '-')}")
    if not routes:
        lines.append("暂无 HTTP 统计。")
    return fit_message("\n".join(lines))


def geocheck_summary(payload: dict) -> str:
    if not payload.get("isCompleted"):
        return "🩺 GeoCheck 尚未完成。"
    result = payload.get("result") or {}
    lines = ["🩺 GeoCheck 结果", f"状态：{'失败' if payload.get('isFailed') else '完成'}",
             f"检测成功：{'是' if result.get('success') else '否'}"]
    if result.get("nodeUuid"):
        lines.append(f"节点 UUID：{str(result['nodeUuid'])[:36]}")
    if result.get("message"):
        lines.append(f"消息：{str(result['message'])[:250]}")
    return fit_message("\n".join(lines))


def top_hwid_users_summary(payload: dict) -> str:
    users = payload.get("users") or []
    lines = [f"📱 HWID Top Users（显示 {min(len(users), 10)}/{payload.get('total', len(users))}）"]
    for row in users[:10]:
        lines.append(f"- #{row.get('id', '-')} {str(row.get('username') or '-')[:40]}：{row.get('devicesCount', '-')} 台")
    if not users:
        lines.append("暂无设备统计。")
    return fit_message("\n".join(lines))
