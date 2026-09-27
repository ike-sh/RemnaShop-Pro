"""Merge deployment secrets with persistent bot configuration at startup."""

import json
import os
import pathlib
import sys
import tempfile


def _bool_from_env(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("PANEL_VERIFY_TLS must be a boolean value")


def ensure_config(config_path: pathlib.Path, env: dict[str, str]) -> dict:
    config_path = pathlib.Path(config_path)
    if config_path.exists():
        current = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            raise ValueError("config.json must contain an object")
        config = dict(current)
    else:
        current = None
        config = {
            "panel_url": env.get("PANEL_URL", ""),
            "panel_token": "",
            "sub_domain": env.get("SUB_DOMAIN", ""),
            "group_uuid": env.get("GROUP_UUID", ""),
            "panel_verify_tls": _bool_from_env(env.get("PANEL_VERIFY_TLS", "true")),
        }

    # Deploy-time identities always win when explicitly set. An empty
    # PANEL_TOKEN leaves the administrator-managed value in config.json intact.
    for env_key, config_key in (
        ("ADMIN_ID", "admin_id"),
        ("BOT_TOKEN", "bot_token"),
        ("PANEL_TOKEN", "panel_token"),
    ):
        value = env.get(env_key, "")
        if env_key == "PANEL_TOKEN" and value == "your_panel_api_token":
            value = ""
        if value:
            config[config_key] = value

    missing = [key for key in ("admin_id", "bot_token") if not str(config.get(key, "")).strip()]
    if missing:
        raise ValueError(f"配置缺少字段: {', '.join(missing)}")

    if config != current:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=config_path.parent,
            prefix=".config-", suffix=".tmp", delete=False,
        ) as temporary:
            os.chmod(temporary.name, 0o600)
            json.dump(config, temporary, ensure_ascii=False, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = temporary.name
        os.replace(temporary_path, config_path)
    return config


if __name__ == "__main__":
    ensure_config(pathlib.Path(sys.argv[1]), os.environ)
    print("[entrypoint] 配置校验通过")
