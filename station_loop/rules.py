from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.metadata import version

from . import ROOT
from .domain import Proposal, digest

RULE_PATH = ROOT / "rules/eligibility.dl"

REASONS = {
    "door_closed": "목적지로 직접 통과할 수 있는 열린 문이 없습니다.",
    "item_not_here": "이 구역 바닥에 해당 물품이 없습니다.",
    "not_owner": "해당 물품을 소지하고 있지 않습니다.",
    "not_colocated": "상대 또는 장치와 같은 구역에 있어야 합니다.",
    "same_recipient": "자기 자신에게 물품을 전달할 수 없습니다.",
    "wrong_role": "수리 능력이 없는 승무원입니다.",
    "missing_tool": "수리 공구가 필요합니다.",
    "missing_part": "수리 부품이 부족합니다.",
    "insufficient_power": "수리에 필요한 전력이 부족합니다.",
    "already_online": "이미 복구된 장치입니다.",
    "provider_error": "모델 호출 또는 구조화된 응답 처리에 실패해 이번 턴은 대기합니다.",
}


class EngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: tuple[str, ...] = ()


def open_edges(w: dict) -> set[tuple[str, str]]:
    result = set()
    for door in w["doors"]:
        if door["open"]:
            a, b = door["rooms"]
            result.update(((a, b), (b, a)))
    return result


class ReferenceJudge:
    """Explicit offline Python backend, never advertised as Wirelog."""

    name = "reference"

    def identity(self) -> dict:
        return {"backend": self.name, "implementation": digest((ROOT / "station_loop/rules.py").read_text())}

    def reachable(self, w: dict) -> set[tuple[str, str]]:
        edges = open_edges(w)
        closure = set(edges)
        while True:
            extra = {(a, c) for a, b in closure for x, c in edges if b == x}
            if extra <= closure:
                return closure
            closure |= extra

    def decide(self, w: dict, agent: str, proposal: Proposal) -> Decision:
        a = proposal.action
        kind = a["kind"]
        room = w["agents"][agent]["room"]
        owned = [i for i in w["items"].values() if i["owner"] == agent]
        reasons = set()
        if kind == "move" and (room, a["destination"]) not in open_edges(w):
            reasons.add("door_closed")
        elif kind == "pickup" and w["items"][a["item_id"]]["room"] != room:
            reasons.add("item_not_here")
        elif kind == "give":
            if w["items"][a["item_id"]]["owner"] != agent:
                reasons.add("not_owner")
            if w["agents"][a["recipient"]]["room"] != room:
                reasons.add("not_colocated")
            if a["recipient"] == agent:
                reasons.add("same_recipient")
        elif kind == "repair":
            device = w["devices"][a["device_id"]]
            if not w["agents"][agent]["can_repair"]:
                reasons.add("wrong_role")
            if device["room"] != room:
                reasons.add("not_colocated")
            if not any(i["kind"] == "tool" for i in owned):
                reasons.add("missing_tool")
            if sum(i["kind"] == "repair_part" for i in owned) < w["rules"]["repair_part_cost"]:
                reasons.add("missing_part")
            if w["resources"]["power"] < w["rules"]["repair_power_cost"]:
                reasons.add("insufficient_power")
            if device["status"] != "broken":
                reasons.add("already_online")
        return Decision(not reasons, tuple(sorted(reasons)))


def facts(w: dict, agent: str | None = None, proposal: Proposal | None = None) -> str:
    rows = []

    def emit(relation, *values):
        encoded = ",".join(json.dumps(v, ensure_ascii=True) for v in values)
        rows.append(f"{relation}({encoded}).")

    for a, info in w["agents"].items():
        emit("agent_at", a, info["room"])
        if info["can_repair"]:
            emit("repairer", a)
        # Count host-owned parts, not number of derivations in the engine.
        count = sum(i["owner"] == a and i["kind"] == "repair_part" for i in w["items"].values())
        emit("part_count", a, count)
    for a, b in sorted(open_edges(w)):
        emit("open_edge", a, b)
    for i, info in w["items"].items():
        emit("item_kind", i, info["kind"])
        if info["owner"] is not None:
            emit("owns", info["owner"], i)
        else:
            emit("item_at", i, info["room"])
    for d, info in w["devices"].items():
        emit("device_at", d, info["room"])
        if info["status"] == "broken":
            emit("broken", d)
    emit("power", w["resources"]["power"])
    emit("repair_cost", w["rules"]["repair_power_cost"])
    emit("part_cost", w["rules"]["repair_part_cost"])
    if proposal is not None:
        a = proposal.action
        emit("candidate", "proposal", agent, a["kind"],
             a.get("destination", a.get("device_id", "")), a.get("item_id", ""), a.get("recipient", ""))
    return "\n".join(rows)


class WirelogJudge:
    name = "wirelog"

    def __init__(self):
        try:
            from pyrewire import EasySession, wirelog_version
            self.session = EasySession
            self.source = RULE_PATH.read_text()
            self.info = {"backend": self.name, "pyrewire": version("pyrewire"),
                         "core": str(wirelog_version()), "rules": digest(self.source),
                         "adapter": digest((ROOT / "station_loop/rules.py").read_text())}
            # Fail at startup rather than during the first real game.
            self._evaluate(initial_probe(), None, None)
        except Exception as exc:
            raise EngineError("Wirelog 초기화 실패. PyreWire와 호환 코어 설치를 확인하세요. "
                              "오프라인 실행은 --rules reference를 명시하세요.") from exc

    def identity(self) -> dict:
        return self.info.copy()

    def _evaluate(self, w, agent, proposal):
        try:
            source = self.source + "\n" + facts(w, agent, proposal)
            # PyreWire 1.1.3 batch CSV output exposes numeric symbol IDs and
            # cannot export an empty relation. EasySession supports both cases.
            # Seed its public reverse-intern cache for inline facts and literals.
            symbols = {json.loads(token) for token in re.findall(r'"(?:[^"\\]|\\.)*"', source)}
            with self.session(source) as session:
                for symbol in sorted(symbols):
                    session.intern(symbol)
                return {name: set(session.snapshot(name)) for name in ("allowed", "blocked", "reachable")}
        except Exception as exc:
            raise EngineError("Wirelog 규칙 평가 실패. 턴을 확정하지 않았습니다.") from exc

    def decide(self, w, agent, proposal) -> Decision:
        result = self._evaluate(w, agent, proposal)
        reasons = tuple(sorted({str(row[1]) for row in result["blocked"] if row[0] == "proposal"}))
        allowed = ("proposal",) in result["allowed"]
        if allowed == bool(reasons):
            raise EngineError("Wirelog 판정 결과가 불완전하거나 모순됩니다.")
        return Decision(allowed, reasons)

    def reachable(self, w):
        return self._evaluate(w, None, None)["reachable"]


def initial_probe():
    from .domain import initial_world
    return initial_world()


def make_judge(name: str):
    if name == "reference":
        return ReferenceJudge()
    if name == "wirelog":
        return WirelogJudge()
    raise ValueError("Unknown rules backend")
