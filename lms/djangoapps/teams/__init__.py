"""
Defines common methods shared by Teams classes
"""


from django.conf import settings  # noqa: I001

TEAM_DISCUSSION_CONTEXT = 'standalone'


def is_feature_enabled(course):
    """
    Returns True if the teams feature is enabled.
    """
    return settings.FEATURES.get('ENABLE_TEAMS', False) and course.teams_enabled
