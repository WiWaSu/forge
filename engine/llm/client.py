from __future__ import annotations

import http.client
import json
import select
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

def _default_host() -> str:
    import os
    raw = (os.environ.get("OLLAMA_HOST") or "").strip()
    if not raw:
        return "http://127.0.0.1:11434"
    if "://" not in raw:
        raw = "http://" + raw
    if raw.count(":") == 1:
        raw += ":11434"
    return raw.replace("0.0.0.0", "127.0.0.1").rstrip("/")


DEFAULT_HOST = _default_host()
DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"


REQUEST_TIMEOUT = 900

MAX_CTX = 8192
CTX_STEPS = (2048, 4096, 8192)
CHARS_PER_TOKEN = 2
ANSWER_TOKENS = 1024
MIN_ANSWER_TOKENS = 256
MAX_ANSWER_TOKENS = 4096
MAX_THINKING_TOKENS = 8192

OOM_MARKS = ("out of memory", "out-of-memory", "cudamalloc", "failed to allocate",
             "insufficient memory", "not enough memory")


def _is_out_of_memory(text: str) -> bool:
    low = (text or "").lower()
    return any(mark in low for mark in OOM_MARKS)


class LLMError(RuntimeError):
    pass


STOP_POLL_SECONDS = 0.5
STOPPED_TEXT = "Построение остановлено кнопкой"


class LLMCancelled(LLMError):
    pass


_THINKING: dict[str, bool] = {}


