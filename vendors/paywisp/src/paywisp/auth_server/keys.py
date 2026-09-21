"""The signing key, and the public half published as a JWKS.

ES256 on P-256. Asymmetric on purpose: the resource server must be able to verify
a token without being able to mint one. A shared HMAC secret would give every
service that checks tokens the power to forge them, which is the opposite of what
separating the two services is for.
"""

from __future__ import annotations

import logging
from typing import Any

from joserfc.jwk import ECKey

logger = logging.getLogger(__name__)

ALGORITHM = "ES256"


class SigningKey:
    """The one key this server signs with.

    The key id is the RFC 7638 thumbprint of the public key, so it is derived
    rather than chosen: the same key always gets the same id, and two different
    keys cannot collide on a name somebody typed.
    """

    def __init__(self, key: ECKey) -> None:
        if key.curve_name != "P-256":
            raise ValueError(f"expected a P-256 key, got {key.curve_name}")
        self.kid = key.thumbprint()
        self._key = ECKey.import_key({**key.as_dict(private=True), "kid": self.kid})

    @classmethod
    def from_pem(cls, pem: str) -> SigningKey:
        key = ECKey.import_key(pem)
        if not key.is_private:
            raise ValueError("the signing key must be a private key")
        return cls(key)

    @classmethod
    def generate(cls) -> SigningKey:
        logger.warning(
            "no PAYWISP_AUTH_SIGNING_KEY configured; generated a key that will not "
            "survive a restart"
        )
        return cls(ECKey.generate_key("P-256", private=True))

    @property
    def private(self) -> ECKey:
        return self._key

    def public_jwks(self) -> dict[str, Any]:
        """The key set clients and resource servers fetch.

        Built from the public members only. A JWKS that included `d` would publish
        the private key, and it is an easy mistake to make with a library call that
        exports "the key"; a test asserts the private member is absent.
        """
        public = self._key.as_dict(private=False)
        return {"keys": [{**public, "kid": self.kid, "alg": ALGORITHM, "use": "sig"}]}
