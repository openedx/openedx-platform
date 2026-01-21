"""  # lint-amnesty, pylint: disable=django-not-configured
Setup script for the Open edX package.
"""

from setuptools import setup

XBLOCKS = [
    "about = xmodule.html_block:AboutBlock",
    "book = xmodule.template_block:TranslateCustomTagBlock",
    "annotatable = xmodule.annotatable_block:AnnotatableBlock",
    "chapter = xmodule.seq_block:SectionBlock",
    "conditional = xmodule.conditional_block:ConditionalBlock",
    "course = xmodule.course_block:CourseBlock",
    "course_info = xmodule.html_block:CourseInfoBlock",
    "customtag = xmodule.template_block:CustomTagBlock",
    "custom_tag_template = xmodule.template_block:CustomTagTemplateBlock",
    "discuss = xmodule.template_block:TranslateCustomTagBlock",
    "discussion = xmodule.discussion_block:DiscussionXBlock",
    "error = xmodule.error_block:ErrorBlock",
    "hidden = xmodule.hidden_block:HiddenBlock",
    "html = xmodule.html_block:HtmlBlock",
    "itembank = xmodule.item_bank_block:ItemBankBlock",
    "image = xmodule.template_block:TranslateCustomTagBlock",
    "library = xmodule.library_root_xblock:LibraryRoot",
    "library_content = xmodule.library_content_block:LegacyLibraryContentBlock",
    "lti = xmodule.lti_block:LTIBlock",
    "poll_question = xmodule.poll_block:PollBlock",
    "problem = xmodule.capa_block:ProblemBlock",
    "randomize = xmodule.randomize_block:RandomizeBlock",
    "sequential = xmodule.seq_block:SequenceBlock",
    "slides = xmodule.template_block:TranslateCustomTagBlock",
    "split_test = xmodule.split_test_block:SplitTestBlock",
    "static_tab = xmodule.html_block:StaticTabBlock",
    "unit = xmodule.unit_block:UnitBlock",
    "vertical = xmodule.vertical_block:VerticalBlock",
    "video = xmodule.video_block:VideoBlock",
    "videoalpha = xmodule.video_block:VideoBlock",
    "videodev = xmodule.template_block:TranslateCustomTagBlock",
    "word_cloud = xmodule.word_cloud_block:WordCloudBlock",
    "wrapper = xmodule.wrapper_block:WrapperBlock",
]
XBLOCKS_ASIDES = [
    'tagging_aside = cms.lib.xblock.tagging:StructuredTagsAside',
]


