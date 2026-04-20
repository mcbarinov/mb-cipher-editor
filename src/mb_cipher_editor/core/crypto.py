"""Password-based file encryption — Tarsnap ``scrypt(1)`` format.

Implements the file layout specified in ``docs/crypto.md``, which mirrors
the reference format at https://github.com/Tarsnap/scrypt/blob/master/FORMAT.

Files produced here are decryptable by ``scrypt dec file`` on any platform
where the `scrypt` CLI is installed; files produced by ``scrypt enc`` are
decryptable here. The code MUST match that spec exactly — if the spec
changes, update both together.

``docs/crypto.md`` also records why this format was chosen over the
alternatives (custom, age v1, JWE).
"""

import hashlib
import hmac
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# Format constants (see spec in docs/crypto.md).
MAGIC = b"scrypt"  # 6-byte file signature
VERSION = 0  # Current (and only) format version

# Field sizes (bytes).
HEADER_PREFIX_SIZE = 48  # magic(6) + version(1) + log_n(1) + r(4) + p(4) + salt(32)
HEADER_CHECKSUM_SIZE = 16  # First 16 bytes of SHA-256(header_prefix)
HEADER_MAC_SIZE = 32  # HMAC-SHA-256(hmac_key, header_prefix || header_checksum)
HEADER_SIZE = HEADER_PREFIX_SIZE + HEADER_CHECKSUM_SIZE + HEADER_MAC_SIZE  # = 96
FILE_MAC_SIZE = 32  # HMAC-SHA-256(hmac_key, everything before it)
SALT_SIZE = 32  # scrypt salt
AES_KEY_SIZE = 32  # AES-256
HMAC_KEY_SIZE = 32  # HMAC-SHA-256 key
KDF_OUTPUT_SIZE = AES_KEY_SIZE + HMAC_KEY_SIZE  # 64 bytes from one scrypt call
AES_IV_SIZE = 16  # All-zero IV; safe because salt is unique per file → key is unique

# Default scrypt work factor.
DEFAULT_LOG_N = 17  # N = 2^17 = 131072; ~100 ms, ~128 MiB on a modern CPU
DEFAULT_R = 8
DEFAULT_P = 1

# Sanity caps on KDF params (see spec § "Parameter caps"). Bounds enforced on both
# encrypt and decrypt to keep "any failure → single error type" honest and prevent
# a hostile file from forcing a scrypt memory bomb.
MIN_LOG_N = 10
MAX_LOG_N = 20
MIN_R = 1
MAX_R = 32
MIN_P = 1
MAX_P = 16

# Header layout: 6-byte magic, version byte, log_n byte, 32-bit r, 32-bit p, 32-byte salt.
_HEADER_PREFIX_STRUCT = struct.Struct(">6sBBII32s")


class DecryptionError(Exception):
    """Raised when a scrypt(1) file cannot be authenticated under the given password.

    All failure modes collapse into this single type: wrong password, tampered
    header, tampered ciphertext, out-of-range KDF params, unknown version,
    corrupted checksum. Distinguishing would give an attacker an oracle.
    """


def encrypt(
    plaintext: bytes,
    password: str,
    *,
    log_n: int = DEFAULT_LOG_N,
    r: int = DEFAULT_R,
    p: int = DEFAULT_P,
) -> bytes:
    """Encrypt ``plaintext`` under ``password``. Returns a full scrypt(1)-format file blob."""
    _check_kdf_params(log_n, r, p)
    salt = os.urandom(SALT_SIZE)
    header_prefix = _HEADER_PREFIX_STRUCT.pack(MAGIC, VERSION, log_n, r, p, salt)
    aes_key, hmac_key = _derive_keys(password, salt, log_n, r, p)

    header_checksum = hashlib.sha256(header_prefix).digest()[:HEADER_CHECKSUM_SIZE]
    header_mac = hmac.new(hmac_key, header_prefix + header_checksum, hashlib.sha256).digest()
    header = header_prefix + header_checksum + header_mac

    cipher = Cipher(algorithms.AES(aes_key), modes.CTR(b"\x00" * AES_IV_SIZE)).encryptor()
    ciphertext = cipher.update(plaintext) + cipher.finalize()

    file_mac = hmac.new(hmac_key, header + ciphertext, hashlib.sha256).digest()
    return header + ciphertext + file_mac


def decrypt(blob: bytes, password: str) -> bytes:
    """Decrypt a scrypt(1)-format file. Raises ``DecryptionError`` on any failure."""
    if len(blob) < HEADER_SIZE + FILE_MAC_SIZE:
        raise DecryptionError

    header_prefix = blob[:HEADER_PREFIX_SIZE]
    header_checksum = blob[HEADER_PREFIX_SIZE : HEADER_PREFIX_SIZE + HEADER_CHECKSUM_SIZE]
    header_mac = blob[HEADER_PREFIX_SIZE + HEADER_CHECKSUM_SIZE : HEADER_SIZE]
    ciphertext = blob[HEADER_SIZE:-FILE_MAC_SIZE]
    file_mac = blob[-FILE_MAC_SIZE:]

    magic, version, log_n, r, p, salt = _HEADER_PREFIX_STRUCT.unpack(header_prefix)
    if magic != MAGIC or version != VERSION:
        raise DecryptionError

    # Cheap integrity check first — catches random corruption without running scrypt.
    expected_checksum = hashlib.sha256(header_prefix).digest()[:HEADER_CHECKSUM_SIZE]
    if not hmac.compare_digest(expected_checksum, header_checksum):
        raise DecryptionError

    # KDF params + scrypt + MAC checks share one error path. Out-of-range, DoS-ish
    # configs, MAC mismatches, and wrong passwords all surface as DecryptionError.
    try:
        _check_kdf_params(log_n, r, p)
        aes_key, hmac_key = _derive_keys(password, salt, log_n, r, p)
    except (ValueError, MemoryError, OverflowError) as e:
        raise DecryptionError from e

    expected_header_mac = hmac.new(hmac_key, header_prefix + header_checksum, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_header_mac, header_mac):
        raise DecryptionError

    expected_file_mac = hmac.new(hmac_key, blob[:-FILE_MAC_SIZE], hashlib.sha256).digest()
    if not hmac.compare_digest(expected_file_mac, file_mac):
        raise DecryptionError

    # Authenticate-before-decrypt: AES runs only after both MACs pass.
    cipher = Cipher(algorithms.AES(aes_key), modes.CTR(b"\x00" * AES_IV_SIZE)).decryptor()
    return cipher.update(ciphertext) + cipher.finalize()


def _derive_keys(password: str, salt: bytes, log_n: int, r: int, p: int) -> tuple[bytes, bytes]:
    """One scrypt call → 64 bytes → (AES-256 key, HMAC-SHA-256 key)."""
    material = Scrypt(salt=salt, length=KDF_OUTPUT_SIZE, n=1 << log_n, r=r, p=p).derive(password.encode("utf-8"))
    return material[:AES_KEY_SIZE], material[AES_KEY_SIZE:]


def _check_kdf_params(log_n: int, r: int, p: int) -> None:
    """Reject KDF params outside the caps documented in docs/crypto.md."""
    if not MIN_LOG_N <= log_n <= MAX_LOG_N:
        raise ValueError(f"log_n must be in [{MIN_LOG_N}, {MAX_LOG_N}]: got {log_n}")
    if not MIN_R <= r <= MAX_R:
        raise ValueError(f"r must be in [{MIN_R}, {MAX_R}]: got {r}")
    if not MIN_P <= p <= MAX_P:
        raise ValueError(f"p must be in [{MIN_P}, {MAX_P}]: got {p}")
