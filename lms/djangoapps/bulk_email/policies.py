"""Course Email optOut Policy"""


from edx_ace.channel import ChannelType
from edx_ace.policy import Policy, PolicyResult
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.bulk_email.models import Optout


class CourseEmailOptout(Policy):  # pylint: disable=missing-class-docstring

    def check(self, message):
        course_ids = message.context.get('course_ids')
        if not course_ids:
            return PolicyResult(deny=frozenset())

        # pylint: disable=line-too-long
        course_keys = [CourseKey.from_string(course_id) for course_id in course_ids]
        if Optout.objects.filter(user_id=message.recipient.lms_user_id, course_id__in=course_keys).count() == len(course_keys):
            return PolicyResult(deny={ChannelType.EMAIL})

        return PolicyResult(deny=frozenset())
