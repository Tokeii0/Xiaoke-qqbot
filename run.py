"""Start TencentDB Agent Memory Gateway and the NoneBot2 process together."""

from __future__ import annotations

import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent


def _load_environment() -> None:
    load_dotenv(ROOT / ".env", override=False)
    environment = os.getenv("ENVIRONMENT", "prod")
    load_dotenv(ROOT / f".env.{environment}", override=False)

    fallbacks = {
        "TDAI_LLM_API_KEY": "BOT_API_KEY",
        "TDAI_LLM_BASE_URL": "BOT_API_BASE_URL",
        "TDAI_LLM_MODEL": "BOT_MODEL",
    }
    for target, source in fallbacks.items():
        if not os.getenv(target):
            os.environ[target] = os.getenv(source, "")
    os.environ.setdefault("TDAI_GATEWAY_CONFIG", str(ROOT / "tdai-gateway.json"))


def _is_true(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _gateway_health() -> bool:
    base = os.getenv("MEMORY_GATEWAY_URL", "http://127.0.0.1:8420").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=1.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _start_gateway() -> tuple[subprocess.Popen[bytes] | None, object | None]:
    if not _is_true("MEMORY_AUTO_START", True) or _gateway_health():
        return None, None

    entry = (
        ROOT
        / "node_modules"
        / "@tencentdb-agent-memory"
        / "memory-tencentdb"
        / "src"
        / "gateway"
        / "server.ts"
    )
    if not entry.exists():
        raise RuntimeError("未安装记忆组件，请先在项目目录执行 npm install")

    log_dir = ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(log_dir / "memory-gateway.log", "ab", buffering=0)
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        ["node", "--import", "tsx", str(entry)],
        cwd=ROOT,
        env=os.environ.copy(),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )

    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log_file.close()
            raise RuntimeError(
                f"TencentDB Agent Memory Gateway 启动失败，日志：{log_dir / 'memory-gateway.log'}"
            )
        if _gateway_health():
            return process, log_file
        time.sleep(0.5)

    process.terminate()
    log_file.close()
    raise RuntimeError("等待 TencentDB Agent Memory Gateway 健康检查超时")


def main() -> None:
    _load_environment()
    gateway: subprocess.Popen[bytes] | None = None
    log_file: object | None = None
    try:
        gateway, log_file = _start_gateway()
        import nonebot  # noqa: E402
        from nonebot.adapters.onebot.v11 import Adapter  # noqa: E402

        nonebot.init()
        driver = nonebot.get_driver()
        driver.register_adapter(Adapter)
        nonebot.load_plugin("xiaoke_bot.plugin")
        nonebot.run()
    finally:
        if gateway is not None and gateway.poll() is None:
            gateway.terminate()
            try:
                gateway.wait(timeout=8)
            except subprocess.TimeoutExpired:
                gateway.kill()
        if log_file is not None:
            log_file.close()


if __name__ == "__main__":
    main()
