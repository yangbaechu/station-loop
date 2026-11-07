from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

from . import ROOT
from .domain import digest
from .simulation import Simulation


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RunStore:
    def __init__(self, initial: dict, judge, provider, root: Path | None = None):
        self.path = (root or ROOT / "runs") / uuid.uuid4().hex
        self.path.mkdir(parents=True)
        self.metadata = {"format": 1, "run_id": self.path.name, "initial_world": initial,
                         "initial_hash": digest(initial), "judge": judge.identity(),
                         "provider": provider.identity(), "simulation_version": 1}
        atomic_json(self.path / "metadata.json", self.metadata)

    def commit(self, record: dict):
        path = self.path / f"turn-{record['tick']:04d}.json"
        if path.exists():
            raise ValueError("Refusing to overwrite committed turn")
        atomic_json(path, record)


def load_run(path: Path):
    metadata = json.loads((path / "metadata.json").read_text())
    records = [json.loads(p.read_text()) for p in sorted(path.glob("turn-*.json"))]
    if digest(metadata["initial_world"]) != metadata["initial_hash"]:
        raise ValueError("Initial state hash mismatch")
    for tick, record in enumerate(records, 1):
        if record["tick"] != tick:
            raise ValueError("Run contains missing or reordered turns")
    return metadata, records


def replay_run(path: Path, judge) -> Simulation:
    metadata, records = load_run(path)
    if metadata["judge"] != judge.identity():
        raise ValueError("Rules backend or version differs from recorded run")
    if metadata.get("simulation_version") != 1:
        raise ValueError("Unsupported simulation version")
    sim = Simulation(judge, metadata["initial_world"])
    for record in records:
        if digest(sim.world) != record["before_hash"]:
            raise ValueError("Replay before-state mismatch")
        actual = sim.step(record["proposals"], record["request_id"], instruction=record["instruction"],
                          errors=record["errors"])
        if actual["after_hash"] != record["after_hash"] or actual["events"] != record["events"]:
            raise ValueError(f"Replay differs at turn {record['tick']}")
        if record["fingerprint"] != actual["fingerprint"] or digest(record["world"]) != actual["after_hash"]:
            raise ValueError("Recorded world or input fingerprint mismatch")
    return sim


def resume_run(path: Path, judge) -> Simulation:
    sim = replay_run(path, judge)
    store = object.__new__(RunStore)
    store.path = path
    store.metadata, _ = load_run(path)
    sim.store = store
    return sim
