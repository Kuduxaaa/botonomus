import pytest

from botonomus.browser import signing

# RFC 8032 section 7.1 test vectors: (secret seed, public key, message, signature).
RFC8032_VECTORS = [
    pytest.param(
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
        "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
        id="test1",
    ),
    pytest.param(
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
        id="test2",
    ),
    pytest.param(
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
        id="test3",
    ),
    pytest.param(
        "833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
        "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
        "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b589"
        "09351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704",
        id="sha_abc",
    ),
]

_L = 2**252 + 27742317777372353535851937790883648493


@pytest.mark.parametrize(("seed", "public", "message", "signature"), RFC8032_VECTORS)
def test_rfc8032_vectors(seed, public, message, signature):
    seed_b, public_b = bytes.fromhex(seed), bytes.fromhex(public)
    message_b, signature_b = bytes.fromhex(message), bytes.fromhex(signature)
    assert signing.public_key_from_seed(seed_b) == public_b
    assert signing.sign(seed_b, message_b) == signature_b
    assert signing.verify(public_b, message_b, signature_b)


@pytest.mark.parametrize(("seed", "public", "message", "signature"), RFC8032_VECTORS)
def test_rfc8032_vectors_reject_tampering(seed, public, message, signature):
    public_b, message_b = bytes.fromhex(public), bytes.fromhex(message)
    signature_b = bytes.fromhex(signature)
    assert not signing.verify(public_b, message_b + b"x", signature_b)
    for index in (0, 31, 32, 63):
        flipped = bytearray(signature_b)
        flipped[index] ^= 0x01
        assert not signing.verify(public_b, message_b, bytes(flipped))
    other = bytearray(public_b)
    other[0] ^= 0x01
    assert not signing.verify(bytes(other), message_b, signature_b)


def test_non_canonical_s_is_rejected():
    seed = bytes(range(32))
    public = signing.public_key_from_seed(seed)
    signature = signing.sign(seed, b"payload")
    s = int.from_bytes(signature[32:], "little")
    # S + L satisfies the group equation identically, so only the S < L check rejects it.
    malleated = signature[:32] + int.to_bytes(s + _L, 32, "little")
    assert signing.verify(public, b"payload", signature)
    assert not signing.verify(public, b"payload", malleated)


def test_invalid_points_are_rejected():
    seed = bytes(range(32))
    public = signing.public_key_from_seed(seed)
    signature = signing.sign(seed, b"m")
    # y = p is a non-canonical encoding of y = 0.
    non_canonical_y = int.to_bytes(2**255 - 19, 32, "little")
    # y = 2 has no x on the curve: (y^2 - 1) / (d y^2 + 1) is a non-square.
    off_curve = int.to_bytes(2, 32, "little")
    for bad in (non_canonical_y, off_curve):
        assert not signing.verify(bad, b"m", signature)
        assert not signing.verify(public, b"m", bad + signature[32:])
    # y = 1 decodes to x = 0, so a set sign bit is an invalid encoding.
    negative_zero = int.to_bytes(1 | (1 << 255), 32, "little")
    assert not signing.verify(negative_zero, b"m", signature)


@pytest.mark.parametrize(
    ("public_len", "signature_len"), [(31, 64), (33, 64), (32, 63), (32, 65), (0, 0)]
)
def test_wrong_lengths_return_false(public_len, signature_len):
    assert not signing.verify(bytes(public_len), b"m", bytes(signature_len))


def test_sign_rejects_wrong_seed_length():
    with pytest.raises(ValueError):
        signing.sign(bytes(31), b"m")
    with pytest.raises(ValueError):
        signing.public_key_from_seed(bytes(33))


def test_round_trip_with_long_message():
    seed = bytes(reversed(range(32)))
    message = bytes(range(256)) * 4
    signature = signing.sign(seed, message)
    assert signing.verify(signing.public_key_from_seed(seed), message, signature)
