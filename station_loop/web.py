from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

from . import ROOT
from .rules import EngineError, REASONS
from .service import BusyError
from .chatgpt_auth import AuthError
from .chatgpt_agents import ChatGPTAgents

ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/auth.js": ("auth.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


def handler_for(game, auth=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type="application/json; charset=utf-8", attachment=False):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            if attachment:
                self.send_header("Content-Disposition", 'attachment; filename="station-loop-run.json"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/auth/callback":
                if auth is None:
                    self.send(404, {"error": "ChatGPT 연결을 사용할 수 없습니다."})
                    return
                if not game.lock.acquire(blocking=False):
                    self.send(409, {"error": "진행 중인 턴이 끝나면 이 페이지를 새로고침해 주세요."})
                    return
                try:
                    query = urlsplit(self.path).query
                    params = parse_qs(query, keep_blank_values=True, max_num_fields=20)
                    if len(query) > 16384 or any(len(values) != 1 for values in params.values()):
                        raise AuthError("invalid_callback")
                    auth.finish({key: values[0] for key, values in params.items()})
                    game.switch_provider(ChatGPTAgents(auth))
                except AuthError as exc:
                    auth.notice = str(exc)
                except (ValueError, KeyError, TypeError):
                    auth.notice = "로그인 응답을 확인하지 못했습니다. 다시 연결해 주세요."
                finally:
                    game.lock.release()
                # Remove code/state from the visible URL and never log callback URLs.
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif path == "/api/auth/status":
                self.send(200, {"enabled": auth is not None, **(auth.status() if auth else {})})
            elif path == "/api/auth/models" and auth:
                try:
                    self.send(200, {"models": auth.models()})
                except AuthError as exc:
                    self.send(exc.status, exc.public())
            elif path in ASSETS:
                filename, kind = ASSETS[path]
                self.send(200, (ROOT / "station_loop/static" / filename).read_bytes(), kind)
            elif path == "/api/state":
                self.send(200, {**game.snapshot(), "reason_labels": REASONS})
            elif path == "/api/export":
                with game.lock:
                    self.send(200, {"metadata": game.sim.store.metadata, "records": game.sim.records}, attachment=True)
            else:
                self.send(404, {"error": "찾을 수 없습니다."})

        def do_POST(self):
            path = urlsplit(self.path).path
            origin = self.headers.get("Origin")
            valid_origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            if origin and origin not in valid_origins:
                self.send(403, {"error": "다른 사이트에서 보낸 요청입니다."})
                return
            if self.headers.get_content_type() != "application/json":
                self.send(415, {"error": "JSON 요청이 필요합니다."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("잘못된 요청 크기입니다.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("잘못된 요청 형식입니다.")
                if path.startswith("/api/auth/"):
                    if auth is None:
                        self.send(404, {"error": "ChatGPT 연결을 사용할 수 없습니다."})
                        return
                    if not game.lock.acquire(blocking=False):
                        raise BusyError("현재 턴이 끝난 뒤 계정 설정을 변경해 주세요.")
                    try:
                        if path == "/api/auth/start":
                            if set(body) - {"account"}:
                                raise ValueError("Invalid login request")
                            url = auth.begin(f"http://127.0.0.1:{self.server.server_port}/auth/callback", body.get("account"))
                            result = {"url": url}
                        elif path == "/api/auth/select":
                            # Finish OAuth before replacing the active account.
                            url = auth.begin(f"http://127.0.0.1:{self.server.server_port}/auth/callback", body["account"])
                            result = {"url": url}
                        elif path == "/api/auth/model":
                            model = body.get("model")
                            if model not in {m["slug"] for m in auth.models()}:
                                raise AuthError("model_required", status=409)
                            game.switch_provider(ChatGPTAgents(auth, model))
                            result = game.snapshot()
                        elif path == "/api/auth/logout":
                            auth.sign_out()
                            game.switch_provider(ChatGPTAgents(auth))
                            result = game.snapshot()
                        elif path == "/api/auth/welcome":
                            auth.acknowledge()
                            result = auth.status()
                        elif path == "/api/auth/retry":
                            auth.retry()
                            result = {"models": auth.models(refresh=True)}
                        else:
                            self.send(404, {"error": "찾을 수 없습니다."})
                            return
                    finally:
                        game.lock.release()
                elif path == "/api/mode":
                    if not game.lock.acquire(blocking=False):
                        raise BusyError("현재 턴이 끝난 뒤 모드를 바꿔 주세요.")
                    try:
                        if body.get("run_id") != game.sim.store.path.name:
                            raise ValueError("현재 게임이 바뀌었습니다. 새로고침해 주세요.")
                        game.switch_mode(body.get("mode"))
                        result = game.snapshot()
                    finally:
                        game.lock.release()
                elif path == "/api/turn":
                    if set(body) - {"request_id", "expected_tick", "run_id", "overrides", "instruction"}:
                        raise ValueError("알 수 없는 요청 항목입니다.")
                    result = game.advance(body["request_id"], body["expected_tick"], body["run_id"],
                                          overrides=body.get("overrides"), instruction=body.get("instruction"))
                elif path == "/api/reset":
                    if not game.lock.acquire(blocking=False):
                        raise BusyError("진행 중인 턴이 끝난 뒤 새 게임을 시작하세요.")
                    try:
                        if body.get("run_id") != game.sim.store.path.name:
                            raise ValueError("현재 게임이 바뀌었습니다.")
                        game.reset()
                        result = game.snapshot()
                    finally:
                        game.lock.release()
                else:
                    self.send(404, {"error": "찾을 수 없습니다."})
                    return
                self.send(200, result)
            except BusyError as exc:
                self.send(409, {"error": str(exc)})
            except AuthError as exc:
                self.send(exc.status, exc.public())
            except (ValueError, KeyError, TypeError) as exc:
                self.send(400, {"error": str(exc)})
            except EngineError as exc:
                self.send(503, {"error": str(exc)})
            except Exception:
                self.send(500, {"error": "턴을 확정하지 못했습니다. 터미널과 저장 경로를 확인하세요."})
                import traceback
                traceback.print_exc()

    return Handler


def server_for(game, port=8765, auth=None):
    return ThreadingHTTPServer(("127.0.0.1", port), handler_for(game, auth))
