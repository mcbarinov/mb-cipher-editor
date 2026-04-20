"""Tests for core.crypto.

Uses a cheap scrypt cost (log_n=10, the documented minimum) everywhere to keep
the suite fast. Production default is DEFAULT_LOG_N = 17.
"""

import pytest

from mb_cipher_editor.core import crypto
from mb_cipher_editor.core.crypto import (
    _HEADER_STRUCT,
    HEADER_SIZE,
    MAGIC,
    NONCE_SIZE,
    SALT_SIZE,
    VERSION,
    DecryptionError,
    decrypt,
    encrypt,
)

FAST_LOG_N = 10  # Cheap scrypt cost for tests; production uses DEFAULT_LOG_N

# Derived offsets into the header — computed from sizes, not duplicated constants.
SALT_OFFSET = HEADER_SIZE - NONCE_SIZE - SALT_SIZE  # = 8
NONCE_OFFSET = HEADER_SIZE - NONCE_SIZE  # = 24


def _parse_header(blob: bytes) -> tuple[bytes, int, int, int, int, bytes, bytes]:
    """Unpack the header for assertions."""
    return _HEADER_STRUCT.unpack(blob[:HEADER_SIZE])  # type: ignore[no-any-return]


class TestRoundTrip:
    """encrypt → decrypt recovers original plaintext."""

    @pytest.mark.parametrize(
        "plaintext",
        [
            b"",
            b"hello",
            b"a" * 1024,
            b"unicode: \xd0\xbf\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82",
            bytes(range(256)) * 16,
        ],
    )
    def test_round_trip(self, plaintext: bytes) -> None:
        """Various payloads round-trip cleanly."""
        blob = encrypt(plaintext, "correct horse battery staple", log_n=FAST_LOG_N)
        assert decrypt(blob, "correct horse battery staple") == plaintext

    def test_non_ascii_password(self) -> None:
        """Passwords with non-ASCII characters round-trip."""
        blob = encrypt(b"payload", "пароль-秘密-🔒", log_n=FAST_LOG_N)
        assert decrypt(blob, "пароль-秘密-🔒") == b"payload"


class TestHeader:
    """Header layout matches docs/cryptography.md."""

    def test_magic_and_version(self) -> None:
        """Header starts with MBCE + version byte."""
        magic, version, *_ = _parse_header(encrypt(b"x", "pw", log_n=FAST_LOG_N))
        assert magic == MAGIC
        assert version == VERSION

    def test_header_size(self) -> None:
        """Header is exactly 36 bytes; body = ciphertext + 16-byte tag."""
        blob = encrypt(b"12345", "pw", log_n=FAST_LOG_N)
        assert HEADER_SIZE == 36
        assert len(blob) == HEADER_SIZE + len(b"12345") + 16

    def test_kdf_params_embedded(self) -> None:
        """KDF params stored in the header are what was passed to encrypt."""
        blob = encrypt(b"x", "pw", log_n=FAST_LOG_N, r=4, p=2)
        _, _, log_n, r, p, _, _ = _parse_header(blob)
        assert log_n == FAST_LOG_N
        assert r == 4
        assert p == 2


class TestUniqueness:
    """Fresh salt and nonce on every call."""

    def test_salt_and_nonce_change(self) -> None:
        """Two encryptions of the same plaintext produce different salt, nonce, and ciphertext."""
        a = encrypt(b"same", "pw", log_n=FAST_LOG_N)
        b = encrypt(b"same", "pw", log_n=FAST_LOG_N)
        _, _, _, _, _, salt_a, nonce_a = _parse_header(a)
        _, _, _, _, _, salt_b, nonce_b = _parse_header(b)
        assert salt_a != salt_b
        assert nonce_a != nonce_b
        assert a[HEADER_SIZE:] != b[HEADER_SIZE:]


