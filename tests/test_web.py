import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from station_loop.agents import CooperativeAgents
from station_loop.rules import ReferenceJudge
from station_loop.service import Game
from station_loop.web import handler_for, server_for


class WebTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.game = Game(ReferenceJudge(), CooperativeAgents(), runs_root=Path(self.directory.name))
        try:
            self.server = server_for(self.game, 0)
        except PermissionError:
            self.directory.cleanup()
            self.skipTest("Environment forbids socket creation; handlers covered in memory")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.directory.cleanup()

    def request(self, path, body=None, **headers):
        data = None if body is None else json.dumps(body).encode()
        if data:
            headers.setdefault("Content-Type", "application/json")
        with urllib.request.urlopen(urllib.request.Request(self.base + path, data, headers), timeout=3) as response:
            return response.status, response.read(), response.headers

    def test_assets_state_advance_export_and_reset(self):
        self.assertIn(b"STATION LOOP", self.request("/")[1])
        for asset in ("/app.js", "/style.css"):
            self.assertEqual(self.request(asset)[0], 200)
        state = json.loads(self.request("/api/state")[1])
        body = {"run_id": state["run_id"], "expected_tick": 0, "request_id": "http-turn"}
        next_state = json.loads(self.request("/api/turn", body)[1])
        self.assertEqual(next_state["world"]["tick"], 1)
        self.assertEqual(json.loads(self.request("/api/turn", body)[1]), next_state)
        exported = json.loads(self.request("/api/export")[1])
        self.assertEqual(len(exported["records"]), 1)
        reset = json.loads(self.request("/api/reset", {"run_id": state["run_id"]})[1])
        self.assertNotEqual(reset["run_id"], state["run_id"])
        self.assertEqual(reset["world"]["tick"], 0)

    def test_external_origin_and_stale_turn_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/reset", {}, Origin="https://example.org")
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/turn", {"run_id": self.game.sim.store.path.name,
                                       "expected_tick": -1, "request_id": "stale"})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.game.sim.world["tick"], 0)

    def test_static_path_traversal_is_not_served(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/../.env")
        self.assertEqual(error.exception.code, 404)


class InMemoryWebTests(WebTests):
    """Run real HTTP request parsing/handlers even in a socket-free sandbox."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.game = Game(ReferenceJudge(), CooperativeAgents(), runs_root=Path(self.directory.name))
        self.base = "http://127.0.0.1:8765"

    def tearDown(self):
        self.directory.cleanup()

    def test_sample_mode_playthrough_and_return(self):
        original = self.game.provider
        state = self.game.snapshot()
        state = json.loads(self.request("/api/mode", {"run_id": state["run_id"], "mode": "sample"})[1])
        self.assertEqual(state["provider"]["provider"], "scripted")
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/turn", {"run_id": state["run_id"], "expected_tick": 0,
                                      "request_id": "override", "overrides": {"scout": {"kind": "wait"}}})
        self.assertEqual(self.game.sim.world["tick"], 0)
        for tick in range(8):
            state = json.loads(self.request("/api/turn", {"run_id": state["run_id"],
                "expected_tick": tick, "request_id": f"sample-{tick}"})[1])
        self.assertEqual(state["world"]["status"], "won")
        self.assertEqual(state["world"]["resources"]["oxygen"], 12)
        state = json.loads(self.request("/api/reset", {"run_id": state["run_id"]})[1])
        self.assertEqual(state["provider"]["provider"], "scripted")
        self.assertEqual(state["world"]["tick"], 0)
        state = json.loads(self.request("/api/mode", {"run_id": state["run_id"], "mode": "play"})[1])
        self.assertIs(self.game.provider, original)
        self.assertEqual(state["world"]["tick"], 0)

    def test_native_wirelog_http_playthrough(self):
        import importlib.util
        from station_loop.rules import WirelogJudge
        if not importlib.util.find_spec("pyrewire"):
            self.skipTest("PyreWire not installed")
        self.game = Game(WirelogJudge(), CooperativeAgents(), runs_root=Path(self.directory.name))
        state = json.loads(self.request("/api/state")[1])
        self.assertEqual(state["judge"]["backend"], "wirelog")
        for tick in range(8):
            body = {"run_id": state["run_id"], "expected_tick": tick, "request_id": f"native-{tick}"}
            state = json.loads(self.request("/api/turn", body)[1])
        self.assertEqual(state["world"]["status"], "won")
        self.assertEqual(state["world"]["resources"]["oxygen"], 12)
        self.assertEqual(len(json.loads(self.request("/api/export")[1])["records"]), 8)

    def request(self, path, body=None, **headers):
        import io
        from http.client import HTTPResponse
        from types import SimpleNamespace

        class Connection:
            def __init__(self, data):
                self.input = io.BytesIO(data)
                self.output = bytearray()

            def makefile(self, *args):
                return self.input

            def sendall(self, data):
                self.output.extend(data)

        data = b"" if body is None else json.dumps(body).encode()
        method = "GET" if body is None else "POST"
        if data:
            headers.setdefault("Content-Type", "application/json")
        headers["Content-Length"] = str(len(data))
        request = f"{method} {path} HTTP/1.0\r\n" + "".join(f"{k}: {v}\r\n" for k,v in headers.items()) + "\r\n"
        conn = Connection(request.encode() + data)
        handler_for(self.game, getattr(self, "auth", None))(conn, ("127.0.0.1", 1), SimpleNamespace(server_port=8765))
        response = HTTPResponse(Connection(bytes(conn.output)))
        response.begin()
        result = response.read()
        if response.status >= 400:
            raise urllib.error.HTTPError(self.base + path, response.status, response.reason,
                                         response.headers, io.BytesIO(result))
        return response.status, result, response.headers


class AuthHTTPTests(unittest.TestCase):
    request = InMemoryWebTests.request

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.game = Game(ReferenceJudge(), CooperativeAgents(), runs_root=Path(self.directory.name))
        self.base = "http://127.0.0.1:8765"

        class FakeAuth:
            notice = None
            connected = False
            redirect = None
            def status(self):
                return {"active": "local-account", "connected": self.connected, "sharing": self.connected,
                        "accounts": [{"id": "local-account", "label": "test"}], "welcome": False,
                        "usage_url": "https://chatgpt.com/#settings/Usage"}
            def begin(self, redirect, profile):
                self.redirect = redirect
                return "https://auth.openai.com/api/accounts/authorize?state=test"
            def finish(self, params):
                from station_loop.chatgpt_auth import AuthError
                if params.get("state") != "test":
                    raise AuthError("invalid_callback")
                self.connected = True
            def models(self):
                return [{"slug": "available-model", "display_name": "Available model"}]
            def sign_out(self):
                self.connected = False
            def select(self, profile):
                self.connected = True
        self.auth = FakeAuth()

    def tearDown(self):
        self.directory.cleanup()

    def test_login_callback_model_selection_and_export_no_credentials(self):
        result = json.loads(self.request("/api/auth/start", {"account": None})[1])
        self.assertTrue(result["url"].startswith("https://auth.openai.com/"))
        self.assertEqual(self.auth.redirect, "http://127.0.0.1:8765/auth/callback")
        old_run = self.game.sim.store.path.name
        status, body, headers = self.request("/auth/callback?state=test&code=do-not-echo&client_id=test")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertNotIn(b"do-not-echo", body)
        self.assertNotEqual(self.game.sim.store.path.name, old_run)
        state = json.loads(self.request("/api/auth/model", {"model": "available-model"})[1])
        self.assertEqual(state["provider"]["provider"], "chatgpt")
        self.assertEqual(state["provider"]["model"], "available-model")
        self.assertNotIn("access_token", self.request("/api/export")[1].decode())
        self.assertIn(b"Continue with ChatGPT", self.request("/")[1])
        self.assertEqual(self.request("/auth.js")[0], 200)
        self.request("/api/auth/logout", {})
        self.assertFalse(self.auth.connected)

    def test_invalid_callback_does_not_reset_current_game(self):
        self.game.advance("turn", 0, self.game.sim.store.path.name)
        run_id = self.game.sim.store.path.name
        self.request("/auth/callback?state=wrong&code=a")
        self.assertIsNotNone(self.auth.notice)
        self.assertEqual(self.game.sim.store.path.name, run_id)
        self.assertEqual(self.game.sim.world["tick"], 1)

    def test_duplicate_callback_query_is_rejected_and_cross_origin_cannot_start_login(self):
        self.request("/auth/callback?state=test&state=test&code=a")
        self.assertFalse(self.auth.connected)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/auth/start", {}, Origin="https://example.org")
        self.assertEqual(error.exception.code, 403)