@dataclass
class OllamaClient:

    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    temperature: float = 0.2
    num_ctx: int = MAX_CTX
    timeout: int = REQUEST_TIMEOUT
    think: Any = None
    options: dict[str, Any] = field(default_factory=dict)
    prompt_notes: list[str] = field(default_factory=list)

    def chat(self, messages: list[dict[str, str]],
             should_stop: Optional[Callable[[], bool]] = None) -> str:
        size = self._context_for(messages)
        if should_stop is None:
            ask = self._ask
        else:
            def ask(m: list[dict[str, str]], s: int) -> str:
                return self._ask_stoppable(m, s, should_stop)
        try:
            return ask(messages, size)
        except LLMError as exc:
            smaller = size // 2
            if (isinstance(exc, LLMCancelled) or not _is_out_of_memory(str(exc))
                    or smaller < self._need(messages, MIN_ANSWER_TOKENS)):
                raise
            return ask(messages, smaller)

    def _need(self, messages: list[dict[str, str]], reserve: int) -> int:
        chars = sum(len(m.get("content") or "") for m in self._with_notes(messages))
        return chars // CHARS_PER_TOKEN + reserve

    def _context_for(self, messages: list[dict[str, str]]) -> int:
        need = self._need(messages, ANSWER_TOKENS)
        for size in CTX_STEPS:
            if size >= need:
                return min(size, self.num_ctx)
        return self.num_ctx

    def _with_notes(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        if not self.prompt_notes:
            return messages
        notes = "\n".join(f"- {n}" for n in self.prompt_notes)
        out, done = [], False
        for m in messages:
            if not done and m.get("role") == "system":
                m = {**m, "content": f"{m.get('content', '')}\n\nДОПОЛНИТЕЛЬНО\n{notes}"}
                done = True
            out.append(m)
        if not done:
            out.insert(0, {"role": "system", "content": f"ДОПОЛНИТЕЛЬНО\n{notes}"})
        return out

    def max_tokens(self) -> int:
        thinks = self.think if self.think is not None else _THINKING.get(self.model, False)
        return MAX_THINKING_TOKENS if thinks else MAX_ANSWER_TOKENS

    def _content(self, body: dict[str, Any], num_ctx: int) -> str:
        if body.get("done_reason") == "length":
            count = body.get("eval_count") or 0
            if count >= self.max_tokens():
                raise LLMError(f"Ответ модели оборван на {count} токенах: модель не остановилась сама — "
                               f"так пишут слишком маленькие модели")
            raise LLMError(f"Ответ модели оборван на {count} токенах: кончилось окно в {num_ctx} токенов — "
                           f"рассуждение или ответ в него не поместились")
        content = (body.get("message") or {}).get("content")
        if not content:
            raise LLMError(f"Пустой ответ модели: {body}")
        return content

    def _payload(self, messages: list[dict[str, str]], num_ctx: int) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": self._with_notes(messages),
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self.temperature,
                "num_ctx": num_ctx,
                "num_predict": self.max_tokens(),
                **self.options,
            },
            **({"think": self.think} if self.think is not None else {}),
        }

    def _ask_stoppable(self, messages: list[dict[str, str]], num_ctx: int,
                       should_stop: Callable[[], bool]) -> str:
        if should_stop():
            raise LLMCancelled(STOPPED_TEXT)
        url = urllib.parse.urlsplit(self.host)
        conn = http.client.HTTPConnection(
            url.hostname or "127.0.0.1", url.port or 11434, timeout=self.timeout)
        body = json.dumps(self._payload(messages, num_ctx)).encode("utf-8")
        try:
            try:
                conn.request("POST", "/api/chat", body=body,
                             headers={"Content-Type": "application/json"})
                deadline = time.monotonic() + self.timeout
                while True:
                    if should_stop():
                        self._hang_up(conn)
                        raise LLMCancelled(STOPPED_TEXT)
                    if time.monotonic() > deadline:
                        raise socket.timeout("timed out")
                    ready, _, _ = select.select([conn.sock], [], [], STOP_POLL_SECONDS)
                    if ready:
                        break
                response = conn.getresponse()
                status, raw = response.status, response.read()
            except LLMCancelled:
                raise
            except (socket.timeout, TimeoutError) as exc:
                raise LLMError(
                    f"Ollama не ответила за {self.timeout // 60} мин. Модель слишком "
                    f"медленная для этой машины — попробуйте режим «по частям» "
                    f"или модель меньше"
                ) from exc
            except (OSError, http.client.HTTPException) as exc:
                raise LLMError(
                    f"Ollama недоступна по адресу {self.host}: {exc}. "
                    f"Проверь, что сервис запущен (ollama serve) и модель загружена "
                    f"(ollama pull {self.model})"
                ) from exc
        finally:
            conn.close()

        if status != 200:
            raise LLMError(self._refusal_text(int(status or 0), raw or b""))
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LLMError(f"Ollama вернула не JSON: {exc}") from exc
        return self._content(data, num_ctx)

    @staticmethod
    def _hang_up(conn: http.client.HTTPConnection) -> None:
        sock = conn.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        conn.close()

    def _ask(self, messages: list[dict[str, str]], num_ctx: int) -> str:
        payload = self._payload(messages, num_ctx)
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LLMError(self._http_error(exc)) from exc
        except urllib.error.URLError as exc:
            raise LLMError(
                f"Ollama недоступна по адресу {self.host}: {exc}. "
                f"Проверь, что сервис запущен (ollama serve) и модель загружена "
                f"(ollama pull {self.model})"
            ) from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ollama вернула не JSON: {exc}") from exc
        return self._content(body, num_ctx)

    def _http_error(self, exc: urllib.error.HTTPError) -> str:
        with exc:
            try:
                raw = exc.read()
            except OSError:
                raw = b""
        return self._refusal_text(exc.code, raw)

    def _refusal_text(self, code: int, raw: bytes) -> str:
        try:
            detail = json.loads(raw.decode("utf-8")).get("error", "")
        except (ValueError, AttributeError):
            detail = ""
        text = f"Ollama ответила отказом {code}"
        if detail:
            text += f": {detail}"
        if code == 404:
            text += (
                f". Чаще всего это значит, что модели '{self.model}' нет на диске — "
                f"сверь имя с выводом 'ollama list' или выполни 'ollama pull {self.model}'"
            )
        if _is_out_of_memory(detail):
            text = (
                "Не хватило видеопамяти для модели. Закройте программы, которые занимают "
                "видеокарту (игры, видео, второй запуск программы), или подождите пару минут, "
                "пока Ollama выгрузит предыдущую модель. Помогает и модель меньшего размера. "
                "Ответ Ollama: " + detail
            )
        return text

    def supports_thinking(self) -> bool | None:
        if self.model in _THINKING:
            return _THINKING[self.model]
        request = urllib.request.Request(
            f"{self.host}/api/show",
            data=json.dumps({"model": self.model}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                caps = json.loads(response.read().decode("utf-8")).get("capabilities")
        except (OSError, ValueError, AttributeError):
            return None
        if not isinstance(caps, list):
            return None
        _THINKING[self.model] = "thinking" in caps
        return _THINKING[self.model]

    def unload(self) -> bool:
        payload = {"model": self.model, "keep_alive": 0}
        request = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10):
                return True
        except (OSError, ValueError):
            return False

    def status(self) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=10) as response:
                tags = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            return {
                "running": False, "ok": False, "models": [],
                "message": f"Ollama не отвечает по адресу {self.host}: {exc}",
            }

        models = self._parse_tags(tags)
        names = [m["name"] for m in models]
        if not names:
            ok, message = False, "Ollama запущена, но ни одной модели не загружено"
        elif not any(self._matches(n) for n in names):
            ok, message = False, (
                f"Модели '{self.model}' нет. Загружены: {', '.join(names)}. "
                f"Выполни: ollama pull {self.model}"
            )
        else:
            ok, message = True, f"Ollama доступна, модель {self.model} загружена"
        return {"running": True, "ok": ok, "models": models, "message": message}

    def installed(self) -> list[str]:
        return [m["name"] for m in self.installed_details()]

    def installed_details(self) -> list[dict[str, Any]]:
        return self.status()["models"]

    @staticmethod
    def _parse_tags(tags: dict[str, Any]) -> list[dict[str, Any]]:
        models = []
        for m in tags.get("models", []):
            name = m.get("name")
            if not name:
                continue
            details = m.get("details") or {}
            models.append({
                "name": name,
                "size_gb": round(float(m.get("size") or 0) / 1_000_000_000, 1),
                "parameters": details.get("parameter_size", ""),
                "family": details.get("family", ""),
            })
        return sorted(models, key=lambda m: m["name"])

    def _matches(self, name: str) -> bool:
        if name == self.model:
            return True
        return ":" not in self.model and name == f"{self.model}:latest"

    def available(self) -> tuple[bool, str]:
        s = self.status()
        return s["ok"], s["message"]


@dataclass
class TrackedClient:

    inner: Any
    job: Any

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.job.request_begin()
        status = "ошибка"
        try:
            text = self.inner.chat(messages)
            status = "ок"
            return text
        except LLMCancelled:
            status = "стоп"
            raise
        finally:
            self.job.request_end(status)


@dataclass
class StoppableClient:

    inner: Any
    should_stop: Callable[[], bool]

    def chat(self, messages: list[dict[str, str]]) -> str:
        if self.should_stop():
            raise LLMCancelled(STOPPED_TEXT)
        if isinstance(self.inner, OllamaClient):
            return self.inner.chat(messages, should_stop=self.should_stop)
        return self.inner.chat(messages)
