"""
Tests for ``openedx.core.lib.session_serializers``.

Regression coverage for the session-serialisation hardening: the legacy
deserialisation sink must be unreachable without a valid HMAC derived
from ``SECRET_KEY``.
"""
# The test module constructs malicious raw payloads to exercise the
# HMAC guard; the production code never deserialises untrusted bytes.
import pickle  # noqa: S403

import pytest
from django.core.exceptions import SuspiciousOperation
from django.test import SimpleTestCase, override_settings

from openedx.core.lib.session_serializers import (
    _SIGNATURE_LENGTH,
    PickleSerializer,
)


@override_settings(SECRET_KEY="unit-test-key")
class PickleSerializerUnitTests(SimpleTestCase):
    """
    Unit tests: the serializer must round-trip legitimate session state
    and refuse any payload it did not sign itself.
    """

    def setUp(self):
        super().setUp()
        self.serializer = PickleSerializer()

    def test_roundtrip_preserves_primitive_session_state(self):
        state = {
            "_auth_user_id": "42",
            "country_code": "FR",
            "ip_address": "1.2.3.4",
        }
        assert self.serializer.loads(self.serializer.dumps(state)) == state

    def test_roundtrip_preserves_non_json_types(self):
        """
        Existing callers (CourseMasquerade, Decimal donation amounts, TPA
        pipeline bytes) rely on non-JSON-safe values surviving a session
        round trip. The HMAC wrapper must not change that.
        """
        import decimal
        state = {
            "donation_for_course": {
                "course-v1:edX+DemoX+T1": decimal.Decimal("42.50"),
            },
            "tpa_custom_auth_entry_data": {
                "data": b"base64bytes==",
                "hmac": b"sigbytes==",
            },
        }
        decoded = self.serializer.loads(self.serializer.dumps(state))
        assert decoded == state
        assert isinstance(
            decoded["donation_for_course"]["course-v1:edX+DemoX+T1"],
            decimal.Decimal,
        )

    def test_sec_pickle_session_regression_rejects_unsigned_payload(self):
        """
        An unsigned serialised blob (the shape an attacker would inject
        directly into Redis) must be refused before deserialisation.
        """
        raw = pickle.dumps({"_auth_user_id": "42"}, 4)
        with pytest.raises(SuspiciousOperation):
            self.serializer.loads(raw)

    def test_sec_pickle_session_regression_rejects_tampered_signature(self):
        """Flipping any byte of the signature must fail verification."""
        signed = bytearray(self.serializer.dumps({"a": 1}))
        signed[0] ^= 0xFF
        with pytest.raises(SuspiciousOperation):
            self.serializer.loads(bytes(signed))

    def test_sec_pickle_session_regression_rejects_tampered_body(self):
        """Flipping any byte of the payload must fail verification."""
        signed = bytearray(self.serializer.dumps({"a": 1}))
        signed[-1] ^= 0xFF
        with pytest.raises(SuspiciousOperation):
            self.serializer.loads(bytes(signed))

    def test_sec_pickle_session_regression_rejects_truncated_payload(self):
        """A payload shorter than the signature must be refused."""
        with pytest.raises(SuspiciousOperation):
            self.serializer.loads(b"\x00" * (_SIGNATURE_LENGTH - 1))

    def test_sec_pickle_session_regression_rejects_non_bytes(self):
        """A non-bytes input must be refused."""
        with pytest.raises(SuspiciousOperation):
            self.serializer.loads("not-bytes")

    def test_sec_pickle_session_regression_rejects_rce_gadget(self):
        """
        A classic ``__reduce__``-based gadget must never reach the
        deserialisation sink — verification happens first.
        """
        class _Gadget:  # pylint: disable=too-few-public-methods
            def __reduce__(self):
                import os
                return (os.system, ("echo pwned",))

        malicious = pickle.dumps(_Gadget())
        with pytest.raises(SuspiciousOperation):
            self.serializer.loads(malicious)

    def test_sec_pickle_session_regression_key_rotation_invalidates(self):
        """
        Sessions signed under an old ``SECRET_KEY`` must be rejected once
        the key is rotated.
        """
        with override_settings(SECRET_KEY="old-key"):
            signed = PickleSerializer().dumps({"a": 1})
        with override_settings(SECRET_KEY="new-key"), pytest.raises(SuspiciousOperation):
            PickleSerializer().loads(signed)