class TestDecryptionFailures:
    """Any authentication failure surfaces as DecryptionError."""

    def test_wrong_password(self) -> None:
        """Wrong password raises DecryptionError."""
        blob = encrypt(b"secret", "right", log_n=FAST_LOG_N)
        with pytest.raises(DecryptionError):
            decrypt(blob, "wrong")

    def test_tampered_body(self) -> None:
        """Flipping a ciphertext byte fails authentication."""
        blob = bytearray(encrypt(b"secret", "pw", log_n=FAST_LOG_N))
        blob[HEADER_SIZE] ^= 0x01
        with pytest.raises(DecryptionError):
            decrypt(bytes(blob), "pw")

    def test_tampered_tag(self) -> None:
        """Flipping a byte inside the GCM tag fails authentication."""
        blob = bytearray(encrypt(b"secret", "pw", log_n=FAST_LOG_N))
        blob[-1] ^= 0x01
        with pytest.raises(DecryptionError):
            decrypt(bytes(blob), "pw")

    def test_tampered_salt(self) -> None:
        """Header is bound via AAD; altering the salt fails authentication."""
        blob = bytearray(encrypt(b"secret", "pw", log_n=FAST_LOG_N))
        blob[SALT_OFFSET] ^= 0x01
        with pytest.raises(DecryptionError):
            decrypt(bytes(blob), "pw")

    def test_tampered_nonce(self) -> None:
        """Altering the nonce fails authentication."""
        blob = bytearray(encrypt(b"secret", "pw", log_n=FAST_LOG_N))
        blob[NONCE_OFFSET] ^= 0x01
        with pytest.raises(DecryptionError):
            decrypt(bytes(blob), "pw")

    def test_bad_magic(self) -> None:
        """Files without the MBCE prefix are rejected."""
        blob = bytearray(encrypt(b"secret", "pw", log_n=FAST_LOG_N))
        blob[0] = ord("X")
        with pytest.raises(DecryptionError):
            decrypt(bytes(blob), "pw")

    def test_unknown_version(self) -> None:
        """Unknown format versions are rejected."""
        blob = bytearray(encrypt(b"secret", "pw", log_n=FAST_LOG_N))
        blob[4] = 0xFF
        with pytest.raises(DecryptionError):
            decrypt(bytes(blob), "pw")

    def test_truncated_below_header(self) -> None:
        """A blob smaller than a header is rejected."""
        with pytest.raises(DecryptionError):
            decrypt(b"MBCE\x01", "pw")

    def test_truncated_body(self) -> None:
        """Missing tag bytes fail authentication."""
        blob = encrypt(b"secret", "pw", log_n=FAST_LOG_N)
        with pytest.raises(DecryptionError):
            decrypt(blob[:-1], "pw")

    @pytest.mark.parametrize(
        ("offset", "value"),
        [
            (5, 40),  # log_n way above MAX_LOG_N (would be ~17 TiB of scrypt memory)
            (5, 9),  # log_n below MIN_LOG_N
            (6, 0),  # r below MIN_R
            (6, 33),  # r above MAX_R
            (7, 0),  # p below MIN_P
            (7, 17),  # p above MAX_P
        ],
    )
    def test_out_of_range_kdf_params_rejected(self, offset: int, value: int) -> None:
        """A header with out-of-range KDF params must fail as DecryptionError, not leak MemoryError/ValueError."""
        blob = bytearray(encrypt(b"secret", "pw", log_n=FAST_LOG_N))
        blob[offset] = value
        with pytest.raises(DecryptionError):
            decrypt(bytes(blob), "pw")


class TestEncryptValidation:
    """encrypt() rejects out-of-range KDF args before doing any work."""

    @pytest.mark.parametrize(
        ("log_n", "r", "p"),
        [
            (9, 8, 1),
            (21, 8, 1),
            (17, 0, 1),
            (17, 33, 1),
            (17, 8, 0),
            (17, 8, 17),
        ],
    )
    def test_rejects_out_of_range(self, log_n: int, r: int, p: int) -> None:
        """Out-of-range KDF args raise ValueError."""
        with pytest.raises(ValueError, match=r"(log_n|r|p) must be in"):
            encrypt(b"x", "pw", log_n=log_n, r=r, p=p)


class TestDefaults:
    """Module-level defaults reflect the spec."""

    def test_default_log_n(self) -> None:
        """Default log_n is 17 per spec."""
        assert crypto.DEFAULT_LOG_N == 17

    def test_default_r_p(self) -> None:
        """Default r and p per spec."""
        assert crypto.DEFAULT_R == 8
        assert crypto.DEFAULT_P == 1

    def test_param_caps(self) -> None:
        """Parameter caps match docs/cryptography.md."""
        assert crypto.MIN_LOG_N == 10
        assert crypto.MAX_LOG_N == 20
        assert crypto.MIN_R == 1
        assert crypto.MAX_R == 32
        assert crypto.MIN_P == 1
        assert crypto.MAX_P == 16
