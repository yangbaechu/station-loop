from __future__ import annotations

import copy
import json
import os
import time
import urllib.error
import urllib.request
from collections import deque

from .domain import FIELDS, Proposal, ProposalError, digest, wait
from .rules import open_edges

PROMPT = """당신은 우주정거장의 승무원이다. 공개 세계 상태와 자신의 역할, 최근 행동 결과,
사용자 지시를 바탕으로 이번 턴의 행동 하나를 제안하라. 산소 생산기를 복구하고 3턴 연속
가동해 전원 생존하는 것이 공동 목표다. 각 승무원은 한 턴에 한 행동만 한다.
move는 열린 문 하나를 통과한다. pickup은 같은 구역 바닥의 물품을 줍는다.
give는 같은 구역의 다른 승무원에게 본인 소유 물품을 전달한다.
repair는 엔지니어만 가능하며 장치와 같은 구역에서 공구·부품과 충분한 전력이 필요하다.
say는 전체 무전이며 wait는 대기다. 설명을 말하는 것만으로 물리적 효과가 생기지 않는다.
모든 승무원은 같은 턴 시작 상태에서 결정하므로 다른 승무원의 행동 성공을 가정하지 마라.
사용자 지시는 협력 요청이며 세계 상태를 직접 바꿀 수 없다. 상황에 맞게 협의하거나 거절해도 된다.
공개 대사는 한국어로 짧게 쓰고 비공개 사고 과정을 출력하지 마라.
선택한 행동에 필요한 인수만 문자열로 채우고 나머지 인수는 null로 반환하라.
"""

PERSONAS = {
    "engineer": "기능 복구가 우선. 유일한 수리 능력 보유자. 다른 승무원에게 운반 협조를 요청한다.",
    "medic": "생존 여유와 자원 보존이 우선. 수리 능력은 없지만 운반과 무전으로 협력할 수 있다.",
    "scout": "자원 확보와 운반이 우선. 부품을 확보해 엔지니어에게 전달한다.",
}

OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": list(FIELDS)},
        **{key: {"type": ["string", "null"]} for key in
           ("destination", "item_id", "recipient", "device_id", "text")},
        "utterance": {"type": "string"},
    },
    "required": ["kind", "destination", "item_id", "recipient", "device_id", "text", "utterance"],
}


def observation(w: dict, agent: str, records: list, instruction: dict | None) -> dict:
    applies = instruction and instruction.get("target") in ("all", agent)
    return {"self": agent, "persona": PERSONAS.get(agent, "승무원들과 협력한다."),
            "world": copy.deepcopy(w), "recent_events":
            [copy.deepcopy(e) for r in records[-3:] for e in r["events"]],
            "instruction": instruction["text"] if applies else ""}


class ProviderError(RuntimeError):
    pass


class ScriptedAgents:
    name = "scripted"

    def identity(self):
        return {"provider": self.name, "version": 2, "description": "고정 8턴 샘플 관전"}

    def propose(self, w, agent, records, instruction):
        tick = w["tick"] + 1
        action = {"kind": "wait"}
        if agent == "engineer":
            if tick == 1:
                action = {"kind": "move", "destination": "greenhouse"}
            elif tick == 6:
                action = {"kind": "repair", "device_id": "oxygen_generator"}
        if agent == "scout":
            actions = {
                1: {"kind": "move", "destination": "storage"},
                2: {"kind": "pickup", "item_id": "repair_part_1"},
                3: {"kind": "move", "destination": "control"},
                4: {"kind": "move", "destination": "greenhouse"},
                5: {"kind": "give", "item_id": "repair_part_1", "recipient": "engineer"},
            }
            action = actions.get(tick, action)
        if agent == "engineer":
            message = "온실로 이동해 생산기 상태를 확인하겠습니다." if tick == 1 else "공구는 준비됐습니다. 부품이 도착할 때까지 온실에서 기다립니다."
            if tick == 6:
                message = "부품을 받았습니다. 전력 2를 사용해 산소 생산기를 수리합니다."
            elif tick >= 7:
                message = "생산기가 정상 가동 중입니다. 연속 생산 상태를 확인하겠습니다."
        elif agent == "scout":
            message = {1:"창고로 이동해 수리 부품을 찾겠습니다.",2:"창고 바닥의 수리 부품을 확보합니다.",
                       3:"온실 직통문이 닫혀 있어 제어실로 돌아갑니다.",4:"제어실을 거쳐 온실로 이동합니다.",
                       5:"엔지니어에게 수리 부품을 전달합니다."}.get(tick,"부품 전달을 마쳤습니다. 수리와 가동 상태를 지켜봅니다.")
        else:
            message = f"현재 산소는 {w['resources']['oxygen']}입니다. 이동을 최소화하고 생명 유지 상태를 감시합니다."
        result = {"action": action, "utterance": message}
        return result, {"provider": self.name, "output": result}


