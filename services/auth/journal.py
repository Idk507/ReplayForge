"""Durable one-use browser login and opaque session custody, no GitHub I/O."""

import base64
import hmac
import re
import secrets
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, SecretBytes, SecretStr

from services.auth.github import AuthDenied, GitHubUser, UserToken
from services.persistence.store import Store


class Closed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class Attempt(Closed):
    state: SecretStr
    browser: SecretStr
    challenge: str


class ExchangeClaim(Closed):
    state_hash: str
    claim: UUID
    verifier: SecretStr


class IssuedSession(Closed):
    user_id: UUID
    secret: SecretStr


class SessionUser(Closed):
    id: UUID
    github_id: int
    github_login: str
    avatar_url: str


class AuthVault:
    """Explicit separate auth encryption/HMAC keys; no environment discovery.

    Key version v1 is authenticated in every envelope. Rotation requires retaining
    the original configured vault until records expire; no silent key fallback.
    """

    def __init__(self, encryption_key: SecretBytes, hash_key: SecretBytes) -> None:
        encryption, hashing = (
            encryption_key.get_secret_value(),
            hash_key.get_secret_value(),
        )
        if len(encryption) != 32 or len(hashing) != 32 or encryption == hashing:
            raise AuthDenied("AUTH_KEY_CONFIG_INVALID")
        self._cipher = AESGCM(encryption)
        self._hash_key = hashing

    def digest(self, purpose: str, value: SecretStr) -> str:
        raw = value.get_secret_value()
        if not 1 <= len(raw) <= 8192:
            raise AuthDenied("AUTH_INPUT_INVALID")
        return hmac.new(
            self._hash_key, (purpose + "\0" + raw).encode(), sha256
        ).hexdigest()

    def seal(self, value: SecretStr, purpose: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return (
            b"v1"
            + nonce
            + self._cipher.encrypt(
                nonce,
                value.get_secret_value().encode(),
                ("auth.v1:" + purpose).encode(),
            )
        )

    def open(self, value: bytes, purpose: str) -> SecretStr:
        try:
            if not 30 <= len(value) <= 9000 or value[:2] != b"v1":
                raise ValueError
            return SecretStr(
                self._cipher.decrypt(
                    value[2:14], value[14:], ("auth.v1:" + purpose).encode()
                ).decode()
            )
        except (InvalidTag, ValueError, UnicodeError):
            raise AuthDenied("AUTH_CUSTODY_INVALID") from None


def opaque(value: SecretStr) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value.get_secret_value()):
        raise AuthDenied("AUTH_REQUIRED")


