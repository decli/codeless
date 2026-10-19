"""界面上的时间线：一条条记录（用户消息、回答、工具调用、检查点……）加上会话状态。

前端先拉一次快照，再订阅增量操作。每个操作带递增序号，断线重连时从序号续传；
序号太旧（缓冲区里已经没有了）就让前端重新拉快照。
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_OPS = 20_000
MAX_TOOL_OUTPUT = 24_000  # 界面上单个工具输出保留的字符数，超出只留末尾


class Timeline:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.items: list[dict] = []
        self.index: dict[str, dict] = {}
        self.state: dict[str, Any] = {}
        self.seq = 0
        self.counter = 0
        self.ops: deque[dict] = deque(maxlen=MAX_OPS)
        self.lock = threading.RLock()
        self.dirty = False
        self.saved_at = 0.0
        self.listeners: set[Callable[[], None]] = set()  # 有新操作时通知推送连接
        self._load()

    # --- 持久化 ---

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.items = data.get("items", [])
        self.state = data.get("state", {})
        self.counter = data.get("counter", len(self.items))
        self.seq = data.get("seq", 0)
        for item in self.items:
            self.index[item["id"]] = item
            # 上次进程退出时还在流式输出或运行的记录，标记为中断
            if item.get("streaming"):
                item["streaming"] = False
            if item.get("status") == "running":
                item["status"] = "interrupted"

    def save(self, force: bool = False) -> None:
        with self.lock:
            if not self.dirty and not force:
                return
            if not force and time.time() - self.saved_at < 2:
                return
            payload = {
                "items": self.items,
                "state": {k: v for k, v in self.state.items() if not k.startswith("_")},
                "counter": self.counter,
                "seq": self.seq,
            }
            self.dirty = False
            self.saved_at = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # --- 操作 ---

    def _emit(self, op: dict) -> None:
        self.seq += 1
        op["seq"] = self.seq
        self.ops.append(op)
        self.dirty = True
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:
                self.listeners.discard(listener)

    def add(self, kind: str, **fields: Any) -> dict:
        with self.lock:
            self.counter += 1
            item = {"id": f"i{self.counter}", "kind": kind, "at": time.time(), **fields}
            self.items.append(item)
            self.index[item["id"]] = item
            self._emit({"op": "add", "item": dict(item)})
            return item

    def append(self, item_id: str, field: str, text: str) -> None:
        if not text:
            return
        with self.lock:
            item = self.index.get(item_id)
            if item is None:
                return
            value = (item.get(field) or "") + text
            trimmed = field == "output" and len(value) > MAX_TOOL_OUTPUT
            if trimmed:
                value = "…\n" + value[-MAX_TOOL_OUTPUT:]
            item[field] = value
            if trimmed:
                self._emit({"op": "update", "id": item_id, "patch": {field: value}})
            else:
                self._emit({"op": "append", "id": item_id, "field": field, "text": text})

    def update(self, item_id: str, **patch: Any) -> None:
        with self.lock:
            item = self.index.get(item_id)
            if item is None:
                return
            item.update(patch)
            self._emit({"op": "update", "id": item_id, "patch": patch})

    def set_state(self, **patch: Any) -> None:
        with self.lock:
            self.state.update(patch)
            public = {k: v for k, v in patch.items() if not k.startswith("_")}
            if public:
                self._emit({"op": "state", "patch": public})

    def get(self, item_id: str) -> dict | None:
        return self.index.get(item_id)

    # --- 读取 ---

    def snapshot(self, limit: int = 1500) -> dict:
        with self.lock:
            items = self.items[-limit:]
            return {
                "items": json.loads(json.dumps(items, ensure_ascii=False)),
                "state": {k: v for k, v in self.state.items() if not k.startswith("_")},
                "seq": self.seq,
                "truncated": len(self.items) > len(items),
            }

    def ops_after(self, seq: int) -> list[dict] | None:
        """seq 之后的操作；缓冲区里已经没有那么早的了返回 None，前端应重拉快照。"""
        with self.lock:
            if seq >= self.seq:
                return []
            if not self.ops or self.ops[0]["seq"] > seq + 1:
                return None
            return [op for op in self.ops if op["seq"] > seq]
