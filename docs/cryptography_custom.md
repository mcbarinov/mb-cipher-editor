# Cryptography — custom MBCE format (variant)

> **Status:** one of several variants being prototyped side-by-side. See also `cryptography_age.md` (age v1) and `cryptography_scrypt.md` (Tarsnap scrypt(1)) once a final format is chosen.

This document is the **source of truth** for the MBCE variant. The code in `core/crypto_custom.py` must match this spec exactly. If the spec changes, bump the format version and update both this document and the code together.

## Goals

1. Strong password-based file encryption. A file is unrecoverable without the password.
2. **No runtime dependency on external CLI tools.** Everything needed to encrypt/decrypt ships inside the Python package.
3. Trusted cryptographic primitives only. No hand-rolled crypto.
4. Simple, fixed binary format. Easy to parse, easy to audit.

Interoperability with third-party tools (e.g. `age`, `gpg`) is **not** a goal. It is acceptable that files can only be opened by this program (or by a small script built on the same primitives).

## Library choice

We use [`pyca/cryptography`](https://github.com/pyca/cryptography) and nothing else for cryptography.

Why:

- Maintained by the Python Cryptographic Authority. De-facto standard in the Python ecosystem.
- Thin wrapper over OpenSSL (and Rust-backed bindings). Real crypto runs in audited C/Rust code.
- Used in bedrock projects (certbot, paramiko, AWS/Azure SDKs, ansible). Hundreds of millions of downloads per month.
- External security audit by Trail of Bits.
- Provides all primitives we need: `Scrypt` (KDF) and `AESGCM` (AEAD).

Alternatives considered and rejected:

- **`age` / `pyrage`.** `age` is an excellent protocol, but `pyrage` is a personal project (single maintainer, not under the PyCA umbrella). Relying on `age` CLI would violate goal #2. Reimplementing `age` on top of `cryptography` buys nothing — correctness risk without a real benefit for our single-file, single-password use case.
- **GPG (`gpg -c`).** Requires external binary; violates goal #2.
- **Argon2id via `argon2-cffi`.** Theoretically preferable password KDF. Rejected to keep dependency surface minimal; `scrypt` at the parameters below is more than sufficient for this use case.

## Primitives

| Role | Algorithm | Source |
| --- | --- | --- |
| Password KDF | scrypt | `cryptography.hazmat.primitives.kdf.scrypt.Scrypt` |
| AEAD | AES-256-GCM | `cryptography.hazmat.primitives.ciphers.aead.AESGCM` |
| Randomness | OS CSPRNG | `os.urandom` |

## File format (version 1)

Binary layout, big-endian, fixed-size header followed by the AEAD body.

```
Offset  Size  Field       Notes
------  ----  ----------  -------------------------------------------------
0       4     magic       ASCII "MBCE"
4       1     version     0x01
5       1     log_n       scrypt cost: N = 2 ** log_n   (default: 17)
6       1     r           scrypt block size             (default: 8)
7       1     p           scrypt parallelism            (default: 1)
8       16    salt        os.urandom(16) — fresh per encryption
24      12    nonce       os.urandom(12) — fresh per encryption
36      ...   body        AES-256-GCM ciphertext || 16-byte tag
```

Total header size: **36 bytes, fixed**. Minimum file size: 36 + 0 plaintext + 16 tag = **52 bytes**.

### KDF

```
key = scrypt(
    password   = utf-8 bytes of the user's password,
    salt       = header.salt,
    n          = 2 ** header.log_n,
    r          = header.r,
    p          = header.p,
    length     = 32,              # AES-256 key
)
```

### AEAD

```
aad        = header[0:36]          # magic || version || log_n || r || p || salt || nonce
ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)   # includes 16-byte tag
body       = ciphertext
```

On decrypt:

```
AESGCM(key).decrypt(header.nonce, body, aad)
```

Authentication covers the entire header via AAD. Any tampering with magic/version/KDF params/salt/nonce causes decryption to fail.

## Parameter choices (version 1 defaults)

- `log_n = 17` → N = 131 072. Roughly ~100 ms on a modern CPU, ~128 MiB peak memory. Cost is paid once per unlock/save, which is acceptable for an interactive editor.
- `r = 8`, `p = 1` — standard scrypt values recommended alongside N = 2^17.

Parameters are embedded in the header, so future versions can raise `log_n` without breaking existing files — old files are decrypted with their own recorded parameters.

## Parameter caps (version 1)

The header carries KDF parameters, but encoders and decoders MUST reject values outside the ranges below. This bounds scrypt memory/CPU for any file we might open (including a malformed or hostile one) and keeps the contract "any failure is a single error type" honest — a decoder that called scrypt with `log_n = 40` would otherwise raise `MemoryError` and leak a distinct failure mode.

| Parameter | Minimum | Maximum |
| --- | --- | --- |
| `log_n`   | 10      | 20      |
| `r`       | 1       | 32      |
| `p`       | 1       | 16      |

- `encrypt()` rejects out-of-range arguments with `ValueError` before producing any output. This is a caller bug — the module will never write a file it cannot read.
- `decrypt()` treats a header with out-of-range KDF parameters exactly like authentication failure: it raises `DecryptionError`, indistinguishable from wrong-password or tampered-ciphertext.

Raising the maxima requires a new format version.

## Operational rules

1. **Fresh salt and nonce on every encryption.** Never reuse. Both come from `os.urandom` on each call to `encrypt()`.
2. **Full-file re-encryption on save.** No streaming, no partial updates. This guarantees nonce is always fresh.
3. **No password caching in the format.** The password is only held in process memory during a session and is never written to disk.
4. **Atomic writes** (enforced at a higher layer, not in this module): write to `<path>.tmp`, `fsync`, `rename` over the target.

## Threat model

In scope:

- Offline attacker with full copy of the ciphertext file attempts to recover plaintext without the password. Protection comes from scrypt cost × password entropy. Users must pick strong passwords; the KDF buys time, not magic.
- Attacker tampers with the ciphertext or header. Rejected by AES-GCM + AAD over the full header.
- Attacker swaps the file for a different encrypted file. Indistinguishable from a legitimate different file — this is a property of file-level encryption, not a flaw. Integrity of *which* file is expected is the caller's concern.

Out of scope:

- Side-channel attacks against the running process (memory scraping, swap, core dumps).
- Coerced disclosure of the password.
- Attacks against the OS CSPRNG.

## Errors

Decryption failure (wrong password, corrupted file, tampering) is reported as a single error type. The module does **not** distinguish "wrong password" from "tampered file" — both indicate the ciphertext cannot be authenticated under the given password, and leaking that distinction gives an attacker an oracle.

## Versioning

The `version` byte identifies the format. A decoder MUST refuse unknown versions. New versions:

- MAY change the header layout, KDF, or AEAD.
- MUST keep the first 5 bytes as `"MBCE" || version` so old decoders reject new files with a clear error.
- MUST be documented here before being implemented in code.
