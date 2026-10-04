"""Pure-Python Ed25519 (RFC 8032) used to authenticate Botonomus Chromium releases.

Verification is the only operation the SDK performs at runtime: it checks the release
manifest against a public key embedded in the package. It works on public data only,
so the lack of constant-time arithmetic is not a concern there.

`sign` and `public_key_from_seed` exist for the test suite and the release
tooling. They are **not constant-time** and leak timing information about the secret
seed; never use them to handle a production signing key on a shared or untrusted
machine. The production release key is held offline and never enters the repository.
"""

import hashlib

__all__ = [
    "PUBLIC_KEY_SIZE",
    "SEED_SIZE",
    "SIGNATURE_SIZE",
    "public_key_from_seed",
    "sign",
    "verify",
]

PUBLIC_KEY_SIZE = 32
"""Length in bytes of an encoded Ed25519 public key."""

SEED_SIZE = 32
"""Length in bytes of an Ed25519 secret key (the RFC 8032 "seed")."""

SIGNATURE_SIZE = 64
"""Length in bytes of an Ed25519 signature."""

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

# Points are kept in extended homogeneous coordinates (X, Y, Z, T), x = X/Z, y = Y/Z,
# x*y = T/Z, which gives addition formulas without field inversions.
_Point = tuple[int, int, int, int]
_IDENTITY: _Point = (0, 1, 1, 0)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_BASE_Y = 4 * pow(5, _P - 2, _P) % _P
_BASE_X = _recover_x(_BASE_Y, 0)
assert _BASE_X is not None
_BASE: _Point = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % _P)


def _add(p: _Point, q: _Point) -> _Point:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _multiply(scalar: int, point: _Point) -> _Point:
    result = _IDENTITY
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(p: _Point, q: _Point) -> bool:
    if (p[0] * q[2] - q[0] * p[2]) % _P != 0:
        return False
    return (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _encode(point: _Point) -> bytes:
    z_inv = pow(point[2], _P - 2, _P)
    x = point[0] * z_inv % _P
    y = point[1] * z_inv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decode(data: bytes) -> _Point | None:
    if len(data) != 32:
        return None
    value = int.from_bytes(data, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _sha512_int(*parts: bytes) -> int:
    digest = hashlib.sha512()
    for part in parts:
        digest.update(part)
    return int.from_bytes(digest.digest(), "little")


def _expand_seed(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != SEED_SIZE:
        raise ValueError("Ed25519 seed must be 32 bytes")
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key_from_seed(seed: bytes) -> bytes:
    """Derive the encoded public key for a 32-byte secret seed.

    Not constant-time; intended for tests and offline release tooling only.

    Args:
        seed: The 32-byte Ed25519 secret key.

    Returns:
        The 32-byte encoded public key.

    Raises:
        ValueError: If ``seed`` is not 32 bytes.
    """
    scalar, _ = _expand_seed(seed)
    return _encode(_multiply(scalar, _BASE))


def sign(seed: bytes, message: bytes) -> bytes:
    """Produce a deterministic Ed25519 signature of ``message``.

    Not constant-time: it leaks timing information about ``seed``. Use it only in
    tests and in offline release tooling, never for production key handling on a
    shared machine.

    Args:
        seed: The 32-byte Ed25519 secret key.
        message: The bytes to sign.

    Returns:
        The 64-byte signature ``R || S``.

    Raises:
        ValueError: If ``seed`` is not 32 bytes.
    """
    scalar, prefix = _expand_seed(seed)
    public_key = _encode(_multiply(scalar, _BASE))
    r = _sha512_int(prefix, message) % _L
    r_encoded = _encode(_multiply(r, _BASE))
    k = _sha512_int(r_encoded, public_key, message) % _L
    s = (r + k * scalar) % _L
    return r_encoded + int.to_bytes(s, 32, "little")


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Check an Ed25519 signature per RFC 8032 section 5.1.7.

    Malformed input never raises: a public key or signature of the wrong length, a
    non-canonical ``S`` (``S >= L``) and point encodings that do not decode to a curve
    point (including a non-canonical ``y >= p``) all yield ``False``. The cofactorless
    equation ``[S]B == R + [k]A`` is checked, which RFC 8032 permits.

    Args:
        public_key: The 32-byte encoded public key ``A``.
        message: The signed bytes.
        signature: The 64-byte signature ``R || S``.

    Returns:
        Whether the signature is valid for ``message`` under ``public_key``.
    """
    if len(public_key) != PUBLIC_KEY_SIZE or len(signature) != SIGNATURE_SIZE:
        return False
    a = _decode(public_key)
    r = _decode(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    k = _sha512_int(signature[:32], public_key, message) % _L
    return _equal(_multiply(s, _BASE), _add(r, _multiply(k, a)))
