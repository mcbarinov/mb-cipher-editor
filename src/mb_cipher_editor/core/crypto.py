"""Password-based file encryption.

Implements the format specified in ``docs/cryptography.md``. The code here
MUST match that document exactly. If the format changes, bump ``VERSION``
and update both the spec and this module together.
"""

import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"MBCE"  # File signature, first 4 bytes
VERSION = 1  # Current format version
HEADER_SIZE = 36  # magic(4) + version(1) + log_n(1) + r(1) + p(1) + salt(16) + nonce(12)
SALT_SIZE = 16  # scrypt salt size in bytes
NONCE_SIZE = 12  # AES-GCM nonce size in bytes
KEY_SIZE = 32  # AES-256 key size in bytes

DEFAULT_LOG_N = 17  # scrypt N = 2^17 = 131072; ~100ms, ~128 MiB on a modern CPU
DEFAULT_R = 8  # scrypt block size parameter
DEFAULT_P = 1  # scrypt parallelism parameter

# Sanity caps on KDF params (see docs/cryptography.md § "Parameter caps").
# Bounds are enforced on both encrypt and decrypt to keep "any failure is a single
# error type" honest and to prevent a hostile file from forcing a scrypt memory bomb.
MIN_LOG_N = 10
MAX_LOG_N = 20
MIN_R = 1
MAX_R = 32
MIN_P = 1
MAX_P = 16

_HEADER_STRUCT = struct.Struct(f">4sBBBB{SALT_SIZE}s{NONCE_SIZE}s")


class DecryptionError(Exception):
    """Raised when a ciphertext cannot be authenticated under the given password.

    The cause may be a wrong password, a corrupted file, tampering, or an
    unknown/unsupported format version. These cases are intentionally not
    distinguished — leaking which one occurred would give an attacker an oracle.
    """


def encrypt(
    plaintext: bytes,
    password: str,
    *,
    log_n: int = DEFAULT_LOG_N,
    r: int = DEFAULT_R,
    p: int = DEFAULT_P,
) -> bytes:
    """Encrypt ``plaintext`` under ``password``. Returns the full file blob (header + body).

    Raises ``ValueError`` if KDF params are outside the documented caps — this is a
    caller bug, we never want to write a file we would refuse to read.
    """
    _check_kdf_params(log_n, r, p)
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    header = _HEADER_STRUCT.pack(MAGIC, VERSION, log_n, r, p, salt, nonce)
    key = _derive_key(password, salt, log_n, r, p)
    body = AESGCM(key).encrypt(nonce, plaintext, header)
    return header + body


def decrypt(blob: bytes, password: str) -> bytes:
    """Decrypt ``blob`` under ``password``. Raises ``DecryptionError`` on any failure."""
    if len(blob) < HEADER_SIZE:
        raise DecryptionError
    header = blob[:HEADER_SIZE]
    body = blob[HEADER_SIZE:]
    magic, version, log_n, r, p, salt, nonce = _HEADER_STRUCT.unpack(header)
    if magic != MAGIC or version != VERSION:
        raise DecryptionError
    # Param caps, scrypt, and AEAD all share one error path. A bad header field,
    # a DoS-ish scrypt config, and a wrong password all surface as DecryptionError —
    # we don't want to leak which check failed (no padding oracle, no DoS oracle).
    try:
        _check_kdf_params(log_n, r, p)
        key = _derive_key(password, salt, log_n, r, p)
        return AESGCM(key).decrypt(nonce, body, header)
    except (InvalidTag, ValueError, MemoryError, OverflowError) as e:
        raise DecryptionError from e


def _check_kdf_params(log_n: int, r: int, p: int) -> None:
    """Reject KDF params outside the caps documented in docs/cryptography.md."""
    if not MIN_LOG_N <= log_n <= MAX_LOG_N:
        raise ValueError(f"log_n must be in [{MIN_LOG_N}, {MAX_LOG_N}]: got {log_n}")
    if not MIN_R <= r <= MAX_R:
        raise ValueError(f"r must be in [{MIN_R}, {MAX_R}]: got {r}")
    if not MIN_P <= p <= MAX_P:
        raise ValueError(f"p must be in [{MIN_P}, {MAX_P}]: got {p}")


def _derive_key(password: str, salt: bytes, log_n: int, r: int, p: int) -> bytes:
    """Derive an AES-256 key from ``password`` via scrypt with the given parameters."""
    kdf = Scrypt(salt=salt, length=KEY_SIZE, n=1 << log_n, r=r, p=p)
    return kdf.derive(password.encode("utf-8"))
