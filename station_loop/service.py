from __future__ import annotations

import copy
import threading

from .domain import initial_world, wait
from .agents import ProviderError, ScriptedAgents, CooperativeAgents
from .replay import RunStore
from .simulation import Simulation


class BusyError(ValueError):
    pass


class Game:
    def __init__(self, judge, provider, *, runs_root=None):
        self.judge, self.provider, self.runs_root = judge, provider, runs_root
        self.lock = threading.RLock()
        self.play_provider = None
        self.reset()

    def reset(self):
        with self.lock:
            world = initial_world()
            store = RunStore(world, self.judge, self.provider, self.runs_root)
            self.sim = Simulation(self.judge, world, store)
            self.inputs = {}

    def snapshot(self):
        with self.lock:
            return {"world": copy.deepcopy(self.sim.world), "records": copy.deepcopy(self.sim.records),
                    "run_id": self.sim.store.path.name, "judge": self.judge.identity(),
                    "provider": self.provider.identity()}

    def switch_provider(self, provider):
        with self.lock:
            previous = self.provider
            self.provider = provider
            try:
                self.reset()
            except Exception:
                self.provider = previous
                raise

    def switch_mode(self, mode):
        with self.lock:
            if mode == "sample":
                previous = self.provider if self.provider.name != "scripted" else self.play_provider
                self.switch_provider(ScriptedAgents())
                self.play_provider = previous
            elif mode == "play":
                self.switch_provider(self.play_provider or CooperativeAgents())
                self.play_provider = None
            else:
                raise ValueError("알 수 없는 플레이 모드입니다.")

    def advance(self, request_id, expected_tick, run_id, *, overrides=None, instruction=None):
        if not self.lock.acquire(blocking=False):
            raise BusyError("승무원들이 행동을 결정하고 있습니다. 잠시 기다려 주세요.")
        try:
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
                raise ValueError("Invalid request ID")
            if type(expected_tick) is not int:
                raise ValueError("Invalid turn number")
            if run_id != self.sim.store.path.name:
                raise ValueError("새 게임이 시작됐습니다. 화면을 새로고침하세요.")
            if not isinstance(overrides, (dict, type(None))) or overrides and set(overrides) - set(self.sim.world["agents"]):
                raise ValueError("Unknown action override")
            if self.provider.name == "scripted" and (overrides or instruction):
                raise ValueError("샘플 관전에서는 정해진 행동만 실행합니다. 직접 플레이로 전환해 주세요.")
            if instruction is not None:
                if not isinstance(instruction, dict) or set(instruction) != {"target", "text"}:
                    raise ValueError("Invalid instruction")
                if instruction["target"] not in {"all", *self.sim.world["agents"]}:
                    raise ValueError("Invalid instruction target")
                if not isinstance(instruction["text"], str) or len(instruction["text"]) > 1000:
                    raise ValueError("지시는 1,000자 이내여야 합니다.")
            from .domain import digest
            input_hash = digest({"overrides": overrides, "instruction": instruction, "tick": expected_tick})
            if request_id in self.inputs:
                if self.inputs[request_id] != input_hash:
                    raise ValueError("Request ID reused with different input")
                return self.snapshot()
            if expected_tick != self.sim.world["tick"] or self.sim.world["status"] != "running":
                raise ValueError("현재 턴이 바뀌었거나 게임이 끝났습니다. 상태를 새로고침하세요.")
            if self.provider.identity() != self.sim.store.metadata["provider"]:
                raise ValueError("계정 또는 모델 설정이 바뀌었습니다. 새 게임을 시작해 주세요.")
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
                raise ValueError("Invalid request ID")
            proposals, errors, audit = {}, {}, []
            observed = copy.deepcopy(self.sim.world)
            for agent in observed["agents"]:
                if overrides is not None and agent in overrides:
                    proposals[agent] = overrides[agent]
                    continue
                try:
                    result, details = self.provider.propose(observed, agent, self.sim.records, instruction)
                    proposals[agent] = result
                    audit.append({"agent": agent, **details})
                except ProviderError as exc:
                    errors[agent] = str(exc)
                    proposals[agent] = wait()
                    audit.append({"agent": agent, "error": str(exc)})
            self.sim.step(proposals, request_id, instruction=instruction, errors=errors, audit=audit,
                          expected_tick=expected_tick)
            self.inputs[request_id] = input_hash
            return self.snapshot()
        finally:
            self.lock.release()
