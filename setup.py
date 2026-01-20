"""  # lint-amnesty, pylint: disable=django-not-configured
Setup script for the Open edX package.
"""

from setuptools import setup

setup(
    entry_points={
        "openedx.user_partition_scheme": [
            "cohort = openedx.core.djangoapps.course_groups.partition_scheme:CohortPartitionScheme",
            "content_type_gate = openedx.features.content_type_gating.partitions:ContentTypeGatingPartitionScheme",
            "enrollment_track = openedx.core.djangoapps.verified_track_content.partition_scheme:EnrollmentTrackPartitionScheme",  # lint-amnesty, pylint: disable=line-too-long
            "random = openedx.core.djangoapps.user_api.partition_schemes:RandomUserPartitionScheme",
            "team = lms.djangoapps.teams.team_partition_scheme:TeamPartitionScheme",
            "verification = openedx.core.djangoapps.user_api.partition_schemes:ReturnGroup1PartitionScheme",
        ],
        "openedx.ace.policy": [
            "bulk_email_optout = lms.djangoapps.bulk_email.policies:CourseEmailOptout",
            "course_push_notification_optout = openedx.core.djangoapps.notifications.policies:CoursePushNotificationOptout",  # lint-amnesty, pylint: disable=line-too-long
            "disabled_user_optout = openedx.core.djangoapps.ace_common.policies:DisableUserOptout",
        ],
        "openedx.call_to_action": [
            "personalized_learner_schedules = openedx.features.personalized_learner_schedules.call_to_action:PersonalizedLearnerScheduleCallToAction"  # lint-amnesty, pylint: disable=line-too-long
        ],
        'openedx.learning_context': [
            'lib = openedx.core.djangoapps.content_libraries.library_context:LibraryContextImpl',
        ],
        'openedx.dynamic_partition_generator': [
            'content_type_gating = openedx.features.content_type_gating.partitions:create_content_gating_partition',
            'enrollment_track = xmodule.partitions.enrollment_track_partition_generator:create_enrollment_track_partition',  # lint-amnesty, pylint: disable=line-too-long
            'team = openedx.core.lib.teams_config:create_team_set_partition',
        ],
        'console_scripts': [
            'xmodule_assets = xmodule.static_content:main',
        ],
    }
)
