from __future__ import annotations

import copy
import json

from .agents import OpenAIAgents
from .chatgpt_auth import AuthError, RESOURCE, remote_error


def read_completed(response):
    """Consume an SSE terminal event; partial text is never a successful action."""
    chunks, event_name, total = [], "", 0
    while True:
        raw = response.readline(65537)
        if not raw:
            break
        total += len(raw)
        if len(raw) > 65536 or total > 16_000_000:
            raise AuthError("interrupted_stream", status=502)
        try:
            line = raw.decode("utf-8").rstrip("\r\n")
        except UnicodeError:
            raise AuthError("interrupted_stream", status=502) from None
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            chunks.append(line[5:].lstrip(" "))
        elif line == "" and chunks:
            data = "\n".join(chunks)
            chunks = []
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
                kind = event.get("type", event_name)
                event_name = ""
                if kind == "response.completed":
                    result = event["response"]
                    if result.get("status") != "completed":
                        raise AuthError("interrupted_stream", status=502)
                    return result
                if kind == "response.failed":
                    error = event.get("response", {}).get("error", {})
                    raise remote_error(502, {"error": error})
                if kind == "error":
                    raise remote_error(502, {"error": event})
                if kind == "response.incomplete":
                    raise AuthError("interrupted_stream", status=502)
            except (ValueError, AttributeError, KeyError):
                raise AuthError("interrupted_stream", status=502) from None
    raise AuthError("interrupted_stream", status=502)


class ChatGPTAgents(OpenAIAgents):
    name = "chatgpt"

    def __init__(self, auth, model=""):
        self.auth = auth
        self.model = model
        self.timeout = 30
        self.transport = self._request

    def identity(self):
        return {**super().identity(), "billing": "chatgpt_plan", "account": self.auth.status()["active"]}

    def propose(self, *args, **kwargs):
        if not self.model:
            raise AuthError("model_required", status=409)
        if self.model not in {m["slug"] for m in self.auth.models()}:
            raise AuthError("model_required", status=409)
        return super().propose(*args, **kwargs)

    def _request(self, body):
        request = copy.deepcopy(body)
        request.pop("max_output_tokens", None)
        request["stream"] = True
        request["store"] = False
        request["input"] = [{"role": "user", "content": request["input"]}]
        try:
            with self.auth.http.open(RESOURCE + "/responses", body=request,
                                     token=self.auth.access_token(), accept="text/event-stream") as response:
                content_type = response.headers.get("Content-Type", "")
                if not content_type.lower().startswith("text/event-stream"):
                    raise AuthError("interrupted_stream", status=502)
                return read_completed(response)
        except AuthError as exc:
            self.auth.mark_error(exc)
            raise
        except OSError:
            raise AuthError("interrupted_stream", status=502) from None