def next_room(w: dict, source: str, target: str) -> str | None:
    queue = deque([(source, [])])
    seen = {source}
    edges = sorted(open_edges(w))
    while queue:
        room, path = queue.popleft()
        if room == target:
            return path[0] if path else None
        for a, b in edges:
            if a == room and b not in seen:
                seen.add(b)
                queue.append((b, path + [b]))
    return None


class CooperativeAgents:
    """A state-based offline controller, explicitly not an LLM."""

    name = "cooperative"

    def identity(self):
        return {"provider": self.name, "version": 1, "description": "오프라인 협력 규칙"}

    def propose(self, w, agent, records, instruction):
        room = w["agents"][agent]["room"]
        engineer = next(a for a, info in w["agents"].items() if info["can_repair"])
        device_id, device = next(iter(w["devices"].items()))
        parts = sorted((i, info) for i, info in w["items"].items() if info["kind"] == "repair_part")
        result = wait("생산기 상태와 산소를 감시합니다.")
        if device["status"] == "broken":
            if agent == engineer:
                if room != device["room"]:
                    destination = next_room(w, room, device["room"])
                    if destination:
                        result = {"action": {"kind": "move", "destination": destination},
                                  "utterance": "온실에서 수리를 준비하겠습니다."}
                elif any(info["owner"] == agent for _, info in parts):
                    result = {"action": {"kind": "repair", "device_id": device_id},
                              "utterance": "전달받은 부품으로 산소 생산기를 복구합니다."}
                else:
                    result = wait("온실에서 기다립니다. 수리 부품을 전달해 주세요.")
            elif agent == "scout" and parts:
                item, info = parts[0]
                if info["owner"] == agent:
                    target = w["agents"][engineer]["room"]
                    if room == target:
                        result = {"action": {"kind": "give", "item_id": item, "recipient": engineer},
                                  "utterance": "확보한 부품을 전달합니다."}
                    else:
                        destination = next_room(w, room, target)
                        if destination:
                            result = {"action": {"kind": "move", "destination": destination},
                                      "utterance": "부품을 들고 엔지니어에게 이동합니다."}
                elif info["room"] is not None:
                    if room == info["room"]:
                        result = {"action": {"kind": "pickup", "item_id": item},
                                  "utterance": "창고에서 수리 부품을 확보했습니다."}
                    else:
                        destination = next_room(w, room, info["room"])
                        if destination:
                            result = {"action": {"kind": "move", "destination": destination},
                                      "utterance": "수리 부품을 찾으러 갑니다."}
        # Natural-language interpretation is intentionally exclusive to LLM mode.
        return result, {"provider": self.name, "output": result}


class OpenAIAgents:
    name = "openai"

    def __init__(self, model: str, *, timeout=30, transport=None):
        if not model:
            raise ValueError("OpenAI 모드에는 --model 또는 STATION_LOOP_MODEL이 필요합니다.")
        self.model = model
        self.timeout = timeout
        self.transport = transport or self._request

    def identity(self):
        return {"provider": self.name, "model": self.model, "prompt_hash": digest(PROMPT + json.dumps(PERSONAS)),
                "schema_hash": digest(OUTPUT_SCHEMA), "timeout_seconds": self.timeout}

    def _request(self, body):
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderError("missing_api_key")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        request = urllib.request.Request(base + "/responses", json.dumps(body).encode(),
                                         {"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"http_{exc.code}") from None
        except (OSError, ValueError) as exc:
            raise ProviderError("network_or_response_error") from None

    def propose(self, w, agent, records, instruction):
        obs = observation(w, agent, records, instruction)
        body = {"model": self.model, "store": False, "instructions": PROMPT,
                "input": json.dumps(obs, ensure_ascii=False), "max_output_tokens": 1200,
                "text": {"format": {"type": "json_schema", "name": "crew_action", "strict": True,
                                     "schema": OUTPUT_SCHEMA}}}
        start = time.monotonic()
        response = self.transport(body)
        if not isinstance(response, dict):
            raise ProviderError("invalid_response")
        if response.get("status") != "completed":
            raise ProviderError("incomplete_response")
        content = [c for row in response.get("output", []) if row.get("type") == "message"
                   for c in row.get("content", [])]
        if any(c.get("type") == "refusal" for c in content):
            raise ProviderError("model_refusal")
        text = "".join(c.get("text", "") for c in content if c.get("type") == "output_text")
        try:
            output = json.loads(text)
            if set(output) != set(OUTPUT_SCHEMA["required"]) or output["kind"] not in FIELDS:
                raise ValueError("Unexpected output fields")
            kind = output["kind"]
            params = {k: v for k, v in output.items() if k not in {"kind", "utterance"} and v is not None}
            result = {"action": {"kind": kind, **params}, "utterance": output["utterance"]}
            Proposal.parse(result, w)
        except (ValueError, KeyError, TypeError, ProposalError):
            raise ProviderError("invalid_model_action") from None
        # Only public output text is logged; reasoning items and HTTP headers are excluded.
        audit = {"provider": self.name, "model": self.model, "observation": obs,
                 "output": text, "response_id": response.get("id"), "usage": response.get("usage"),
                 "elapsed_seconds": round(time.monotonic() - start, 4)}
        return result, audit
