"""
Permission definitions for the enrollments djangoapp
"""

from bridgekeeper import perms

from lms.djangoapps.courseware.rules import HasAccessRule

ENROLL_IN_COURSE = 'enrollment.enroll_in_course'

perms[ENROLL_IN_COURSE] = HasAccessRule('enroll')
