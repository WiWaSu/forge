from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from engine.llm.client import LLMError


@dataclass
class MockClient:

    responses: list[Any]
    calls: list[list[dict[str, str]]] = field(default_factory=list)

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if not self.responses:
            raise LLMError("У заглушки закончились заготовленные ответы")
        item = self.responses.pop(0)
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
