"""
Grades related signals.
"""


from django.dispatch import Signal  # noqa: I001

# Signal that indicates that a user's grade for a course has been updated.
# This is a downstream signal of SUBSECTION_SCORE_CHANGED.

# Signal that indicates that a user's raw score for a problem has been updated.
# This signal is generated when a scoring event occurs within the core
# platform. Note that this signal will be triggered
# regardless of the new and previous values of the score (i.e. it may be the
# case that this signal is generated when a user re-attempts a problem but
# receives the same score).
# providing_args=[
#         'user_id',  # Integer User ID
#         'course_id',  # Unicode string representing the course
#         'usage_id',  # Unicode string indicating the courseware instance
#         'raw_earned',   # Score obtained by the user
#         'raw_possible',  # Maximum score available for the exercise
#         'weight',  # Weight of the problem
#         'only_if_higher',   # Boolean indicating whether updates should be
#                             # made only if the new score is higher than previous.
#         'modified',  # A datetime indicating when the database representation of
#                      # this the problem score was saved.
#         'score_db_table',  # The database table that houses the score that changed.
#         'score_deleted',  # Boolean indicating whether the score changed due to
#                           # the user state being deleted.
#     ]
PROBLEM_RAW_SCORE_CHANGED = Signal()

# Signal that indicates that a user's weighted score for a problem has been updated.
# This signal is generated when a scoring event occurs in the Submissions module
# or a PROBLEM_RAW_SCORE_CHANGED event is handled in the core platform.
# Note that this signal will be triggered
# regardless of the new and previous values of the score (i.e. it may be the
# case that this signal is generated when a user re-attempts a problem but
# receives the same score).
# providing_args=[
#         'user_id',  # Integer User ID
#         'anonymous_user_id',  # Anonymous User ID
#         'course_id',  # Unicode string representing the course
#         'usage_id',  # Unicode string indicating the courseware instance
#         'weighted_earned',   # Score obtained by the user
#         'weighted_possible',  # Maximum score available for the exercise
#         'only_if_higher',   # Boolean indicating whether updates should be
#                             # made only if the new score is higher than previous.
#         'modified',  # A datetime indicating when the database representation of
#                      # this the problem score was saved.
#         'score_db_table',  # The database table that houses the score that changed.
#         'score_deleted',  # Boolean indicating whether the score changed due to
#                           # the user state being deleted.
#     ]
PROBLEM_WEIGHTED_SCORE_CHANGED = Signal()


# Signal that indicates that a user's score for a problem has been published
# for possible persistence and update.  Typically, most clients should listen
# to the PROBLEM_WEIGHTED_SCORE_CHANGED signal instead, since that is signalled
# only after the problem's score is changed.
# providing_args=[
#         'block',  # Course block object
#         'user',   # User object
#         'raw_earned',    # Score obtained by the user
#         'raw_possible',  # Maximum score available for the exercise
#         'only_if_higher',   # Boolean indicating whether updates should be
#                             # made only if the new score is higher than previous.
#         'score_db_table',  # The database table that houses the score that changed.
#     ]
SCORE_PUBLISHED = Signal()


# Signal that indicates that a user's score for a subsection has been updated.
# This is a downstream signal of PROBLEM_WEIGHTED_SCORE_CHANGED sent for each
# affected containing subsection.
# providing_args=[
#         'course',  # Course object
#         'course_structure',  # BlockStructure object
#         'user',  # User object
#         'subsection_grade',  # SubsectionGrade object
#     ]
SUBSECTION_SCORE_CHANGED = Signal()

# Signal that indicates that a user's score for a subsection has been overridden.
# This signal is generated when a user's exam attempt state is set to rejected or
# to verified from rejected. This signal may also be sent by any other client
# using the GradesService to override subsections in the future.
# providing_args=[
#         'user_id',  # Integer User ID
#         'course_id',  # Unicode string representing the course
#         'usage_id',  # Unicode string indicating the courseware instance
#         'only_if_higher',   # Boolean indicating whether updates should be
#                             # made only if the new score is higher than previous.
#         'modified',  # A datetime indicating when the database representation of
#                      # this subsection override score was saved.
#         'score_deleted',  # Boolean indicating whether the override score was
#                           # deleted in this event.
#         'score_db_table',  # The database table that houses the subsection override
#                            # score that was created.
#     ]
SUBSECTION_OVERRIDE_CHANGED = Signal()


# This Signal indicates that the user has received a passing grade in the course for the first time.
# Any subsequent grade changes that may vary the passing/failing status will not re-trigger this event.
# Emits course grade passed first time event
# providing_args=[
#         'course_id',  # Course object id
#         'user_id',  # User object id
#     ]
COURSE_GRADE_PASSED_FIRST_TIME = Signal()
COURSE_GRADE_PASSED_UPDATE_IN_LEARNER_PATHWAY = Signal()

# This Signal indicates that a segment event has fired for user who has passed a course for the first time
# providing_args=[
#     'user_id',  # User object id
#     'course_id',  # Course object id
#     'event_properties',  # Segment event properties that will be needed for follow up event
# ]
SCHEDULE_FOLLOW_UP_SEGMENT_EVENT_FOR_COURSE_PASSED_FIRST_TIME = Signal()
