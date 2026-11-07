import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

from station_loop.agents import ScriptedAgents
from station_loop.domain import Proposal, ProposalError, digest, initial_world, wait
from station_loop.replay import RunStore, replay_run, resume_run
from station_loop.rules import Decision, EngineError, ReferenceJudge, WirelogJudge
from station_loop.simulation import Simulation


def payload(kind, **fields):
    return {"action": {"kind": kind, **fields}}


class WorldTests(unittest.TestCase):
    judge_type = ReferenceJudge

    def setUp(self):
        self.judge = self.judge_type()
        self.world = initial_world()

    def decide(self, agent, action):
        return self.judge.decide(self.world, agent, Proposal.parse(action, self.world))

    def ready(self):
        w = self.world
        w["agents"]["engineer"]["room"] = "greenhouse"
        w["items"]["repair_part_1"].update(owner="engineer", room=None)

    def test_reference_play_exact_resources_and_inventory(self):
        s = Simulation(self.judge)
        provider = ScriptedAgents()
        expected = [(21,12,0),(18,12,0),(15,12,0),(12,12,0),
                    (9,12,0),(10,9,1),(11,8,2),(12,7,3)]
        for tick, numbers in enumerate(expected):
            actions = {a: provider.propose(s.world, a, s.records, None)[0] for a in s.world["agents"]}
            record = s.step(actions, f"reference-{tick}")
            self.assertTrue(all(e["allowed"] for e in record["events"]))
            r = s.world["resources"]
            self.assertEqual((r["oxygen"], r["power"], s.world["production_streak"]), numbers)
        self.assertEqual(s.world["status"], "won")
        self.assertNotIn("repair_part_1", s.world["items"])
        self.assertEqual(s.world["items"]["wrench_1"]["owner"], "engineer")

    def test_wait_only_loses_on_turn_eight(self):
        s = Simulation(self.judge)
        for tick in range(8):
            s.step({}, f"wait-{tick}")
        self.assertEqual((s.world["tick"], s.world["status"], s.world["termination"]),
                         (8, "failed", "oxygen_depleted"))

    def test_reachable_does_not_allow_teleportation(self):
        self.world["agents"]["scout"]["room"] = "storage"
        closure = self.judge.reachable(self.world)
        self.assertIn(("storage", "greenhouse"), closure)
        self.assertEqual(len(closure), 9)
        self.assertEqual(self.decide("scout", payload("move", destination="greenhouse")),
                         Decision(False, ("door_closed",)))

    def test_multiple_repair_reasons(self):
        decision = self.decide("medic", payload("repair", device_id="oxygen_generator"))
        self.assertEqual(set(decision.reasons), {"missing_part", "missing_tool", "not_colocated", "wrong_role"})

    def test_repair_without_part_is_blocked(self):
        self.world["agents"]["engineer"]["room"] = "greenhouse"
        self.assertEqual(self.decide("engineer", payload("repair", device_id="oxygen_generator")),
                         Decision(False, ("missing_part",)))

    def test_role_restriction_even_with_equipment(self):
        self.ready()
        self.world["agents"]["medic"]["room"] = "greenhouse"
        for item in self.world["items"].values():
            item.update(owner="medic", room=None)
        self.assertEqual(self.decide("medic", payload("repair", device_id="oxygen_generator")),
                         Decision(False, ("wrong_role",)))

    def test_wrong_room_give_does_not_transfer(self):
        self.world["agents"]["scout"]["room"] = "storage"
        s = Simulation(self.judge, self.world)
        record = s.step({"engineer": payload("give", item_id="wrench_1", recipient="scout")}, "give")
        self.assertEqual(record["events"][0]["reasons"], ["not_colocated"])
        self.assertEqual(s.world["items"]["wrench_1"]["owner"], "engineer")

    def test_same_item_race_uses_latest_state_and_rotating_order(self):
        for tick, winner in [(0, "medic"), (2, "scout")]:
            w = copy.deepcopy(self.world)
            w["tick"] = tick
            w["agents"]["scout"]["room"] = "storage"
            w["agents"]["medic"]["room"] = "storage"
            s = Simulation(self.judge, w)
            proposals = {a: payload("pickup", item_id="repair_part_1") for a in ("scout", "medic")}
            record = s.step(proposals, "race")
            self.assertEqual(s.world["items"]["repair_part_1"]["owner"], winner)
            self.assertEqual(sum(e["allowed"] for e in record["events"] if e["action"]["kind"] == "pickup"), 1)

    def test_exact_power_repairs_without_production(self):
        self.ready(); self.world["resources"]["power"] = 2
        s = Simulation(self.judge, self.world)
        s.step({"engineer": payload("repair", device_id="oxygen_generator")}, "repair")
        self.assertEqual(s.world["resources"]["power"], 0)
        self.assertEqual(s.world["production_streak"], 0)
        self.assertEqual(s.world["devices"]["oxygen_generator"]["status"], "online")

    def test_already_repaired_does_not_spend(self):
        self.ready(); self.world["devices"]["oxygen_generator"]["status"] = "online"
        s = Simulation(self.judge, self.world)
        record = s.step({"engineer": payload("repair", device_id="oxygen_generator")}, "again")
        self.assertEqual(record["events"][0]["reasons"], ["already_online"])
        self.assertEqual(s.world["resources"]["power"], 11)  # environment only
        self.assertIn("repair_part_1", s.world["items"])

    def test_claiming_success_does_not_change_world(self):
        s = Simulation(self.judge)
        s.step({"engineer": {"action": {"kind": "wait"}, "utterance": "생산기 수리 완료!"}}, "lie")
        self.assertEqual(s.world["devices"]["oxygen_generator"]["status"], "broken")

    def test_invalid_payload_waits_and_preserves_other_actions(self):
        s = Simulation(self.judge)
        record = s.step({"engineer": {"action": {"kind": "move", "destination": "greenhouse", "power": 100}},
                         "scout": payload("move", destination="storage")}, "bad")
        self.assertEqual(record["events"][0]["reasons"], ["invalid_fields"])
        self.assertEqual(s.world["agents"]["engineer"]["room"], "control")
        self.assertEqual(s.world["agents"]["scout"]["room"], "storage")

    def test_provider_failure_becomes_explicit_wait(self):
        s = Simulation(self.judge)
        r = s.step({}, "failure", errors={"engineer": "network_or_response_error"})
        self.assertEqual(r["events"][0]["reasons"], ["provider_error"])
        self.assertEqual(s.world["tick"], 1)

    def test_final_oxygen_not_intermediate_value(self):
        self.ready(); self.world["resources"]["oxygen"] = 1
        s = Simulation(self.judge, self.world)
        s.step({"engineer": payload("repair", device_id="oxygen_generator")}, "last-chance")
        self.assertEqual(s.world["resources"]["oxygen"], 2)
        self.assertEqual(s.world["status"], "running")

    def test_oxygen_failure_wins_over_production_success(self):
        self.world["resources"]["oxygen"] = 1
        self.world["rules"]["oxygen_production_per_tick"] = 2
        self.world["production_streak"] = 2
        self.world["devices"]["oxygen_generator"]["status"] = "online"
        s = Simulation(self.judge, self.world)
        s.step({}, "death")
        self.assertEqual(s.world["production_streak"], 3)
        self.assertEqual(s.world["termination"], "oxygen_depleted")

    def test_timeout(self):
        self.world["resources"].update(oxygen=100, oxygen_capacity=100)
        s = Simulation(self.judge, self.world)
        for tick in range(12):
            s.step({}, f"timeout-{tick}")
        self.assertEqual(s.world["termination"], "time_limit")

    def test_duplicate_request_and_different_payload(self):
        s = Simulation(self.judge)
        r = s.step({}, "same", expected_tick=0)
        self.assertEqual(s.step({}, "same", expected_tick=0), r)
        self.assertEqual(s.world["tick"], 1)
        with self.assertRaises(ValueError):
            s.step({"scout": wait()}, "same")
        with self.assertRaises(ValueError):
            s.step({}, "new", expected_tick=0)

    def test_order_of_proposal_mapping_does_not_matter(self):
        actions = {"engineer": payload("move", destination="greenhouse"),
                   "scout": payload("move", destination="storage"), "medic": wait()}
        a, b = Simulation(self.judge), Simulation(self.judge)
        a.step(actions, "order"); b.step(dict(reversed(list(actions.items()))), "order")
        self.assertEqual(a.world, b.world)

    def test_save_replay_resume_and_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = ScriptedAgents()
            store = RunStore(self.world, self.judge, provider, Path(directory))
            s = Simulation(self.judge, self.world, store)
            s.step({"scout": payload("move", destination="storage")}, "stored")
            actual = replay_run(store.path, self.judge)
            self.assertEqual(actual.world, s.world)
            resumed = resume_run(store.path, self.judge)
            resumed.step({}, "next")
            self.assertEqual(replay_run(store.path, self.judge).world, resumed.world)
            import json
            path = store.path / "turn-0002.json"
            record = json.loads(path.read_text()); record["world"]["resources"]["power"] = 999
            path.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                replay_run(store.path, self.judge)


