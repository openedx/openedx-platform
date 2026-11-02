"""
Tests for commerce toggles.
"""
from django.test import TestCase
from edx_toggles.toggles.testutils import override_waffle_flag

from lms.djangoapps.commerce.toggles import (
    ENABLE_REFUND_POLICY_CHANGE,
    SHOW_REFUND_POLICY_CHANGE_NOTICE,
    should_apply_refund_policy_change,
    should_show_refund_policy_change_notice,
)


class RefundPolicyChangeToggleTests(TestCase):
    """
    Tests for the commerce.enable_refund_policy_change waffle flag.
    """

    def test_disabled_by_default(self):
        assert not should_apply_refund_policy_change()

    @override_waffle_flag(ENABLE_REFUND_POLICY_CHANGE, active=True)
    def test_enabled(self):
        assert should_apply_refund_policy_change()


class RefundPolicyChangeNoticeToggleTests(TestCase):
    """
    Tests for the commerce.show_refund_policy_change_notice waffle flag.
    """

    def test_disabled_by_default(self):
        assert not should_show_refund_policy_change_notice()

    @override_waffle_flag(SHOW_REFUND_POLICY_CHANGE_NOTICE, active=True)
    def test_enabled(self):
        assert should_show_refund_policy_change_notice()

    @override_waffle_flag(SHOW_REFUND_POLICY_CHANGE_NOTICE, active=True)
    def test_independent_of_policy_flag(self):
        assert should_show_refund_policy_change_notice()
        assert not should_apply_refund_policy_change()
