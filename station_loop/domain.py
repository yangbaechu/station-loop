from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from . import ROOT


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def initial_world(scenario: dict | None = None) -> dict:
    if scenario is None:
        scenario = json.loads((ROOT / "scenarios/life_support.json").read_text())
    s = copy.deepcopy(scenario)
    world = {k: s[k] for k in ("id", "title", "max_ticks", "rooms", "doors", "resources",
                               "rules", "initiative_order")}
    for key in ("agents", "items", "devices"):
        rows = s[key]
        if len({r["id"] for r in rows}) != len(rows):
            raise ValueError(f"Duplicate {key} IDs")
        world[key] = {row["id"]: row for row in rows}
    world.update(tick=0, revision=0, production_streak=0, status="running", termination=None)
    validate_world(world)
    return world


def validate_world(w: dict) -> None:
    rooms = set(w["rooms"])
    agents = w["agents"]
    if not rooms or len(rooms) != len(w["rooms"]) or not agents:
        raise ValueError("Invalid room/agent domain")
    if len(w["initiative_order"]) != len(agents) or set(w["initiative_order"]) != set(agents):
        raise ValueError("Invalid initiative order")
    for a in agents.values():
        if a["room"] not in rooms or type(a["can_repair"]) is not bool:
            raise ValueError("Invalid agent state")
    for d in w["doors"]:
        if len(d["rooms"]) != 2 or len(set(d["rooms"])) != 2 or not set(d["rooms"]) <= rooms:
            raise ValueError("Invalid door")
        if type(d["open"]) is not bool:
            raise ValueError("Invalid door status")
    for i in w["items"].values():
        if (i["owner"] is None) == (i["room"] is None):
            raise ValueError("An item must have exactly one location")
        if i["owner"] is not None and i["owner"] not in agents:
            raise ValueError("Unknown owner")
        if i["room"] is not None and i["room"] not in rooms:
            raise ValueError("Unknown item location")
    for d in w["devices"].values():
        if d["room"] not in rooms or d["status"] not in {"broken", "online"}:
            raise ValueError("Invalid device state")
    for k, v in w["resources"].items():
        if type(v) is not int or (k != "oxygen" and v < 0):
            raise ValueError("Invalid resource")
    if w["resources"]["oxygen"] > w["resources"]["oxygen_capacity"]:
        raise ValueError("Oxygen exceeds capacity")
    for k, v in w["rules"].items():
        if type(v) is not int or v <= 0:
            raise ValueError(f"Invalid rule constant: {k}")
    if type(w["max_ticks"]) is not int or w["max_ticks"] <= 0:
        raise ValueError("Invalid turn limit")


class ProposalError(ValueError):
    pass


FIELDS = {
    "move": {"destination"}, "pickup": {"item_id"},
    "give": {"item_id", "recipient"}, "repair": {"device_id"},
    "say": {"text"}, "wait": set(),
}


@dataclass(frozen=True)
class Proposal:
    action: dict
    utterance: str = ""

    @classmethod
    def parse(cls, payload: Any, world: dict) -> Proposal:
        if not isinstance(payload, dict) or set(payload) - {"action", "utterance"}:
            raise ProposalError("invalid_envelope")
        action = payload.get("action")
        if not isinstance(action, dict) or not isinstance(action.get("kind"), str):
            raise ProposalError("invalid_action")
        kind = action["kind"]
        if kind not in FIELDS or set(action) != FIELDS[kind] | {"kind"}:
            raise ProposalError("invalid_fields")
        for key in FIELDS[kind]:
            if not isinstance(action[key], str) or not action[key] or len(action[key]) > 400:
                raise ProposalError("invalid_value")
        utterance = payload.get("utterance", "")
        if not isinstance(utterance, str) or len(utterance) > 240:
            raise ProposalError("invalid_utterance")
        for field, domain in (("destination", "rooms"), ("item_id", "items"),
                              ("device_id", "devices"), ("recipient", "agents")):
            if field in action and action[field] not in world[domain]:
                raise ProposalError("unknown_" + field)
        return cls(copy.deepcopy(action), utterance)


def wait(message: str = "") -> dict:
    return {"action": {"kind": "wait"}, "utterance": message}
