"""age v1 file encryption — passphrase subset.

Implements the age v1 file format (https://github.com/C2SP/C2SP/blob/main/age.md)
restricted to the ``scrypt`` passphrase stanza. Output is binary age that
``age -d`` can decrypt; input includes anything ``age -e -p`` (or ``rage``)
produces in passphrase mode.

Recipient identities (X25519 public keys) and ASCII armor are intentionally
NOT supported — we are a passphrase-only editor.

This module exists as a SIDE-BY-SIDE alternative to ``core.crypto_custom``
(the custom MBCE format) and ``core.crypto_scrypt`` (Tarsnap scrypt(1)
format) so the complexity trade-off can be compared directly.
"""

import base64
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.hmac import HMAC
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# --- age v1 spec constants ---
V1_HEADER_LINE = b"age-encryption.org/v1"  # First line of every age v1 file
SCRYPT_LABEL = b"age-encryption.org/v1/scrypt"  # Domain-separation prefix for scrypt salt
HKDF_HEADER_INFO = b"header"  # HKDF info for deriving the header-MAC key
HKDF_PAYLOAD_INFO = b"payload"  # HKDF info for deriving the STREAM key

# --- sizes (bytes) ---
FILE_KEY_SIZE = 16  # The per-file symmetric key wrapped by the scrypt-derived key
SCRYPT_SALT_SIZE = 16  # Random salt written in the stanza (LABEL is prepended before scrypt)
STREAM_NONCE_SIZE = 16  # Random prefix that salts the payload HKDF
CHACHA_TAG_SIZE = 16  # Poly1305 authentication tag appended to each AEAD block
PAYLOAD_CHUNK_SIZE = 64 * 1024  # Plaintext bytes per STREAM chunk
CHACHA_CHUNK_SIZE = PAYLOAD_CHUNK_SIZE + CHACHA_TAG_SIZE  # Ciphertext size of a non-final chunk
WRAP_KEY_SIZE = 32  # ChaCha20-Poly1305 key length
MAC_SIZE = 32  # HMAC-SHA256 output length

# --- scrypt work factor ---
DEFAULT_LOG_N = 18  # age CLI default (N = 2^18 = 262144); ~200 ms, ~256 MiB
MIN_LOG_N = 1  # Permissive lower bound to stay interop-compatible
MAX_LOG_N = 22  # age's own decryption cap; above this it refuses files to prevent DoS


class DecryptionError(Exception):
    """Raised when an age file cannot be authenticated under the given password."""


def encrypt(plaintext: bytes, password: str, *, log_n: int = DEFAULT_LOG_N) -> bytes:
    """Encrypt ``plaintext`` as a binary age v1 passphrase file."""
    if not MIN_LOG_N <= log_n <= MAX_LOG_N:
        raise ValueError(f"log_n must be in [{MIN_LOG_N}, {MAX_LOG_N}]: got {log_n}")
    file_key = os.urandom(FILE_KEY_SIZE)
    salt = os.urandom(SCRYPT_SALT_SIZE)
    wrap_key = _scrypt_key(password, salt, log_n)
    # Wrap the file key under the scrypt-derived key with an all-zero nonce (per spec).
    wrapped_file_key = ChaCha20Poly1305(wrap_key).encrypt(b"\x00" * 12, file_key, None)
    header_body = _build_header_body(salt, log_n, wrapped_file_key)
    mac = _header_mac(file_key, header_body)
    header = header_body + b" " + _b64enc(mac) + b"\n"
    # Fresh 16-byte random nonce salts the payload HKDF — rebinds the STREAM key per file.
    stream_nonce = os.urandom(STREAM_NONCE_SIZE)
    payload_key = _hkdf(file_key, stream_nonce, HKDF_PAYLOAD_INFO)
    return header + stream_nonce + _stream_encrypt(payload_key, plaintext)


def decrypt(blob: bytes, password: str) -> bytes:
    """Decrypt a binary age v1 passphrase file. Raises ``DecryptionError`` on any failure.

    Failure modes (wrong password, tampered header, corrupted body, out-of-range KDF
    params, malformed base64) all collapse into a single error — no oracle for attackers.
    """
    try:
        return _decrypt(blob, password)
    except (InvalidTag, ValueError, MemoryError, OverflowError, IndexError, UnicodeDecodeError) as e:
        raise DecryptionError from e


