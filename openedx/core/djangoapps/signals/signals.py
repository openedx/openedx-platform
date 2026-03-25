"""
This module contains all general use signals.
"""


from django.dispatch import Signal  # noqa: I001

# Signal that fires when a user is graded
# providing_args=["user", "course_grade", "course_key", "deadline"]
COURSE_GRADE_CHANGED = Signal()

# Signal that fires when a user is awarded a certificate in a course (in the certificates django app)
# TODO: runtime coupling between apps will be reduced if this event is changed to carry a username
# rather than a User object; however, this will require changes to the milestones
# Same providing_args=["user", "course_key", "mode", "status"] for next 3 signals.
COURSE_CERT_CHANGED = Signal()
COURSE_CERT_AWARDED = Signal()
COURSE_CERT_REVOKED = Signal()
# providing_args=["course_key",]
COURSE_CERT_DATE_CHANGE = Signal()

# providing_args=['user', 'course_id', 'subsection_id', 'subsection_grade', ]
COURSE_ASSESSMENT_GRADE_CHANGED = Signal()

# Signal that indicates that a user has passed a course.
# providing_args=[
#    'user',  # user object
#    'course_id',  # course.id
# ]
COURSE_GRADE_NOW_PASSED = Signal()
#Signal that indicates a user is now failing a course that they had previously passed.
# providing_args=[
#     'user',  # user object
#     'course_id',  # course.id
#     'grade',  # CourseGrade object
# ]
COURSE_GRADE_NOW_FAILED = Signal()

# Signal that indicates that a user has become verified via SSO for certificate purposes
# providing_args=['user']
LEARNER_SSO_VERIFIED = Signal()

# Signal that indicates a user has been verified via verify_studnet.PhotoVerification for certificate purposes
# Please note that this signal and the corresponding PhotoVerification model are planned for deprecation.
# Future implementations of IDV will use the verify_student.VerificationAttempt model and corresponding
# openedx events.
# DEPR:  https://github.com/openedx/edx-platform/issues/35128
PHOTO_VERIFICATION_APPROVED = Signal()

# providing_args=['user']
USER_ACCOUNT_ACTIVATED = Signal()  # Signal indicating email verification
