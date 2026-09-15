import hashlib
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

CANVAS_URL = os.environ.get("CANVAS_URL", "http://localhost:3100").rstrip("/")
ISSUER = "https://canvas.instructure.com"
AUTHORIZE_URL = f"{CANVAS_URL}/api/lti/authorize_redirect"
JWKS_URL = f"{CANVAS_URL}/api/lti/security/jwks"

CLAIM = "https://purl.imsglobal.org/spec/lti/claim"
EDUCATOR_ROLES = ("#Instructor", "#TeachingAssistant", "#Administrator")
DEFAULT_COURSE = 1

STATE_TTL_SECONDS = 300
KEY_PATH = Path(__file__).resolve().parent / "keys" / "tool.pem"


class Refused(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Pending:
    nonce: str
    client_id: str
    target_link_uri: str
    expires: float


@dataclass(frozen=True, slots=True)
class Launch:
    user_lti_id: str
    name: str
    roles: list[str]
    is_educator: bool
    canvas_user_id: int | None
    course_id: int
    context_title: str | None
    message_type: str
    raw_claims: dict


_pending: dict[str, Pending] = {}
_lock = threading.Lock()
_keys = jwt.PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600, timeout=10)


def begin_login(client_id: str, login_hint: str, target_link_uri: str, lti_message_hint: str) -> str:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    pending = Pending(nonce, client_id, target_link_uri, time.monotonic() + STATE_TTL_SECONDS)
    with _lock:
        now = time.monotonic()
        for stale in [key for key, entry in _pending.items() if entry.expires < now]:
            del _pending[stale]
        _pending[state] = pending
    query = {
        "scope": "openid",
        "response_type": "id_token",
        "response_mode": "form_post",
        "prompt": "none",
        "client_id": client_id,
        "redirect_uri": target_link_uri,
        "login_hint": login_hint,
        "state": state,
        "nonce": nonce,
        "lti_message_hint": lti_message_hint,
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def claim(state: str) -> Pending | None:
    with _lock:
        pending = _pending.pop(state, None)
    if pending is None or pending.expires < time.monotonic():
        return None
    return pending


def verify_id_token(id_token: str, pending: Pending) -> dict:
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
        key = _keys.get_signing_key(kid)
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256"],
            audience=pending.client_id,
            issuer=ISSUER,
        )
    except (jwt.PyJWTError, jwt.exceptions.PyJWKClientError) as error:
        raise Refused(f"{type(error).__name__}: {error}") from error
    if claims.get("nonce") != pending.nonce:
        raise Refused("nonce mismatch")
    return claims


def launch_from_claims(claims: dict) -> Launch:
    roles = list(claims.get(f"{CLAIM}/roles") or [])
    custom = claims.get(f"{CLAIM}/custom") or {}
    context = claims.get(f"{CLAIM}/context") or {}
    return Launch(
        user_lti_id=str(claims.get("sub", "")),
        name=str(claims.get("name") or "unknown"),
        roles=roles,
        is_educator=any(marker in role for role in roles for marker in EDUCATOR_ROLES),
        canvas_user_id=_as_int(custom.get("canvas_user_id")),
        course_id=_as_int(custom.get("canvas_course_id")) or DEFAULT_COURSE,
        context_title=context.get("title"),
        message_type=str(claims.get(f"{CLAIM}/message_type") or "unknown"),
        raw_claims=claims,
    )


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def private_key() -> rsa.RSAPrivateKey:
    if not KEY_PATH.exists():
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        KEY_PATH.write_bytes(pem)
        os.chmod(KEY_PATH, 0o600)
    return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)


def public_jwk() -> dict:
    public = private_key().public_key()
    der = public.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(public, as_dict=True)
    # RFC 7517 section 4.3: use and key_ops together are discouraged, and `use` is what Canvas reads.
    jwk.pop("key_ops", None)
    jwk["kid"] = hashlib.sha256(der).hexdigest()[:16]
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    return jwk
