from __future__ import annotations

import copy
import threading

from .domain import Proposal, ProposalError, digest, initial_world, validate_world, wait


class Simulation:
    def __init__(self, judge, world=None, store=None):
        self.world = copy.deepcopy(world) if world is not None else initial_world()
        validate_world(self.world)
        self.judge = judge
        self.store = store
        self.lock = threading.RLock()
        self.records = []
        self.requests = {}

    def step(self, proposals: dict, request_id: str, *, instruction=None, audit=None, errors=None,
             expected_tick=None) -> dict:
        with self.lock:
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
                raise ValueError("Invalid request ID")
            if not isinstance(proposals, dict) or set(proposals) - set(self.world["agents"]):
                raise ValueError("Unknown proposal agent")
            errors = errors or {}
            fingerprint = digest({"proposals": proposals, "instruction": instruction, "errors": errors})
            if request_id in self.requests:
                prior = self.requests[request_id]
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("Request ID reused with different input")
                return copy.deepcopy(prior)
            if expected_tick is not None and expected_tick != self.world["tick"]:
                raise ValueError("다른 요청으로 턴이 이미 진행됐습니다. 상태를 새로고침하세요.")
            if self.world["status"] != "running":
                raise ValueError("종료된 게임입니다. 새 게임을 시작하세요.")
            before = copy.deepcopy(self.world)
            after = copy.deepcopy(before)
            tick = before["tick"] + 1
            order = before["initiative_order"]
            rotate = (tick - 1) % len(order)
            order = order[rotate:] + order[:rotate]
            # Parse all proposals against the common observation, before any effect.
            parsed = {}
            failures = {}
            for agent in order:
                try:
                    if agent in errors:
                        failures[agent] = "provider_error"
                        continue
                    parsed[agent] = Proposal.parse(proposals.get(agent, wait()), before)
                except ProposalError as exc:
                    failures[agent] = str(exc)
            events = []
            for agent in order:
                if agent in failures:
                    events.append({"agent": agent, "action": {"kind": "wait"}, "utterance": "",
                                   "allowed": False, "reasons": [failures[agent]],
                                   "provider_error": errors.get(agent), "observed_revision": before["revision"]})
                    continue
                proposal = parsed[agent]
                # An earlier actor might have consumed the referenced item.
                try:
                    Proposal.parse({"action": proposal.action, "utterance": proposal.utterance}, after)
                    decision = self.judge.decide(after, agent, proposal)
                    reasons, allowed = list(decision.reasons), decision.allowed
                except ProposalError as exc:
                    reasons, allowed = [str(exc)], False
                event = {"agent": agent, "action": proposal.action, "utterance": proposal.utterance,
                         "allowed": allowed, "reasons": reasons, "observed_revision": before["revision"]}
                if allowed:
                    self._apply(after, agent, proposal.action)
                    after["revision"] += 1
                    validate_world(after)
                events.append(event)
            environment = self._environment(after)
            after["tick"] = tick
            after["revision"] += 1
            if after["resources"]["oxygen"] <= 0:
                after.update(status="failed", termination="oxygen_depleted")
            elif after["production_streak"] >= after["rules"]["success_production_streak"]:
                after.update(status="won", termination="life_support_restored")
            elif tick >= after["max_ticks"]:
                after.update(status="failed", termination="time_limit")
            validate_world(after)
            record = {"tick": tick, "request_id": request_id, "fingerprint": fingerprint,
                      "instruction": instruction, "proposals": copy.deepcopy(proposals),
                      "errors": errors, "audit": audit or [], "initiative": order,
                      "events": events, "environment": environment,
                      "before_hash": digest(before), "after_hash": digest(after), "world": after}
            if self.store:
                self.store.commit(record)
            # Commit in memory only after durable storage succeeds.
            self.world = after
            self.records.append(record)
            self.requests[request_id] = record
            return copy.deepcopy(record)

    @staticmethod
    def _apply(w: dict, agent: str, a: dict):
        kind = a["kind"]
        if kind == "move":
            w["agents"][agent]["room"] = a["destination"]
        elif kind == "pickup":
            w["items"][a["item_id"]].update(owner=agent, room=None)
        elif kind == "give":
            w["items"][a["item_id"]].update(owner=a["recipient"], room=None)
        elif kind == "repair":
            parts = sorted(i for i, info in w["items"].items()
                           if info["owner"] == agent and info["kind"] == "repair_part")
            for item in parts[:w["rules"]["repair_part_cost"]]:
                del w["items"][item]
            w["resources"]["power"] -= w["rules"]["repair_power_cost"]
            w["devices"][a["device_id"]]["status"] = "online"

    @staticmethod
    def _environment(w: dict) -> dict:
        rules, resources = w["rules"], w["resources"]
        consumption = len(w["agents"]) * rules["oxygen_consumption_per_agent_per_tick"]
        production = 0
        for device in w["devices"].values():
            if device["status"] == "online" and resources["power"] >= rules["production_power_cost_per_tick"]:
                resources["power"] -= rules["production_power_cost_per_tick"]
                production += rules["oxygen_production_per_tick"]
        resources["oxygen"] = min(resources["oxygen_capacity"], resources["oxygen"] - consumption + production)
        w["production_streak"] = w["production_streak"] + 1 if production else 0
        return {"oxygen_consumed": consumption, "oxygen_produced": production}
