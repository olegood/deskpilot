"""The signature scheme, and the attacks it is meant to stop."""

import pytest

from shiptrack import signing

SECRET = "a-shared-secret-for-tests"


def signature(**overrides: object) -> str:
    args: dict[str, object] = {
        "secret": SECRET,
        "method": "GET",
        "path": "/api/shipments/ST-100042",
        "timestamp": "1700000000",
        "nonce": "abc",
        "body": b"",
    }
    return signing.sign(**{**args, **overrides})  # type: ignore[arg-type]


def test_the_same_request_always_signs_the_same() -> None:
    assert signature() == signature()


def test_a_different_secret_gives_a_different_signature() -> None:
    assert signature() != signature(secret="a different secret")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("method", "POST"),
        ("path", "/api/shipments/ST-100001"),
        ("timestamp", "1700000001"),
        ("nonce", "xyz"),
        ("body", b"{}"),
    ],
)
def test_every_part_of_the_request_is_covered(field: str, value: object) -> None:
    """If changing a field does not change the signature, that field is unprotected."""
    assert signature() != signature(**{field: value})


def test_the_canonical_string_is_unambiguous() -> None:
    """Two different requests must not produce the same string to sign.

    Concatenating without a separator is how that happens: a path ending in "1"
    with nonce "23" would otherwise match a path ending in "12" with nonce "3".
    """
    one = signing.canonical_request("GET", "/a/1", "1700000000", "23", b"")
    two = signing.canonical_request("GET", "/a/12", "1700000000", "3", b"")

    assert one != two


def test_the_body_digest_covers_the_body() -> None:
    assert signing.body_digest(b"") != signing.body_digest(b"{}")


def test_matches_accepts_the_right_signature() -> None:
    assert signing.matches(signature(), signature())


def test_matches_rejects_a_wrong_one() -> None:
    assert not signing.matches(signature(), signature(nonce="other"))


def test_matches_rejects_a_prefix() -> None:
    """A comparison that stopped at the shorter length would accept this."""
    correct = signature()

    assert not signing.matches(correct, correct[:10])
    assert not signing.matches(correct, "")
