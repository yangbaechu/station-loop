"""Public-client OAuth for local ChatGPT plan usage; never reuses Codex tokens."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import ROOT

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
DISCOVERY = ISSUER + "/.well-known/openid-configuration"
USAGE_URL = "https://chatgpt.com/#settings/Usage"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
MESSAGES = {
    "not_connected": "ChatGPT 계정을 먼저 연결해 주세요.",
    "consent_required": "로그인은 됐지만 ChatGPT 구독 사용 권한이 없습니다. 다시 연결해 권한을 승인해 주세요.",
    "access_denied": "ChatGPT 연결이 취소됐습니다.",
    "invalid_callback": "로그인 응답을 확인할 수 없습니다. 새 로그인을 시작해 주세요.",
    "expired_attempt": "로그인 시간이 만료됐습니다. 새 로그인을 시작해 주세요.",
    "invalid_identity": "계정 서명 또는 로그인 정보를 검증하지 못했습니다.",
    "reauth_required": "인증이 만료되거나 해제됐습니다. 해당 계정을 다시 연결해 주세요.",
    "usage_limit": "ChatGPT 사용량 한도에 도달했습니다. Manage usage에서 확인한 뒤 다시 시도해 주세요.",
    "not_eligible": "선택한 계정 또는 워크스페이스에서 ChatGPT 구독을 사용할 수 없습니다.",
    "unavailable": "ChatGPT 구독 사용 서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
    "network_error": "인증 서비스에 연결하지 못했습니다. 네트워크를 확인해 주세요.",
    "model_required": "이 계정에서 사용할 모델을 선택해 주세요.",
    "interrupted_stream": "모델 응답이 완료되기 전에 연결이 끊겼습니다. 턴을 진행하지 않았습니다.",
    "storage_error": "인증정보를 안전하게 저장하지 못했습니다. 저장 경로를 확인해 주세요.",
    "auth_busy": "같은 인증 폴더를 다른 프로세스가 사용 중입니다. 기존 서버를 종료해 주세요.",
    "dependency_missing": "ChatGPT 로그인에는 PyJWT[crypto] 설치가 필요합니다. README의 설치 명령을 실행해 주세요.",
    "request_failed": "ChatGPT 요청이 실패했습니다. 계정 권한과 연결 상태를 확인해 주세요.",
}


class AuthError(RuntimeError):
    def __init__(self, code, *, status=400, request_id=None, remote_code=None):
        self.code, self.status = code, status
        self.request_id, self.remote_code = request_id, remote_code
        super().__init__(MESSAGES.get(code, MESSAGES["request_failed"]))

    def public(self):
        return {"error": str(self), "code": self.code, "request_id": self.request_id,
                "remote_code": self.remote_code, "usage_url": USAGE_URL}


def remote_error(status, data=None, request_id=None):
    detail = data.get("error", {}) if isinstance(data, dict) else {}
    remote = detail.get("code") if isinstance(detail, dict) else detail if isinstance(detail, str) else None
    if not isinstance(remote, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,100}", remote):
        remote = None
    mapping = {
        "subscription_sharing_usage_limit_exceeded": "usage_limit",
        "subscription_sharing_user_not_eligible": "not_eligible",
        "subscription_sharing_usage_unavailable": "unavailable",
        "invalid_grant": "reauth_required",
    }
    code = mapping.get(remote, {401: "reauth_required", 429: "usage_limit", 503: "unavailable"}.get(status, "request_failed"))
    return AuthError(code, status=status if 400 <= status <= 599 else 502,
                     request_id=request_id, remote_code=remote)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTP:
    """Fixed upstream endpoints; no auth header follows a redirect."""
    def __init__(self):
        self.opener = urllib.request.build_opener(NoRedirect)

    def open(self, url, *, form=None, body=None, token=None, accept=None):
        headers = {}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        if accept:
            headers["Accept"] = accept
        request = urllib.request.Request(url, data, headers)
        try:
            return self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read(65536))
            except (ValueError, OSError):
                payload = None
            raise remote_error(exc.code, payload, exc.headers.get("x-request-id")) from None
        except OSError:
            raise AuthError("network_error", status=503) from None

    def json(self, url, **kwargs):
        with self.open(url, **kwargs) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise AuthError("request_failed", status=502)
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except ValueError:
                raise AuthError("request_failed", status=502) from None


def secure_write(path: Path, data):
    fd, temporary = tempfile.mkstemp(prefix=".auth-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise AuthError("storage_error", status=500) from None
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def trusted_auth_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "auth.openai.com" or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise AuthError("invalid_identity")
    return url


class ChatGPTAuth:
    def __init__(self, root: Path | None = None, *, http=None):
        self.root = Path(root) if root is not None else ROOT / ".station-loop/auth"
        if self.root.is_symlink():
            raise AuthError("storage_error")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.path = self.root / "accounts.json"
        self.lock = threading.RLock()
        self.http = http or HTTP()
        self.pending = None
        self.blocked = {}
        self.catalogs = {}
        self.notice = None
        self.poisoned = False
        self._metadata = None
        self._lock_fd = os.open(self.root / "session.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            import fcntl
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, BlockingIOError):
            os.close(self._lock_fd)
            self._lock_fd = None
            raise AuthError("auth_busy", status=409) from None
        try:
            if self.path.exists():
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd) as stream:
                    self.db = json.load(stream)
                os.chmod(self.path, 0o600)
                if self.db.get("format") != 1 or not isinstance(self.db.get("profiles"), dict):
                    raise AuthError("storage_error")
            else:
                self.db = {"format": 1, "host_id": "urn:uuid:" + str(uuid.uuid4()), "profiles": {}, "active": None}
                self._save()
        except Exception:
            self.close()
            raise

    def close(self):
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _save(self):
        try:
            secure_write(self.path, self.db)
        except AuthError:
            # In-memory rotating credentials are newer; never reuse stale disk tokens.
            self.poisoned = True
            raise

    def metadata(self):
        if self._metadata is None:
            data = self.http.json(DISCOVERY)
            if not isinstance(data, dict) or data.get("issuer") != ISSUER:
                raise AuthError("invalid_identity")
            trusted_auth_url(data["jwks_uri"])
            self._metadata = data
        return self._metadata

    def status(self):
        with self.lock:
            active = self.db["active"]
            profile = self.db["profiles"].get(active, {})
            connected = bool(profile.get("subject") and profile.get("access_token"))
            sharing = connected and DIRECT_SCOPE in profile.get("scopes", [])
            return {"accounts": [{"id": key, "label": p.get("label", "등록 중인 계정"),
                                   "email": p.get("email", ""), "connected": bool(p.get("access_token"))}
                                  for key, p in self.db["profiles"].items()],
                    "active": active, "connected": connected, "sharing": sharing,
                    "pending": bool(self.pending and time.time() < self.pending["deadline"]),
                    "blocked": self.blocked.get(active), "notice": self.notice,
                    "welcome": sharing and not profile.get("welcomed", False), "usage_url": USAGE_URL}

    def begin(self, redirect_uri, profile_id=None):
        with self.lock:
            if profile_id is not None and not isinstance(profile_id, str):
                raise AuthError("invalid_callback")
            try:
                import jwt  # noqa: F401
                from cryptography.hazmat.primitives import hashes  # noqa: F401
            except ImportError:
                raise AuthError("dependency_missing") from None
            uri = urllib.parse.urlsplit(redirect_uri)
            if uri.scheme != "http" or uri.hostname != "127.0.0.1" or not uri.port or uri.path != "/auth/callback" or uri.query or uri.fragment or uri.username:
                raise AuthError("invalid_callback")
            profile = self.db["profiles"].get(profile_id) if profile_id else None
            if profile_id and profile is None:
                raise AuthError("invalid_callback")
            verifier = secrets.token_urlsafe(48)
            pending = {"state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
                       "verifier": verifier, "redirect_uri": redirect_uri, "deadline": time.time() + 600,
                       "profile_id": profile_id, "client_id": profile["client_id"] if profile else None}
            query = {"client_id": pending["client_id"] or "dynamic_agent_client", "response_type": "code",
                     "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE,
                     "state": pending["state"], "nonce": pending["nonce"], "code_challenge_method": "S256",
                     "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
                     "ext_agent_host_id": self.db["host_id"]}
            if not profile:
                query["agent_name_hint"] = "Station Loop"
            elif profile.get("email"):
                query["login_hint"] = profile["email"]
            # No id_token_hint in browser-facing URLs. Account selection is explicit.
            self.pending = pending
            return AUTHORIZE + "?" + urllib.parse.urlencode(query)

    def verify_identity(self, token, client_id, nonce=None):
        try:
            import jwt
            header = jwt.get_unverified_header(token)
            alg = header.get("alg")
            supported = self.metadata().get("id_token_signing_alg_values_supported", ["RS256"])
            if alg not in {"RS256", "ES256", "PS256", "EdDSA"} or alg not in supported:
                raise ValueError("Unsupported signing algorithm")
            keys = self.http.json(trusted_auth_url(self.metadata()["jwks_uri"]))["keys"]
            matching = [k for k in keys if k.get("kid") == header.get("kid") and k.get("use", "sig") == "sig"
                        and k.get("alg", alg) == alg]
            if len(matching) != 1:
                raise ValueError("Unknown or ambiguous signing key")
            key = jwt.PyJWK.from_dict(matching[0], algorithm=alg).key
            claims = jwt.decode(token, key, algorithms=[alg], audience=client_id, issuer=ISSUER,
                                leeway=30, options={"require": ["exp", "iat", "iss", "sub", "aud"]})
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise ValueError("Missing subject")
            if nonce is not None and (not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(claims["nonce"], nonce)):
                raise ValueError("Nonce mismatch")
            if (isinstance(claims["aud"], list) and len(claims["aud"]) > 1) or "azp" in claims:
                if claims.get("azp") != client_id:
                    raise ValueError("Authorized party mismatch")
            return claims
        except AuthError:
            raise
        except ImportError:
            raise AuthError("dependency_missing") from None
        except Exception:
            raise AuthError("invalid_identity") from None

    def _credentials(self, data, previous=None):
        if not isinstance(data, dict) or not isinstance(data.get("access_token"), str) or not data["access_token"]:
            raise AuthError("invalid_identity")
        if str(data.get("token_type", "")).lower() != "bearer":
            raise AuthError("invalid_identity")
        expiry = data.get("expires_in")
        if type(expiry) not in (int, float) or not 0 < expiry <= 86400:
            raise AuthError("invalid_identity")
        scope = data.get("scope")
        if scope is not None and not isinstance(scope, str):
            raise AuthError("invalid_identity")
        refresh = data.get("refresh_token", (previous or {}).get("refresh_token"))
        if refresh is not None and not isinstance(refresh, str):
            raise AuthError("invalid_identity")
        return {"access_token": data["access_token"], "refresh_token": refresh,
                "token_type": "Bearer", "expires_at": time.time() + expiry,
                "scopes": scope.split() if scope is not None else (previous or {}).get("scopes", []),
                "id_token": data.get("id_token", (previous or {}).get("id_token"))}

    def finish(self, params):
        with self.lock:
            pending = self.pending
            if not pending or not isinstance(params.get("state"), str) or not secrets.compare_digest(params["state"], pending["state"]):
                raise AuthError("invalid_callback")
            self.pending = None  # Valid-state callback is one-use, including denial/errors.
            if time.time() > pending["deadline"]:
                raise AuthError("expired_attempt")
            if params.get("error"):
                raise AuthError("access_denied" if params["error"] == "access_denied" else "invalid_callback")
            code = params.get("code")
            client = params.get("client_id", pending["client_id"])
            if not isinstance(code, str) or not 1 <= len(code) <= 4096 or not isinstance(client, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,200}", client) or client == "dynamic_agent_client":
                raise AuthError("invalid_callback")
            if pending["client_id"] and pending["client_id"] != client:
                raise AuthError("invalid_callback")
            pid = pending["profile_id"]
            if pid is None:
                pid = uuid.uuid4().hex
                # Preserve the issued registration if exchange needs a fresh sign-in.
                self.db["profiles"][pid] = {"client_id": client, "subject": None, "label": "등록 중 · " + client[-8:]}
                self._save()
            profile = self.db["profiles"][pid]
            data = self.http.json(TOKEN, form={"grant_type": "authorization_code", "client_id": client,
                                 "code": code, "code_verifier": pending["verifier"],
                                 "redirect_uri": pending["redirect_uri"], "resource": RESOURCE})
            if not isinstance(data, dict):
                raise AuthError("invalid_identity")
            claims = self.verify_identity(data.get("id_token"), client, pending["nonce"])
            if profile.get("subject") and claims["sub"] != profile["subject"]:
                raise AuthError("invalid_identity")
            credentials = self._credentials(data)
            email = claims.get("email", "")
            email = email[:256] if isinstance(email, str) else ""
            profile.update(credentials, subject=claims["sub"], email=email,
                           label=(email or "ChatGPT 계정") + " · " + client[-8:])
            self.db["active"] = pid
            self.blocked.pop(pid, None)
            self._save()
            self.notice = None
            return self.status()

    def select(self, profile_id):
        with self.lock:
            if profile_id not in self.db["profiles"]:
                raise AuthError("not_connected")
            self.db["active"] = profile_id
            self._save()
            return self.status()

    def acknowledge(self):
        with self.lock:
            if self.db["active"]:
                self.db["profiles"][self.db["active"]]["welcomed"] = True
                self._save()

    def access_token(self):
        with self.lock:
            if self.poisoned:
                raise AuthError("storage_error", status=500)
            pid = self.db["active"]
            p = self.db["profiles"].get(pid, {})
            if not p.get("access_token") or not p.get("subject"):
                raise AuthError("not_connected", status=401)
            if DIRECT_SCOPE not in p.get("scopes", []):
                raise AuthError("consent_required", status=403)
            if pid in self.blocked:
                raise AuthError(self.blocked[pid], status=429 if self.blocked[pid] == "usage_limit" else 403)
            if p["expires_at"] - time.time() > 60:
                return p["access_token"]
            if not p.get("refresh_token"):
                raise AuthError("reauth_required", status=401)
            try:
                data = self.http.json(TOKEN, form={"grant_type": "refresh_token", "client_id": p["client_id"],
                                     "refresh_token": p["refresh_token"], "resource": RESOURCE})
            except AuthError as exc:
                if exc.code == "reauth_required":
                    self._clear_tokens(p)
                    self._save()
                raise
            if data.get("id_token"):
                claims = self.verify_identity(data["id_token"], p["client_id"])
                if claims["sub"] != p["subject"]:
                    raise AuthError("invalid_identity")
            p.update(self._credentials(data, p))
            self._save()
            if DIRECT_SCOPE not in p["scopes"]:
                raise AuthError("consent_required", status=403)
            return p["access_token"]

    def mark_error(self, error):
        with self.lock:
            if error.code in {"usage_limit", "not_eligible", "reauth_required"} and self.db["active"]:
                self.blocked[self.db["active"]] = error.code

    def retry(self):
        with self.lock:
            self.blocked.pop(self.db["active"], None)

    def models(self, *, refresh=False):
        with self.lock:
            pid = self.db["active"]
            if not refresh and pid in self.catalogs and time.time() - self.catalogs[pid][0] < 60:
                return copy.deepcopy(self.catalogs[pid][1])
            try:
                data = self.http.json(RESOURCE + "/models", token=self.access_token())
                models = [{"slug": m["slug"], "display_name": m["display_name"]}
                          for m in data["models"] if m.get("visibility") == "list"
                          and isinstance(m.get("slug"), str) and isinstance(m.get("display_name"), str)]
            except AuthError as exc:
                self.mark_error(exc)
                raise
            except (KeyError, TypeError, ValueError):
                raise AuthError("request_failed", status=502) from None
            self.catalogs[pid] = (time.time(), models)
            return copy.deepcopy(models)

    @staticmethod
    def _clear_tokens(profile):
        for field in ("access_token", "refresh_token", "id_token", "expires_at", "scopes"):
            profile.pop(field, None)

    def sign_out(self):
        with self.lock:
            pid = self.db["active"]
            p = self.db["profiles"].get(pid, {})
            confirmed = not p.get("refresh_token")
            if p.get("refresh_token"):
                for attempt in range(2):
                    try:
                        endpoint = trusted_auth_url(self.metadata()["revocation_endpoint"])
                        self.http.json(endpoint, form={"token": p["refresh_token"], "token_type_hint": "refresh_token",
                                                      "client_id": p["client_id"]})
                        confirmed = True
                        break
                    except (AuthError, KeyError) as exc:
                        if attempt == 0 and (not isinstance(exc, AuthError) or exc.status >= 500):
                            time.sleep(0.2)
                        else:
                            break
            self._clear_tokens(p)
            self.catalogs.pop(pid, None)
            self.blocked.pop(pid, None)
            self.pending = None
            self._save()
            self.notice = None if confirmed else "로컬 로그아웃은 완료했습니다. 서버 연결 해제는 확인하지 못했으니 ChatGPT 설정에서 앱 연결을 확인해 주세요."
            return self.status()