class AuthJournal:
    def __init__(self, store: Store, vault: AuthVault) -> None:
        self.store, self.vault = store, vault

    def rate(self, peer: str, action: str) -> None:
        """Commit rate accounting separately, including requests later denied."""
        with self.store._connection() as c:
            bucket = self.vault.digest(action, SecretStr(peer))
            limited = c.execute(
                """INSERT INTO auth_rate_limits(bucket,hits) VALUES(%s,1)
                ON CONFLICT(bucket) DO UPDATE SET hits=CASE WHEN
                auth_rate_limits.started_at<clock_timestamp()-interval '1 minute'
                THEN 1 ELSE auth_rate_limits.hits+1 END, started_at=CASE WHEN
                auth_rate_limits.started_at<clock_timestamp()-interval '1 minute'
                THEN clock_timestamp() ELSE auth_rate_limits.started_at END
                RETURNING hits""",
                (bucket,),
            ).fetchone()
            if (
                limited is None
                or not isinstance(limited["hits"], int)
                or limited["hits"] > 10
            ):
                raise AuthDenied("AUTH_RATE_LIMITED")

    def denial(self, code: str) -> None:
        """Append only a bounded code, never an exception body or OAuth input."""
        if not re.fullmatch(r"AUTH_[A-Z_]{1,60}", code):
            code = "AUTH_DENIED"
        with self.store._connection() as c:
            c.execute(
                "INSERT INTO auth_security_events(id,action,decision,reason_code) "
                "VALUES(%s,'AUTH_DENIED','DENY',%s)",
                (uuid4(), code),
            )

    def start(
        self, *, browser: SecretStr | None = None, peer: str = "internal"
    ) -> Attempt:
        """Persist preparation before redirect, bounded per trusted peer."""
        self.rate(peer, "login-start")
        state = SecretStr(secrets.token_urlsafe(32))
        browser = browser or SecretStr(secrets.token_urlsafe(32))
        opaque(browser)
        verifier = SecretStr(secrets.token_urlsafe(32))
        digest = self.vault.digest("state", state)
        with self.store._connection() as c:
            c.execute(
                """INSERT INTO auth_oauth_attempts
                (state_hash,browser_hash,verifier_ciphertext) VALUES(%s,%s,%s)""",
                (
                    digest,
                    self.vault.digest("browser", browser),
                    self.vault.seal(verifier, digest),
                ),
            )
            c.execute(
                "INSERT INTO auth_security_events(id,action) "
                "VALUES(%s,'LOGIN_STARTED')",
                (uuid4(),),
            )
        challenge = (
            base64.urlsafe_b64encode(
                sha256(verifier.get_secret_value().encode()).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        return Attempt(state=state, browser=browser, challenge=challenge)

    def consume(self, state: SecretStr, browser: SecretStr) -> ExchangeClaim:
        """Commit irreversible claim before exchanging the provider's one-use code."""
        opaque(state)
        opaque(browser)
        digest = self.vault.digest("state", state)
        claim = uuid4()
        with self.store._connection() as c:
            row = c.execute(
                """UPDATE auth_oauth_attempts SET consumed_at=clock_timestamp(),claim=%s
                WHERE state_hash=%s AND browser_hash=%s AND consumed_at IS NULL
                AND expires_at>clock_timestamp() RETURNING verifier_ciphertext""",
                (claim, digest, self.vault.digest("browser", browser)),
            ).fetchone()
            if row is None:
                raise AuthDenied("AUTH_ATTEMPT_DENIED")
        ciphertext = row["verifier_ciphertext"]
        if not isinstance(ciphertext, bytes):
            raise AuthDenied("AUTH_CUSTODY_INVALID")
        return ExchangeClaim(
            state_hash=digest,
            claim=claim,
            verifier=self.vault.open(ciphertext, digest),
        )

    def finish(
        self,
        claim: ExchangeClaim,
        user: GitHubUser,
        token: UserToken,
        *,
        origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"],
        old_session: SecretStr | None = None,
    ) -> IssuedSession:
        """Commit stable identity, encrypted credentials and rotated session."""
        user = GitHubUser.model_validate(user.model_dump())
        token = UserToken.model_validate(token.model_dump())
        secret = SecretStr(secrets.token_urlsafe(32))
        with self.store._connection() as c:
            row = c.execute(
                """SELECT state_hash FROM auth_oauth_attempts
                WHERE state_hash=%s AND claim=%s AND consumed_at IS NOT NULL
                AND completed_at IS NULL AND expires_at>clock_timestamp() FOR UPDATE""",
                (claim.state_hash, claim.claim),
            ).fetchone()
            if row is None:
                raise AuthDenied("AUTH_ATTEMPT_DENIED")
            c.execute("SELECT pg_advisory_xact_lock(%s)", (user.id,))
            identity = c.execute(
                """SELECT u.id,u.active FROM auth_users u JOIN
                auth_identities i ON i.user_id=u.id WHERE i.github_id=%s FOR UPDATE""",
                (user.id,),
            ).fetchone()
            if identity is None:
                user_id = uuid4()
                c.execute("INSERT INTO auth_users(id) VALUES(%s)", (user_id,))
                c.execute(
                    """INSERT INTO auth_identities(github_id,user_id,login,avatar_url)
                    VALUES(%s,%s,%s,%s)""",
                    (user.id, user_id, user.login, user.avatar_url),
                )
            else:
                if not identity["active"]:
                    raise AuthDenied("AUTH_USER_DISABLED")
                user_id = UUID(str(identity["id"]))
                c.execute(
                    "UPDATE auth_identities SET login=%s,avatar_url=%s WHERE "
                    "user_id=%s",
                    (user.login, user.avatar_url, user_id),
                )
            c.execute(
                """INSERT INTO auth_credentials(user_id,access_ciphertext,
                refresh_ciphertext,expires_at,refresh_expires_at) VALUES(%s,%s,%s,
                clock_timestamp()+make_interval(secs=>%s),
                clock_timestamp()+make_interval(secs=>%s))
                ON CONFLICT(user_id) DO UPDATE SET
                access_ciphertext=EXCLUDED.access_ciphertext,
                refresh_ciphertext=EXCLUDED.refresh_ciphertext,
                expires_at=EXCLUDED.expires_at,
                refresh_expires_at=EXCLUDED.refresh_expires_at,
                rotation_version=auth_credentials.rotation_version+1""",
                (
                    user_id,
                    self.vault.seal(token.access_token, f"access:{user_id}"),
                    self.vault.seal(token.refresh_token, f"refresh:{user_id}"),
                    token.expires_in,
                    token.refresh_token_expires_in,
                ),
            )
            if old_session is not None:
                opaque(old_session)
                c.execute(
                    """UPDATE auth_sessions SET revoked_at=clock_timestamp()
                    WHERE secret_hash=%s AND revoked_at IS NULL""",
                    (self.vault.digest("session", old_session),),
                )
            c.execute(
                """INSERT INTO auth_sessions(secret_hash,user_id,origin,expires_at)
                VALUES(%s,%s,%s,clock_timestamp()+make_interval(secs=>%s))""",
                (
                    self.vault.digest("session", secret),
                    user_id,
                    origin,
                    min(token.expires_in, 3600),
                ),
            )
            c.execute(
                "UPDATE auth_oauth_attempts SET completed_at=clock_timestamp() "
                "WHERE state_hash=%s",
                (claim.state_hash,),
            )
            c.execute(
                "INSERT INTO auth_security_events(id,actor_id,action) "
                "VALUES(%s,%s,'LOGIN_COMPLETED')",
                (uuid4(), user_id),
            )
        return IssuedSession(user_id=user_id, secret=secret)

    def session(self, secret: SecretStr, *, allow_test: bool = False) -> SessionUser:
        opaque(secret)
        with self.store._connection() as c:
            row = c.execute(
                """SELECT u.id,i.github_id,i.login AS github_login,i.avatar_url
                FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id
                JOIN auth_identities i ON i.user_id=u.id WHERE s.secret_hash=%s
                AND s.expires_at>clock_timestamp() AND s.revoked_at IS NULL AND u.active
                AND (s.origin='REMOTE_SERVICE' OR %s)""",
                (self.vault.digest("session", secret), allow_test is True),
            ).fetchone()
            if row is None:
                raise AuthDenied("AUTH_REQUIRED")
            c.execute(
                """UPDATE auth_sessions SET last_seen=clock_timestamp()
                WHERE secret_hash=%s
                AND last_seen<clock_timestamp()-interval '1 minute' """,
                (self.vault.digest("session", secret),),
            )
            return SessionUser.model_validate(row)

    def logout(self, secret: SecretStr) -> None:
        opaque(secret)
        with self.store._connection() as c:
            row = c.execute(
                """UPDATE auth_sessions SET revoked_at=clock_timestamp()
                WHERE secret_hash=%s AND revoked_at IS NULL RETURNING user_id""",
                (self.vault.digest("session", secret),),
            ).fetchone()
            if row is not None:
                c.execute(
                    "INSERT INTO auth_security_events(id,actor_id,action) "
                    "VALUES(%s,%s,'LOGOUT')",
                    (uuid4(), row["user_id"]),
                )
