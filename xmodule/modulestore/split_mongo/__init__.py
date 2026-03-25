"""
General utilities
"""
from collections import namedtuple  # noqa: I001

# We import BlockKey here for backwards compatibility with modulestore code.
# Feel free to remove this and fix the imports if you have time.
from xmodule.util.keys import BlockKey

CourseEnvelope = namedtuple('CourseEnvelope', 'course_key structure')
