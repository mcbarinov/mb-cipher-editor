# Cryptography

This project encrypts text files with a user-supplied password using the
**Tarsnap `scrypt(1)`** on-disk format. This document is the **source of
truth** for that format — the code in `core/crypto.py` must match it exactly.

It also records why `scrypt(1)` was chosen over the alternatives.

## Constraints

These drove the decision:

1. **Strong password-based encryption.** Files may sit on disk for years; the
   KDF must stand up to offline brute-force with modern GPU/ASIC hardware.
2. **Single cryptographic dependency: [`pyca/cryptography`](https://github.com/pyca/cryptography).**
   Audited, widely deployed, maintained by the Python Cryptographic Authority.
   No other crypto libraries — supply-chain risk from less-vetted packages is
   unacceptable for a tool that holds long-lived secrets.
3. **Survivability without the editor.** If this program disappears, the user
   must still be able to decrypt their files. That means a well-known format
   and a widely-available third-party CLI for recovery.
4. **Minimal own code.** Every line of our own format code is a correctness
   risk. Preference goes to formats that are simple to implement and have
   stable published specs.

## Decision

**Tarsnap `scrypt(1)` format.** It is the only option that clears all four
constraints simultaneously:

- KDF: native scrypt (memory-hard, strong against GPU/ASIC).
- One dependency: `cryptography` provides `Scrypt`, `AES-CTR`, and HMAC is in
  the stdlib. No extra packages.
- Recovery CLI: `scrypt dec file` — packaged in Homebrew, apt, dnf, pacman.
- Format: simple, fixed header, stable since 2009, authoritative spec upstream.

## Alternatives rejected

### Custom (home-grown MBCE format)

Plain AES-256-GCM over a scrypt-derived key with a 36-byte header. Simple and
secure in isolation.

Rejected because it **fails the survivability constraint**. No third-party
tool can read it. If this project disappears, recovery requires reconstructing
the format from this repository. Our own spec is only as durable as the
project itself — a risk we don't need to take when `scrypt(1)` gives the same
security for the same effort.

### age v1 (passphrase mode)

Excellent format, widely adopted CLI (`age`, `rage`, `pyrage`), frozen spec,
strong scrypt-based KDF. Would be the top choice if the cost of our own
implementation were free.

Rejected for two reasons:

- **Implementation cost.** age v1 is meaningfully more complex than
  `scrypt(1)` — STREAM chunked AEAD, HKDF domain separation, a separate header
  HMAC, base64-without-padding parsing. A prototype lived at ~220 lines. Any
  drift from the spec silently breaks interop with `age -d`.
- **No trusted Python binding under the constraint.** `pyrage` is a single-
  maintainer project outside the PyCA umbrella, and a full age implementation
  on top of `cryptography` alone is exactly the 220-line maintenance burden
  above. `scrypt(1)` delivers the same recovery story with a much simpler
  format.

### JWE password-based (RFC 7516 / RFC 7518 PBES2)

JSON-serialized, self-describing (algorithms in the header), simple to
implement on `cryptography` alone (PBKDF2HMAC + AES Key Wrap + AES-GCM — all
present). A `jose` CLI exists in Homebrew for third-party recovery.

Rejected because **JWE mandates PBKDF2 as the password KDF** (PBES2-HS*+AxKW).
There is no standardized Argon2 or scrypt variant in JOSE. PBKDF2 is
memory-cheap, so GPU/ASIC acceleration reduces the cost of offline brute-force
by orders of magnitude relative to scrypt or Argon2id.

For files at rest — the whole point of this tool — that regression is
unacceptable. The JSON-readable header is a cosmetic win; it does not justify
weakening the KDF. `scrypt(1)` gives the same "self-describing + external CLI
for recovery" properties without the KDF compromise.

### GPG (`gpg -c`)

Requires an external binary at encryption time — violates constraint #2. Only
the tool that produced the file can read it; nothing else is portable without
GnuPG installed.

### Argon2id via `argon2-cffi`

Argon2id is the current state-of-the-art password KDF and would be a
theoretically better choice than scrypt. Rejected to keep the dependency
surface at one library; `scrypt` at the parameters we use (N = 2^17, r = 8,
p = 1) is more than adequate for this threat model, and no standard file
format builds on Argon2id today.

## Out of scope

- Key-based (public-key) encryption. This is a password-only editor by design.
- Streaming encryption for very large files. Files are small, text-only, and
  re-encrypted in full on every save.
- Windows as a first-class recovery platform. The Tarsnap `scrypt` CLI exists
  on Windows but is not first-class; that is accepted.

---

# Format specification

Upstream: **[Tarsnap `scrypt` FORMAT](https://github.com/Tarsnap/scrypt/blob/master/FORMAT)** (authoritative). Algorithm author = utility author = Colin Percival. Format is stable since 2009.

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
6        1      version                       0x00
7        1      log_n                         scrypt cost: N = 2 ** log_n   (default 17)
8        4      r                             scrypt block size             (default 8)
12       4      p                             scrypt parallelism            (default 1)
16      32      salt                          os.urandom(32) — fresh per encryption
48      16      header_checksum               SHA-256(bytes 0..47)[:16]
64      32      header_mac                    HMAC-SHA-256(hmac_key, bytes 0..63)
96       X      ciphertext                    AES-256-CTR(aes_key, iv=0^128, plaintext)
96+X    32      file_mac                      HMAC-SHA-256(hmac_key, bytes 0..96+X-1)
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

Defaults: `log_n=17`, `r=8`, `p=1` — roughly ~100 ms and ~128 MiB per unlock on a modern CPU.

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
