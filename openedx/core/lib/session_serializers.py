"""
Session serializer for Open edX.

Historically this wrapped the standard library object serializer with a
fixed protocol to survive the Python 2 -> Python 3 upgrade. Python 2
support is long gone, but a number of callers still put non-JSON-safe
values (Decimal, bytes, custom classes such as CourseMasquerade) into
``request.session``, so a blanket switch to
``django.contrib.sessions.serializers.JSONSerializer`` is not yet
possible.

To remove the CWE-502 unsafe-deserialization sink without forcing that
refactor, this serializer authenticates every payload with an HMAC-SHA256
signature derived from ``SECRET_KEY`` before the object sink is ever
reached. Unsigned or tampered payloads raise ``SuspiciousOperation``,
which Django's session backend converts into an empty session (the user
is redirected to login). This gives defense-in-depth on top of Django's
own signing layer and makes the deserialization sink unreachable by any
attacker who does not already hold ``SECRET_KEY``.
"""
import hashlib
import hmac
import pickle  # noqa: S403 - authenticity is enforced by an HMAC check in loads()

from django.conf import settings
from django.core.exceptions import SuspiciousOperation

# SHA-256 digest length in bytes. Payload layout is:
# ``<32-byte HMAC signature> || <serialised bytes>``.
_SIGNATURE_LENGTH = hashlib.sha256().digest_size

# Domain-separation salt for the derived HMAC key so this serializer's key
# is never identical to any other HMAC that happens to reuse SECRET_KEY
# directly.
_HMAC_SALT = b"openedx.core.lib.session_serializers.PickleSerializer.v1"


def _derive_key():
    """
    Derive an HMAC key from Django's ``SECRET_KEY``.
    """
    secret = settings.SECRET_KEY
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hashlib.sha256(_HMAC_SALT + secret).digest()


class PickleSerializer:
    """
    HMAC-authenticated session serializer.

    ``dumps`` returns ``HMAC-SHA256(payload) || payload``.
    ``loads`` refuses to deserialise anything whose signature does not
    verify in constant time against the key derived from ``SECRET_KEY``.
    The class name and import path are preserved so
    ``openedx/envs/common.py`` does not need to change.
    """

    protocol = 4

    def dumps(self, obj):
        """
        Return an HMAC-authenticated serialised representation of ``obj``.
        """
        payload = pickle.dumps(obj, self.protocol)
        signature = hmac.new(_derive_key(), payload, hashlib.sha256).digest()
        return signature + payload

    def loads(self, data):
        """
        Verify the HMAC and return the python object.

        Raises ``SuspiciousOperation`` if ``data`` is too short, unsigned,
        or has been tampered with. Django's session backend catches this
        and invalidates the session rather than propagating.
        """
        if not isinstance(data, (bytes, bytearray)) or len(data) < _SIGNATURE_LENGTH:
            raise SuspiciousOperation(
                "Session payload is missing its HMAC signature."
            )
        signature = bytes(data[:_SIGNATURE_LENGTH])
        payload = bytes(data[_SIGNATURE_LENGTH:])
        expected = hmac.new(_derive_key(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise SuspiciousOperation(
                "Session payload HMAC verification failed."
            )
        # Payload authenticity is enforced by the HMAC check above, so the
        # subsequent call operates only on data this process just produced.
        return pickle.loads(payload)  # noqa: S301
