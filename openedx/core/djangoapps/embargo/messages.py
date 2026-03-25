"""Define messages for restricted courses.

These messages are displayed to users when they are blocked
from either enrolling in or accessing a course.

"""

from collections import namedtuple  # noqa: I001

BlockedMessage = namedtuple('BlockedMessage', [
    # A user-facing description of the message
    'description',

    # The mako template used to render the message
    'template',
])


ENROLL_MESSAGES = {
    'default': BlockedMessage(
        description='Default',
        template='embargo/default_enrollment.html'
    ),
    'embargo': BlockedMessage(
        description='Embargo',
        template='static_templates/embargo.html'
    )
}


COURSEWARE_MESSAGES = {
    'default': BlockedMessage(
        description='Default',
        template='embargo/default_courseware.html'
    ),
    'embargo': BlockedMessage(
        description='Embargo',
        template='static_templates/embargo.html'
    )
}