setup(
    name="Open edX",
    version='0.13',
    install_requires=["setuptools"],
    requires=[],
    # NOTE: These are not the names we should be installing.  This tree should
    # be reorganized to be a more conventional Python tree.
    packages=[
        "cms",
        "common",
        "lms",
        "openedx",
        "xmodule",
    ],
    package_data={
        'xmodule': ['js/module/*'],
    },
    entry_points={
        "openedx.course_tab": [
            "ccx = lms.djangoapps.ccx.plugins:CcxCourseTab",
            "courseware = lms.djangoapps.courseware.tabs:CoursewareTab",
            "dates = lms.djangoapps.courseware.tabs:DatesTab",
            "discussion = lms.djangoapps.discussion.plugins:DiscussionTab",
            "edxnotes = lms.djangoapps.edxnotes.plugins:EdxNotesTab",
            "external_discussion = lms.djangoapps.courseware.tabs:ExternalDiscussionCourseTab",
            "external_link = lms.djangoapps.courseware.tabs:ExternalLinkCourseTab",
            "html_textbooks = lms.djangoapps.courseware.tabs:HtmlTextbookTabs",
            "instructor = lms.djangoapps.instructor.views.instructor_dashboard:InstructorDashboardTab",
            "lti_discussion = openedx.features.lti_course_tab.tab:DiscussionLtiCourseTab",
            "lti_live = openedx.core.djangoapps.course_live.tab:CourseLiveTab",
            "lti_tab = openedx.features.lti_course_tab.tab:LtiCourseTab",
            "pdf_textbooks = lms.djangoapps.courseware.tabs:PDFTextbookTabs",
            "progress = lms.djangoapps.courseware.tabs:ProgressTab",
            "static_tab = xmodule.tabs:StaticTab",
            "syllabus = lms.djangoapps.courseware.tabs:SyllabusTab",
            "teams = lms.djangoapps.teams.plugins:TeamsTab",
            "textbooks = lms.djangoapps.courseware.tabs:TextbookTabs",
            "wiki = lms.djangoapps.course_wiki.tab:WikiTab",
        ],
        "openedx.course_app": [
            "calculator = lms.djangoapps.courseware.plugins:CalculatorCourseApp",
            "custom_pages = lms.djangoapps.courseware.plugins:CustomPagesCourseApp",
            "dates = lms.djangoapps.courseware.plugins:DatesCourseApp",
            "discussion = openedx.core.djangoapps.discussions.plugins:DiscussionCourseApp",
            "edxnotes = lms.djangoapps.edxnotes.plugins:EdxNotesCourseApp",
            "live = openedx.core.djangoapps.course_live.plugins:LiveCourseApp",
            "ora_settings = lms.djangoapps.courseware.plugins:ORASettingsApp",
            "proctoring = lms.djangoapps.courseware.plugins:ProctoringCourseApp",
            "progress = lms.djangoapps.courseware.plugins:ProgressCourseApp",
            "teams = lms.djangoapps.teams.plugins:TeamsCourseApp",
            "textbooks = lms.djangoapps.courseware.plugins:TextbooksCourseApp",
            "wiki = lms.djangoapps.course_wiki.plugins.course_app:WikiCourseApp",
        ],
        "openedx.course_tool": [
            "calendar_sync_toggle = openedx.features.calendar_sync.plugins:CalendarSyncToggleTool",
            "course_bookmarks = openedx.features.course_bookmarks.plugins:CourseBookmarksTool",
            "course_updates = openedx.features.course_experience.plugins:CourseUpdatesTool",
            "financial_assistance = lms.djangoapps.courseware.course_tools:FinancialAssistanceTool",
        ],
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
        "lms.djangoapp": [
            "ace_common = openedx.core.djangoapps.ace_common.apps:AceCommonConfig",
            "content_libraries = openedx.core.djangoapps.content_libraries.apps:ContentLibrariesConfig",
            "course_apps = openedx.core.djangoapps.course_apps.apps:CourseAppsConfig",
            "course_live = openedx.core.djangoapps.course_live.apps:CourseLiveConfig",
            "courseware_api = openedx.core.djangoapps.courseware_api.apps:CoursewareAPIConfig",
            "credentials = openedx.core.djangoapps.credentials.apps:CredentialsConfig",
            "discussion = lms.djangoapps.discussion.apps:DiscussionConfig",
            "discussions = openedx.core.djangoapps.discussions.apps:DiscussionsConfig",
            "grades = lms.djangoapps.grades.apps:GradesConfig",
            "plugins = openedx.core.djangoapps.plugins.apps:PluginsConfig",
            "theming = openedx.core.djangoapps.theming.apps:ThemingConfig",
            "bookmarks = openedx.core.djangoapps.bookmarks.apps:BookmarksConfig",
            "zendesk_proxy = openedx.core.djangoapps.zendesk_proxy.apps:ZendeskProxyConfig",
            "instructor = lms.djangoapps.instructor.apps:InstructorConfig",
            "password_policy = openedx.core.djangoapps.password_policy.apps:PasswordPolicyConfig",
            "user_authn = openedx.core.djangoapps.user_authn.apps:UserAuthnConfig",
            "program_enrollments = lms.djangoapps.program_enrollments.apps:ProgramEnrollmentsConfig",
        ],
        "cms.djangoapp": [
            "ace_common = openedx.core.djangoapps.ace_common.apps:AceCommonConfig",
            "bookmarks = openedx.core.djangoapps.bookmarks.apps:BookmarksConfig",
            "course_live = openedx.core.djangoapps.course_live.apps:CourseLiveConfig",
            "content_libraries = openedx.core.djangoapps.content_libraries.apps:ContentLibrariesConfig",
            "content_staging = openedx.core.djangoapps.content_staging.apps:ContentStagingAppConfig",
            "course_apps = openedx.core.djangoapps.course_apps.apps:CourseAppsConfig",
            # Importing an LMS app into the Studio process is not a good
            # practice. We're ignoring this for Discussions here because its
            # placement in LMS is a historical artifact. The eventual goal is to
            # consolidate the multiple discussions-related Django apps and
            # either put them in the openedx/ dir, or in another repo entirely.
            "discussion = lms.djangoapps.discussion.apps:DiscussionConfig",
            "discussions = openedx.core.djangoapps.discussions.apps:DiscussionsConfig",
            "instructor = lms.djangoapps.instructor.apps:InstructorConfig",
            "olx_rest_api = openedx.core.djangoapps.olx_rest_api.apps:OlxRestApiAppConfig",
            "password_policy = openedx.core.djangoapps.password_policy.apps:PasswordPolicyConfig",
            "plugins = openedx.core.djangoapps.plugins.apps:PluginsConfig",
            "theming = openedx.core.djangoapps.theming.apps:ThemingConfig",
            "user_authn = openedx.core.djangoapps.user_authn.apps:UserAuthnConfig",
            "zendesk_proxy = openedx.core.djangoapps.zendesk_proxy.apps:ZendeskProxyConfig",
        ],
        'openedx.learning_context': [
            'lib = openedx.core.djangoapps.content_libraries.library_context:LibraryContextImpl',
        ],
        'openedx.dynamic_partition_generator': [
            'content_type_gating = openedx.features.content_type_gating.partitions:create_content_gating_partition',
            'enrollment_track = xmodule.partitions.enrollment_track_partition_generator:create_enrollment_track_partition',  # lint-amnesty, pylint: disable=line-too-long
            'team = openedx.core.lib.teams_config:create_team_set_partition',
        ],
        'xblock.v1': XBLOCKS,
        'xblock_asides.v1': XBLOCKS_ASIDES,
        'console_scripts': [
            'xmodule_assets = xmodule.static_content:main',
        ],
    }
)
