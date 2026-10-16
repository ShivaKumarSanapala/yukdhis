"""Reversible masking vault for the local backend.

Each session gets ``{{TYPE_N}}`` tokens.  Originals are held only in process
memory, AES-GCM encrypted with a per-process key, and expire after a TTL.
On GCP the vault is replaced by Cloud DLP crypto-deterministic surrogates
(KMS-wrapped key), which are reversible without storing any mapping.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TOKEN = re.compile(r"\{\{([A-Z_]+)_(\d+)\}\}")
DEFAULT_TTL_SECONDS = 3600


@dataclass
class _SessionVault:
    created: float = field(default_factory=time.monotonic)
    forward: dict[str, str] = field(default_factory=dict)       # HMAC(original) -> token
    reverse: dict[str, bytes] = field(default_factory=dict)     # token -> nonce+ciphertext
    counters: dict[str, int] = field(default_factory=dict)


class TokenVault:
    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._aead = AESGCM(AESGCM.generate_key(bit_length=256))
        self._mac_key = os.urandom(32)
        self._sessions: dict[str, _SessionVault] = {}
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def _session(self, session_id: str) -> _SessionVault:
        now = time.monotonic()
        for key in [k for k, v in self._sessions.items() if now - v.created > self._ttl]:
            del self._sessions[key]
        return self._sessions.setdefault(session_id, _SessionVault())

    def tokenize(self, session_id: str, original: str, entity_type: str) -> str:
        with self._lock:
            vault = self._session(session_id)
            digest = hmac.new(self._mac_key, f"{session_id}\x00{original}".encode(), hashlib.sha256).hexdigest()
            if digest in vault.forward:
                return vault.forward[digest]
            index = vault.counters.get(entity_type, 0) + 1
            vault.counters[entity_type] = index
            token = f"{{{{{entity_type}_{index}}}}}"
            nonce = os.urandom(12)
            vault.reverse[token] = nonce + self._aead.encrypt(nonce, original.encode(), session_id.encode())
            vault.forward[digest] = token
            return token

    def detokenize(self, session_id: str, text: str) -> str:
        with self._lock:
            vault = self._sessions.get(session_id)
            if not vault:
                return text

            def swap(match: re.Match) -> str:
                blob = vault.reverse.get(match.group(0))
                return self._aead.decrypt(blob[:12], blob[12:], session_id.encode()).decode() if blob else match.group(0)

            return TOKEN.sub(swap, text)

    def purge(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


VAULT = TokenVault()
