"""
Block for testing variously scoped XBlock fields.
"""

import json

from webob import Response
from xblock import fields
from xblock.core import Scope, XBlock


class UserStateTestBlock(XBlock):
    """
    Block for testing variously scoped XBlock fields.
    """
    BLOCK_TYPE = "user-state-test"
    has_score = False

    display_name = fields.String(scope=Scope.content, name='User State Test Block')
    # User-specific fields:
    user_str = fields.String(scope=Scope.user_state, default='default value')  # This usage, one user
    uss_str = fields.String(scope=Scope.user_state_summary, default='default value')  # This usage, all users
    pref_str = fields.String(scope=Scope.preferences, default='default value')  # Block type, one user
    user_info_str = fields.String(scope=Scope.user_info, default='default value')  # All blocks, one user

    @XBlock.json_handler
    def set_user_state(self, data, suffix):  # pylint: disable=unused-argument
        """
        Set the user-scoped fields
        """
        self.user_str = data["user_str"]
        self.uss_str = data["uss_str"]
        self.pref_str = data["pref_str"]
        self.user_info_str = data["user_info_str"]
        return {}

    @XBlock.handler
    def get_user_state(self, request, suffix=None):  # pylint: disable=unused-argument
        """
        Get the various user-scoped fields of this XBlock.
        """
        return Response(
            json.dumps({
                "user_str": self.user_str,
                "uss_str": self.uss_str,
                "pref_str": self.pref_str,
                "user_info_str": self.user_info_str,
            }),
            content_type='application/json',
            charset='UTF-8',
        )
