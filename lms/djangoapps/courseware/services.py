"""
Courseware services.
"""


import json

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user

from common.djangoapps.student.models import get_user_by_username_or_email
from lms.djangoapps.courseware.models import StudentModule


class UserStateService:
    """
    User state service to make state accessible in runtime.
    """

    def get_state_as_dict(self, username_or_email, block_id):
        """
        Return dict containing user state for a given set of parameters.

        Arguments:
            username_or_email: username or email of the user for whom the data is being retrieved
            block_id: string/object representation of the block whose user state is required

        Returns:
            Returns a dict containing user state, if present, else empty.
        """
        try:
            user = get_user_by_username_or_email(username_or_email=username_or_email)
        except User.DoesNotExist:
            return {}
        try:
            student_module = StudentModule.objects.get(
                student=user,
                module_state_key=block_id
            )
            return json.loads(student_module.state)
        except StudentModule.DoesNotExist:
            return {}
