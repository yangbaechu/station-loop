import json
import tempfile
import unittest
from pathlib import Path

from station_loop.agents import CooperativeAgents, OpenAIAgents, ProviderError, observation
from station_loop.domain import initial_world
from station_loop.rules import ReferenceJudge
from station_loop.service import Game


def completion(**changes):
    output = {"kind": "wait", "destination": None, "item_id": None, "recipient": None,
              "device_id": None, "text": None, "utterance": "산소를 감시합니다."}
    output.update(changes)
    return {"id": "response-test", "status": "completed", "usage": {"input_tokens": 100},
            "output": [{"type": "reasoning", "summary": [{"text": "DO NOT LOG"}]},
                       {"type": "message", "content": [{"type": "output_text", "text": json.dumps(output)}]}]}


class AgentTests(unittest.TestCase):
    def test_provider_switch_storage_failure_keeps_existing_game(self):
        from unittest.mock import patch
        from station_loop.agents import ScriptedAgents
        with tempfile.TemporaryDirectory() as directory:
            original = CooperativeAgents()
            game = Game(ReferenceJudge(), original, runs_root=Path(directory))
            run = game.sim.store.path.name
            with patch("station_loop.service.RunStore", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    game.switch_provider(ScriptedAgents())
            self.assertIs(game.provider, original)
            self.assertEqual(game.sim.store.path.name, run)

    def test_concurrent_turn_is_rejected_without_extra_provider_calls(self):
        import threading
        from station_loop.service import BusyError
        started, release = threading.Event(), threading.Event()
        class SlowProvider(CooperativeAgents):
            calls = 0
            def propose(self, *args):
                self.calls += 1
                if self.calls == 1:
                    started.set()
                    if not release.wait(3):
                        raise RuntimeError("Test synchronization timeout")
                return super().propose(*args)
        with tempfile.TemporaryDirectory() as directory:
            provider = SlowProvider()
            game = Game(ReferenceJudge(), provider, runs_root=Path(directory))
            run_id = game.sim.store.path.name
            errors = []
            def advance():
                try:
                    game.advance("slow", 0, run_id)
                except Exception as exc:
                    errors.append(exc)
            thread = threading.Thread(target=advance)
            thread.start()
            try:
                self.assertTrue(started.wait(3))
                with self.assertRaises(BusyError):
                    game.advance("other", 0, run_id)
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(provider.calls, 3)
            self.assertEqual(game.sim.world["tick"], 1)

    def test_openai_structured_request_and_public_only_audit(self):
        captured = []
        def transport(body):
            captured.append(body)
            return completion()
        provider = OpenAIAgents("test-model", transport=transport)
        result, audit = provider.propose(initial_world(), "engineer", [], {"target": "scout", "text": "비밀 지시"})
        self.assertEqual(result["action"], {"kind": "wait"})
        self.assertTrue(captured[0]["text"]["format"]["strict"])
        self.assertFalse(captured[0]["store"])
        self.assertEqual(json.loads(captured[0]["input"])["instruction"], "")
        self.assertNotIn("DO NOT LOG", json.dumps(audit))
        self.assertNotIn("Authorization", json.dumps(audit))

    def test_invalid_model_action_is_not_silently_corrected(self):
        provider = OpenAIAgents("test", transport=lambda _: completion(kind="wait", destination="storage"))
        with self.assertRaises(ProviderError):
            provider.propose(initial_world(), "scout", [], None)

    def test_refusal_incomplete_and_malformed(self):
        cases = [{"status": "incomplete"},
                 {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
                 {"status": "completed", "output": []}]
        for response in cases:
            provider = OpenAIAgents("test", transport=lambda _, r=response: r)
            with self.assertRaises(ProviderError):
                provider.propose(initial_world(), "scout", [], None)

    def test_cooperative_controller_finishes_and_service_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            game = Game(ReferenceJudge(), CooperativeAgents(), runs_root=Path(directory))
            run_id = game.sim.store.path.name
            first = game.advance("first", 0, run_id)
            self.assertEqual(game.advance("first", 0, run_id), first)
            with self.assertRaises(ValueError):
                game.advance("first", 0, run_id, overrides={})
            while game.sim.world["status"] == "running":
                tick = game.sim.world["tick"]
                game.advance(str(tick), tick, run_id)
            self.assertEqual(game.sim.world["status"], "won")
            self.assertEqual(game.sim.world["tick"], 8)
            self.assertEqual(len(game.sim.records), 8)

    def test_targeted_instruction(self):
        w = initial_world()
        message = {"target": "medic", "text": "부품 전달을 도와줘"}
        self.assertEqual(observation(w, "medic", [], message)["instruction"], message["text"])
        self.assertEqual(observation(w, "engineer", [], message)["instruction"], "")
