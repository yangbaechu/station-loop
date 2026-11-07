import base64
import copy
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from station_loop.agents import CooperativeAgents
from station_loop.chatgpt_agents import ChatGPTAgents, read_completed
from station_loop.chatgpt_auth import (AuthError, ChatGPTAuth, DIRECT_SCOPE, DISCOVERY, ISSUER,
                                     RESOURCE, SCOPES, TOKEN)
from station_loop.domain import initial_world
from station_loop.rules import ReferenceJudge
from station_loop.service import Game

try:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    jwt = None


def sse(event):
    return ("data: " + json.dumps(event) + "\r\n\r\n").encode()


class Stream(io.BytesIO):
    headers = {"Content-Type": "text/event-stream"}


@unittest.skipUnless(jwt, "PyJWT[crypto] not installed")
class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.private.public_key()))
        cls.jwk.update(kid="test-key", alg="RS256", use="sig")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.calls = []
        self.scope = SCOPES
        self.subject = "person-one"
        self.claim_changes = {}
        self.stream = b""
        self.exchange_error = None
        self.revocation_error = None
        self.refresh_count = 0
        self.open_calls = []
        owner = self

        class FakeHTTP:
            def json(self, url, **kwargs):
                owner.calls.append((url, copy.deepcopy(kwargs)))
                if url == DISCOVERY:
                    return {"issuer": ISSUER, "jwks_uri": ISSUER + "/jwks", "revocation_endpoint": ISSUER + "/revoke",
                            "id_token_signing_alg_values_supported": ["RS256"]}
                if url == ISSUER + "/jwks":
                    return {"keys": [owner.jwk]}
                if url == TOKEN:
                    if owner.exchange_error:
                        raise owner.exchange_error
                    form = kwargs["form"]
                    if form["grant_type"] == "refresh_token":
                        owner.refresh_count += 1
                        return {"access_token": "refreshed-secret", "refresh_token": "rotated-secret",
                                "scope": owner.scope, "token_type": "Bearer", "expires_in": 3600}
                    claims = {"iss": ISSUER, "aud": form["client_id"], "sub": owner.subject,
                              "exp": int(time.time())+600, "iat": int(time.time()),
                              "nonce": owner.expected_nonce, "email": "same@example.org"}
                    claims.update(owner.claim_changes)
                    return {"id_token": jwt.encode(claims, owner.private, algorithm="RS256", headers={"kid": "test-key"}),
                            "access_token": "access-secret", "refresh_token": "refresh-secret", "scope": owner.scope,
                            "token_type": "Bearer", "expires_in": 3600}
                if url == RESOURCE + "/models":
                    return {"models": [{"slug": "available-model", "display_name": "Available model", "visibility": "list"},
                                       {"slug": "hidden-model", "display_name": "Hidden", "visibility": "hidden"}]}
                if url == ISSUER + "/revoke":
                    if owner.revocation_error:
                        raise owner.revocation_error
                    return {}
                raise AssertionError("Unexpected upstream URL")

            def open(self, url, **kwargs):
                owner.open_calls.append((url, copy.deepcopy(kwargs)))
                return Stream(owner.stream)

        self.auth = ChatGPTAuth(Path(self.directory.name) / "auth", http=FakeHTTP())

    def tearDown(self):
        self.auth.close()
        self.directory.cleanup()

    def start(self, profile=None):
        url = self.auth.begin("http://127.0.0.1:8765/auth/callback", profile)
        query = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
        self.expected_nonce = query["nonce"]
        return query

    def login(self, client="oaiapp_client_one", profile=None):
        query = self.start(profile)
        return self.auth.finish({"state": query["state"], "code": "one-time-code", "client_id": client})

    def test_pkce_host_registration_and_protected_storage(self):
        query = self.start()
        self.assertEqual(query["client_id"], "dynamic_agent_client")
        self.assertEqual(query["agent_name_hint"], "Station Loop")
        self.assertEqual(query["resource"], RESOURCE)
        self.assertEqual(query["scope"], SCOPES)
        verifier = self.auth.pending["verifier"]
        import hashlib
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], challenge)
        host = self.auth.db["host_id"]
        self.assertEqual(self.auth.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.auth.path.stat().st_mode & 0o777, 0o600)
        self.auth.close()
        self.auth = ChatGPTAuth(self.auth.root, http=self.auth.http)
        self.assertEqual(self.auth.db["host_id"], host)

    def test_login_verifies_and_never_exposes_tokens(self):
        status = self.login()
        self.assertTrue(status["sharing"])
        self.assertTrue(status["welcome"])
        raw = json.dumps(status)
        for value in ["access-secret", "refresh-secret", "id_token", "nonce", "verifier"]:
            self.assertNotIn(value, raw)
        form = next(kwargs["form"] for url, kwargs in self.calls if url == TOKEN)
        self.assertEqual(form["client_id"], "oaiapp_client_one")
        self.assertEqual(form["redirect_uri"], "http://127.0.0.1:8765/auth/callback")
        self.assertEqual(self.auth.access_token(), "access-secret")
        self.auth.acknowledge()
        self.assertFalse(self.auth.status()["welcome"])

    def test_bad_state_is_not_exchanged_and_callback_is_one_use(self):
        query = self.start()
        with self.assertRaises(AuthError):
            self.auth.finish({"state": "bad", "code": "a", "client_id": "oaiapp_test"})
        self.assertFalse(any(url == TOKEN for url, _ in self.calls))
        self.auth.finish({"state": query["state"], "code": "valid", "client_id": "oaiapp_test"})
        count = len(self.calls)
        with self.assertRaises(AuthError):
            self.auth.finish({"state": query["state"], "code": "valid", "client_id": "oaiapp_test"})
        self.assertEqual(len(self.calls), count)

    def test_cancel_expiry_and_missing_registration(self):
        query = self.start()
        with self.assertRaisesRegex(AuthError, "취소"):
            self.auth.finish({"state": query["state"], "error": "access_denied"})
        query = self.start(); self.auth.pending["deadline"] = 0
        with self.assertRaisesRegex(AuthError, "만료"):
            self.auth.finish({"state": query["state"], "code": "a", "client_id": "oaiapp_test"})
        query = self.start()
        with self.assertRaises(AuthError):
            self.auth.finish({"state": query["state"], "code": "a"})

    def test_signature_issuer_audience_expiry_and_nonce(self):
        for bad in [{"nonce": "wrong"}, {"iss": "https://attacker.example"},
                    {"aud": "another_client"}, {"exp": int(time.time())-300}]:
            self.claim_changes = bad
            with self.assertRaises(AuthError):
                self.login()
            self.assertIsNone(self.auth.status()["active"])
        self.claim_changes = {}
        self.login()
        profile = self.auth.db["profiles"][self.auth.db["active"]]
        parts = profile["id_token"].split(".")
        claims = json.loads(base64.urlsafe_b64decode(parts[1]+"==")); claims["sub"] = "attacker"
        parts[1] = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        with self.assertRaises(AuthError):
            self.auth.verify_identity(".".join(parts), profile["client_id"])

    def test_identity_without_plan_permission_cannot_infer(self):
        self.scope = "openid email profile"
        self.assertFalse(self.login()["sharing"])
        with self.assertRaises(AuthError) as error:
            self.auth.access_token()
        self.assertEqual(error.exception.code, "consent_required")

    def test_reauthorization_reuses_client_and_does_not_accept_another_subject(self):
        self.login()
        pid = self.auth.db["active"]
        query = self.start(pid)
        self.assertEqual(query["client_id"], "oaiapp_client_one")
        self.assertNotIn("agent_name_hint", query)
        self.assertNotIn("id_token_hint", query)
        self.subject = "another-person"
        with self.assertRaises(AuthError):
            self.auth.finish({"state": query["state"], "code": "b", "client_id": "oaiapp_client_one"})
        self.assertEqual(self.auth.db["profiles"][pid]["subject"], "person-one")

    def test_same_email_registrations_keep_credentials_separate(self):
        self.login("oaiapp_workspace_one"); first = self.auth.db["active"]
        self.subject = "person-two"
        self.login("oaiapp_workspace_two"); second = self.auth.db["active"]
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.auth.status()["accounts"]), 2)
        self.auth.select(first)
        self.assertEqual(self.auth.db["profiles"][self.auth.db["active"]]["client_id"], "oaiapp_workspace_one")

    def test_returning_callback_may_omit_client_id_but_not_replace_it(self):
        self.login(); pid = self.auth.db["active"]
        query = self.start(pid)
        result = self.auth.finish({"state": query["state"], "code": "returning"})
        self.assertTrue(result["sharing"])
        self.assertEqual(self.auth.db["active"], pid)
        query = self.start(pid)
        with self.assertRaises(AuthError):
            self.auth.finish({"state": query["state"], "code": "wrong", "client_id": "different_client"})

    def test_refresh_rotation_is_serialized_and_retained(self):
        self.login(); profile = self.auth.db["profiles"][self.auth.db["active"]]
        profile["expires_at"] = 0
        values = []
        threads = [threading.Thread(target=lambda: values.append(self.auth.access_token())) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(values, ["refreshed-secret"]*4)
        self.assertEqual(self.refresh_count, 1)
        self.assertEqual(profile["refresh_token"], "rotated-secret")
        self.assertEqual(json.loads(self.auth.path.read_text())["profiles"][self.auth.db["active"]]["refresh_token"], "rotated-secret")
        form = [args["form"] for url,args in self.calls if url == TOKEN][-1]
        self.assertNotIn("scope", form)

    def test_invalid_grant_clears_credentials_but_keeps_registration(self):
        self.login(); pid = self.auth.db["active"]
        self.auth.db["profiles"][pid]["expires_at"] = 0
        self.exchange_error = AuthError("reauth_required", status=400)
        with self.assertRaises(AuthError): self.auth.access_token()
        self.assertFalse(self.auth.status()["connected"])
        self.assertEqual(self.auth.db["profiles"][pid]["client_id"], "oaiapp_client_one")

    def test_registration_survives_failed_exchange(self):
        self.exchange_error = AuthError("reauth_required", status=400)
        with self.assertRaises(AuthError): self.login()
        profile = next(iter(self.auth.db["profiles"].values()))
        self.assertEqual(profile["client_id"], "oaiapp_client_one")
        self.assertFalse(profile.get("access_token"))

    def test_sign_out_revokes_and_preserves_host_and_client(self):
        self.login(); pid = self.auth.db["active"]; host = self.auth.db["host_id"]
        self.auth.sign_out()
        self.assertFalse(self.auth.status()["connected"])
        self.assertEqual(self.auth.db["host_id"], host)
        query = self.start(pid)
        self.assertEqual(query["client_id"], "oaiapp_client_one")
        self.assertNotIn("id_token_hint", query)
        self.assertTrue(any(url == ISSUER+"/revoke" for url, _ in self.calls))

    def test_unconfirmed_remote_signout_clears_local_tokens(self):
        self.login(); self.revocation_error = AuthError("network_error", status=503)
        self.auth.sign_out()
        self.assertFalse(self.auth.status()["connected"])
        self.assertIsNotNone(self.auth.status()["notice"])

    def test_models_filter_visibility_and_plan_request_contract(self):
        self.login()
        self.assertEqual(self.auth.models(), [{"slug": "available-model", "display_name": "Available model"}])
        output = {"kind":"wait","destination":None,"item_id":None,"recipient":None,"device_id":None,"text":None,"utterance":"감시 중입니다."}
        completed = {"id":"response-test","status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":json.dumps(output)}]}]}
        self.stream = sse({"type":"response.output_text.delta","delta":"partial"}) + sse({"type":"response.completed","response":completed})
        provider = ChatGPTAgents(self.auth, "available-model")
        with patch.dict(os.environ, {"OPENAI_API_KEY":"must-never-be-used"}):
            result, audit = provider.propose(initial_world(), "engineer", [], None)
        self.assertEqual(result["action"], {"kind":"wait"})
        url, args = self.open_calls[0]
        self.assertEqual(url, RESOURCE+"/responses")
        self.assertEqual(args["token"], "access-secret")
        self.assertTrue(args["body"]["stream"])
        self.assertFalse(args["body"]["store"])
        self.assertIsInstance(args["body"]["input"], list)
        self.assertNotIn("max_output_tokens", args["body"])
        self.assertNotIn("must-never-be-used", json.dumps(audit))
        self.assertNotIn("access-secret", json.dumps(audit))

    def test_usage_failure_pauses_without_world_mutation_or_key_fallback(self):
        self.login(); self.stream = sse({"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}})
        game = Game(ReferenceJudge(), ChatGPTAgents(self.auth, "available-model"), runs_root=Path(self.directory.name)/"runs")
        before = copy.deepcopy(game.sim.world)
        with self.assertRaises(AuthError): game.advance("quota",0,game.sim.store.path.name)
        self.assertEqual(game.sim.world, before)
        self.assertEqual(game.sim.records, [])
        self.assertEqual(self.auth.status()["blocked"], "usage_limit")
        with self.assertRaises(AuthError): game.advance("again",0,game.sim.store.path.name)
        self.assertEqual(len(self.open_calls), 1)
        self.auth.retry()
        self.assertIsNone(self.auth.status()["blocked"])

    def test_one_process_per_credential_store(self):
        with self.assertRaises(AuthError) as error:
            ChatGPTAuth(self.auth.root, http=self.auth.http)
        self.assertEqual(error.exception.code, "auth_busy")


class StreamTests(unittest.TestCase):
    def test_partial_or_done_without_completed_is_failure(self):
        for body in [sse({"type":"response.output_text.delta","delta":"hello"}),b"data: [DONE]\n\n",b"data: broken\n\n"]:
            with self.assertRaises(AuthError): read_completed(Stream(body))

    def test_incomplete_and_error_are_not_success(self):
        for event in [{"type":"response.incomplete"},{"type":"error","code":"subscription_sharing_usage_unavailable"}]:
            with self.assertRaises(AuthError): read_completed(Stream(sse(event)))

    def test_completed_requires_terminal_status(self):
        with self.assertRaises(AuthError):
            read_completed(Stream(sse({"type":"response.completed","response":{"status":"in_progress"}})))
