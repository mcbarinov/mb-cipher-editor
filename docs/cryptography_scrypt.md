# Cryptography — Tarsnap `scrypt(1)` format (variant)

> **Status:** one of several variants being prototyped side-by-side. See also `cryptography_custom.md` (custom MBCE) and (pending) `cryptography_age.md` (age v1).

This document is the **source of truth** for the `scrypt(1)` variant. The code in `core/crypto_scrypt.py` must match this spec exactly.

## Goals

1. Strong password-based file encryption. A file is unrecoverable without the password.
2. **No runtime dependency on external CLI tools.** Encryption and decryption work entirely inside the Python package using only [`pyca/cryptography`](https://github.com/pyca/cryptography).
3. **Third-party recovery possible with one command.** Files produced by this module are decryptable by the reference `scrypt(1)` CLI (`scrypt dec file`); files produced by `scrypt enc file` are decryptable by this module.
4. Trusted cryptographic primitives only. No hand-rolled crypto.

## Format reference

The on-disk layout is the Tarsnap `scrypt` file format, version 0:

**[FORMAT specification](https://github.com/Tarsnap/scrypt/blob/master/FORMAT)** (upstream, authoritative).

Algorithm author = utility author = Colin Percival. Format is stable since 2009.

## Tool compatibility

Files produced by this module:
- `scrypt dec file.enc [plain]` — decrypts to stdout or `plain`
- `scrypt info file.enc` — reads KDF parameters without the password

Package availability:
- macOS: `brew install scrypt`
- Debian/Ubuntu: `apt install scrypt`
- Fedora/RHEL: `dnf install scrypt`
- Arch: `pacman -S scrypt`
- Source: https://github.com/Tarsnap/scrypt

Windows is an explicit non-goal.

## Primitives

| Role | Algorithm | Source |
| --- | --- | --- |
| Password KDF | scrypt (RFC 7914) | `cryptography.hazmat.primitives.kdf.scrypt.Scrypt` |
| Cipher | AES-256-CTR | `cryptography.hazmat.primitives.ciphers` |
| Authentication | HMAC-SHA-256 | `hmac` + `hashlib` (stdlib) |
| Hash | SHA-256 | `hashlib` (stdlib) |
| Randomness | OS CSPRNG | `os.urandom` |

### Construction notes

- **Encrypt-then-MAC, not AEAD.** The `scrypt(1)` format predates widespread AEAD use. It combines AES-256-CTR with two HMAC-SHA-256 tags (one over the header, one over the whole file). Cryptographically equivalent to AEAD given proper key separation; different pattern, same security.
- **Key separation via KDF output split.** A single scrypt call produces 64 bytes: the first 32 bytes are the AES-CTR key, the last 32 bytes are the HMAC key. No shared keying material between cipher and MAC.
- **All-zero AES IV is safe here.** The AES key is unique per file (fresh 32-byte salt → unique scrypt output). Reusing the zero counter is only dangerous when keys repeat, which cannot happen.

## File layout (version 0)

All multi-byte integers are big-endian.

```
Offset   Size   Field                         Notes
------   ----   ---------------------------   ------------------------------------------
0        6      magic                         ASCII "scrypt"
6        1     version                        0x00
7        1     log_n                          scrypt cost: N = 2 ** log_n   (default 17)
8        4     r                              scrypt block size             (default 8)
12       4     p                              scrypt parallelism            (default 1)
16      32     salt                           os.urandom(32) — fresh per encryption
48      16     header_checksum                SHA-256(bytes 0..47)[:16]
64      32     header_mac                     HMAC-SHA-256(hmac_key, bytes 0..63)
96       X     ciphertext                     AES-256-CTR(aes_key, iv=0^128, plaintext)
96+X    32     file_mac                       HMAC-SHA-256(hmac_key, bytes 0..96+X-1)
```

Total overhead: **128 bytes**, regardless of plaintext length. Ciphertext is exactly the same length as plaintext (CTR mode, no padding).

### Key derivation

```
material = scrypt(password=utf8(password), salt=salt, N=2^log_n, r=r, p=p, length=64)
aes_key  = material[:32]
hmac_key = material[32:]
```

### Header integrity

1. `header_checksum` is a cheap corruption detector — verifiable without running scrypt or knowing the password.
2. `header_mac` cryptographically authenticates the header under the HMAC key derived from the password.
3. `file_mac` authenticates the header **and** the ciphertext. Verification order on decrypt: structural → checksum → KDF params → scrypt → header MAC → file MAC → decrypt.

MAC verification happens **before** AES-CTR decryption (authenticate-before-decrypt).

## Parameter caps

The header carries KDF parameters. Encoders and decoders MUST reject values outside these ranges to bound scrypt memory/CPU and preserve the single-error-type contract:

| Parameter | Minimum | Maximum |
| --- | --- | --- |
| `log_n`   | 10      | 20      |
| `r`       | 1       | 32      |
| `p`       | 1       | 16      |

- `encrypt()` rejects out-of-range arguments with `ValueError` before producing output.
- `decrypt()` treats a file whose header advertises out-of-range parameters as authentication failure — raises `DecryptionError`.

Defaults: `log_n=17`, `r=8`, `p=1` (same as the custom MBCE variant; roughly ~100 ms and ~128 MiB per unlock on a modern CPU).

## Operational rules

1. Fresh 32-byte salt on every encryption. Never reused.
2. Full-file re-encryption on save. No streaming, no partial updates.
3. Password only in process memory during a session; never persisted.
4. Atomic writes are the caller's concern (`*.tmp` + `fsync` + `rename`).

## Threat model

In scope:
- Offline attacker with the ciphertext tries to recover plaintext without the password. Protection = scrypt cost × password entropy.
- Attacker tampers with header or ciphertext. Rejected by HMAC (header MAC and/or file MAC, under the password-derived HMAC key).
- Random file corruption. Detected by header checksum (before scrypt) or HMAC (after scrypt).

Out of scope:
- Side-channel attacks against the running process (memory scraping, swap).
- Coerced disclosure of the password.
- Attacks against OS CSPRNG.

## Errors

All decryption failures collapse into a single `DecryptionError`. Wrong password, corrupted header, tampered MAC, out-of-range KDF parameters, unknown version — none are distinguished. Distinguishing would leak a decryption oracle.

## Versioning

- The `version` byte (currently `0x00`) identifies the format. Decoders MUST refuse unknown versions.
- Upstream `scrypt(1)` has not bumped version in 15+ years. If we ever need to evolve, coordination with upstream is required to keep the "one standard CLI decrypts" property.
