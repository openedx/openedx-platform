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
        "openedx.block_structure_transformer": [
            "library_content = lms.djangoapps.course_blocks.transformers.library_content:ContentLibraryTransformer",
            "library_content_randomize = lms.djangoapps.course_blocks.transformers.library_content:ContentLibraryOrderTransformer",  # lint-amnesty, pylint: disable=line-too-long
            "split_test = lms.djangoapps.course_blocks.transformers.split_test:SplitTestTransformer",
            "start_date = lms.djangoapps.course_blocks.transformers.start_date:StartDateTransformer",
            "user_partitions = lms.djangoapps.course_blocks.transformers.user_partitions:UserPartitionTransformer",
            "visibility = lms.djangoapps.course_blocks.transformers.visibility:VisibilityTransformer",
            "hidden_content = lms.djangoapps.course_blocks.transformers.hidden_content:HiddenContentTransformer",
            "course_blocks_api = lms.djangoapps.course_api.blocks.transformers.blocks_api:BlocksAPITransformer",
            "milestones = lms.djangoapps.course_api.blocks.transformers.milestones:MilestonesAndSpecialExamsTransformer",  # lint-amnesty, pylint: disable=line-too-long
            "grades = lms.djangoapps.grades.transformer:GradesTransformer",
            "completion = lms.djangoapps.course_api.blocks.transformers.block_completion:BlockCompletionTransformer",
            "load_override_data = lms.djangoapps.course_blocks.transformers.load_override_data:OverrideDataTransformer",
            "content_type_gate = openedx.features.content_type_gating.block_transformers:ContentTypeGateTransformer",
            "access_denied_message_filter = lms.djangoapps.course_blocks.transformers.access_denied_filter:AccessDeniedMessageFilterTransformer",  # lint-amnesty, pylint: disable=line-too-long
            "open_assessment_transformer = lms.djangoapps.courseware.transformers:OpenAssessmentDateTransformer",
            'effort_estimation = openedx.features.effort_estimation.api:EffortEstimationTransformer',
            'discussions_link = openedx.core.djangoapps.discussions.transformers:DiscussionsTopicLinkTransformer',
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
