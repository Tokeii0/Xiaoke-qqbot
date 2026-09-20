from __future__ import annotations

import asyncio
import json
from pathlib import Path


class GroupStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._overrides: dict[str, bool] = self._read()

    def _read(self) -> dict[str, bool]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {str(key): bool(value) for key, value in data.items()}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def is_enabled(self, group_id: int) -> bool:
        return self._overrides.get(str(group_id), True)

    async def set_enabled(self, group_id: int, enabled: bool) -> None:
        async with self._lock:
            self._overrides[str(group_id)] = enabled
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(self._overrides, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp.replace(self.path)