def _decrypt(blob: bytes, password: str) -> bytes:
    """Full age-file decrypt. Can raise DecryptionError or library exceptions (caught above)."""
    # Locate the end of the header. The header is ASCII, terminated by "--- <mac>\n";
    # we find the "\n---" token then the next newline. "---" never appears inside the
    # standard base64 alphabet, so this search is unambiguous for well-formed files.
    try:
        triple = blob.index(b"\n---")
    except ValueError as e:
        raise DecryptionError from e
    mac_line_end = blob.find(b"\n", triple + 4)
    if mac_line_end < 0:
        raise DecryptionError
    header_bytes = blob[:mac_line_end]
    payload = blob[mac_line_end + 1 :]

    lines = header_bytes.split(b"\n")
    if len(lines) != 4 or lines[0] != V1_HEADER_LINE:
        raise DecryptionError

    # Line 1: "-> scrypt <b64 salt> <decimal log_n>"
    stanza = lines[1].split(b" ")
    if len(stanza) != 4 or stanza[0] != b"->" or stanza[1] != b"scrypt":
        raise DecryptionError
    salt = _b64dec(stanza[2])
    log_n = int(stanza[3])
    if len(salt) != SCRYPT_SALT_SIZE or not MIN_LOG_N <= log_n <= MAX_LOG_N:
        raise DecryptionError

    # Line 2: base64 of the wrapped file key (16-byte file key + 16-byte AEAD tag).
    wrapped_file_key = _b64dec(lines[2])
    if len(wrapped_file_key) != FILE_KEY_SIZE + CHACHA_TAG_SIZE:
        raise DecryptionError

    # Line 3: "--- <b64 header mac>"
    tail = lines[3].split(b" ")
    if len(tail) != 2 or tail[0] != b"---":
        raise DecryptionError
    provided_mac = _b64dec(tail[1])
    if len(provided_mac) != MAC_SIZE:
        raise DecryptionError

    header_body = b"\n".join(lines[:3]) + b"\n---"
    wrap_key = _scrypt_key(password, salt, log_n)
    file_key = ChaCha20Poly1305(wrap_key).decrypt(b"\x00" * 12, wrapped_file_key, None)

    expected_mac = _header_mac(file_key, header_body)
    if not hmac.compare_digest(expected_mac, provided_mac):
        raise DecryptionError

    if len(payload) < STREAM_NONCE_SIZE:
        raise DecryptionError
    stream_nonce = payload[:STREAM_NONCE_SIZE]
    body = payload[STREAM_NONCE_SIZE:]
    payload_key = _hkdf(file_key, stream_nonce, HKDF_PAYLOAD_INFO)
    return _stream_decrypt(payload_key, body)


def _scrypt_key(password: str, salt: bytes, log_n: int) -> bytes:
    """Derive the 32-byte wrap key via scrypt with age's fixed r=8, p=1 and LABEL prefix."""
    return Scrypt(salt=SCRYPT_LABEL + salt, length=WRAP_KEY_SIZE, n=1 << log_n, r=8, p=1).derive(password.encode("utf-8"))


def _build_header_body(salt: bytes, log_n: int, wrapped_file_key: bytes) -> bytes:
    """Serialize the header lines that get MAC'd — everything up to and including '---'."""
    return (
        V1_HEADER_LINE
        + b"\n-> scrypt "
        + _b64enc(salt)
        + b" "
        + str(log_n).encode("ascii")
        + b"\n"
        + _b64enc(wrapped_file_key)
        + b"\n---"
    )


def _header_mac(file_key: bytes, header_body: bytes) -> bytes:
    """HMAC-SHA256 of the header body under a key derived from the file key."""
    hmac_key = _hkdf(file_key, b"", HKDF_HEADER_INFO)
    h = HMAC(hmac_key, hashes.SHA256())
    h.update(header_body)
    return h.finalize()


def _hkdf(ikm: bytes, salt: bytes, info: bytes) -> bytes:
    """HKDF-SHA256 to a 32-byte output. Empty salt is remapped to None so pyca matches RFC 5869."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt or None, info=info).derive(ikm)


def _stream_encrypt(payload_key: bytes, plaintext: bytes) -> bytes:
    """Apply age's STREAM construction over ChaCha20-Poly1305 — chunked AEAD with last-flag nonce."""
    aead = ChaCha20Poly1305(payload_key)
    chunks: list[bytes] = []
    # Emit every full 64-KiB slice as a non-final chunk. If the plaintext is an exact
    # multiple of the chunk size (including 0), we still close with an empty final chunk
    # — this matches the age reference implementation and is required for round-trip.
    num_non_final = len(plaintext) // PAYLOAD_CHUNK_SIZE
    for i in range(num_non_final):
        start = i * PAYLOAD_CHUNK_SIZE
        chunks.append(aead.encrypt(_stream_nonce(i, last=False), plaintext[start : start + PAYLOAD_CHUNK_SIZE], None))
    last_plain = plaintext[num_non_final * PAYLOAD_CHUNK_SIZE :]
    chunks.append(aead.encrypt(_stream_nonce(num_non_final, last=True), last_plain, None))
    return b"".join(chunks)


def _stream_decrypt(payload_key: bytes, body: bytes) -> bytes:
    """Inverse of ``_stream_encrypt``. Chunk boundaries are implied by the remaining length."""
    aead = ChaCha20Poly1305(payload_key)
    out: list[bytes] = []
    offset = 0
    counter = 0
    while True:
        remaining = len(body) - offset
        if remaining < CHACHA_TAG_SIZE:
            raise DecryptionError
        # A chunk that doesn't exceed a full ciphertext block is the final one.
        if remaining <= CHACHA_CHUNK_SIZE:
            out.append(aead.decrypt(_stream_nonce(counter, last=True), body[offset:], None))
            return b"".join(out)
        out.append(aead.decrypt(_stream_nonce(counter, last=False), body[offset : offset + CHACHA_CHUNK_SIZE], None))
        offset += CHACHA_CHUNK_SIZE
        counter += 1


def _stream_nonce(counter: int, *, last: bool) -> bytes:
    """12-byte STREAM nonce: 88-bit BE counter || 1-byte last-flag (0x01 on the final chunk)."""
    return counter.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


def _b64enc(data: bytes) -> bytes:
    """Encode to standard base64 (RFC 4648) with trailing '=' stripped — age's canonical form."""
    return base64.standard_b64encode(data).rstrip(b"=")


def _b64dec(data: bytes) -> bytes:
    """Inverse of ``_b64enc``; restores padding for the stdlib decoder."""
    return base64.standard_b64decode(data + b"=" * ((-len(data)) % 4))