@unittest.skipUnless(importlib.util.find_spec("pyrewire"), "PyreWire not installed")
class WirelogWorldTests(WorldTests):
    judge_type = WirelogJudge


class TransactionTests(unittest.TestCase):
    def test_engine_error_rolls_back_entire_turn(self):
        class FailingJudge(ReferenceJudge):
            def decide(self, w, agent, proposal):
                if agent == "medic":
                    raise EngineError("test engine failure")
                return super().decide(w, agent, proposal)
        s = Simulation(FailingJudge()); before = copy.deepcopy(s.world)
        with self.assertRaises(EngineError):
            s.step({"engineer": payload("move", destination="greenhouse")}, "rollback")
        self.assertEqual(s.world, before); self.assertEqual(s.records, [])

    def test_storage_failure_rolls_back(self):
        class BrokenStore:
            def commit(self, record):
                raise OSError("disk full")
        s = Simulation(ReferenceJudge(), store=BrokenStore()); before = digest(s.world)
        with self.assertRaises(OSError):
            s.step({}, "disk")
        self.assertEqual(digest(s.world), before); self.assertEqual(s.requests, {})

    def test_invalid_ids_and_envelope(self):
        for value in [True, None, [], {"agent_id": "medic", "action": {"kind": "wait"}},
                      payload("move", destination="injected\"). power(100).")]:
            with self.assertRaises(ProposalError):
                Proposal.parse(value, initial_world())
