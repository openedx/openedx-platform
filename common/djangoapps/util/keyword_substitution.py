"""
keyword_substitution.py

Contains utility functions to help substitute keywords in a text body with
the appropriate user / course data.

Supported:
    LMS:
        - %%USER_ID%% => anonymous user id
        - %%USER_FULLNAME%% => User's full name
        - %%COURSE_DISPLAY_NAME%% => display name of the course
        - %%COURSE_END_DATE%% => end date of the course

Usage:
    Call substitute_keywords_with_data where substitution is
    needed. Currently called in:
        - LMS: Announcements + Bulk emails
        - CMS: Not called
"""


from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.utils.html import escape as html_escape_fn

from common.djangoapps.student.models import anonymous_id_for_user


def anonymous_id_from_user_id(user_id):
    """
    Gets a user's anonymous id from their user id
    """
    user = User.objects.get(id=user_id)
    return anonymous_id_for_user(user, None)


def substitute_keywords(string, user_id, context, html_escape=False):
    """
    Replaces all %%-encoded words using KEYWORD_FUNCTION_MAP mapping functions

    Iterates through all keywords that must be substituted and replaces
    them by calling the corresponding functions stored in KEYWORD_FUNCTION_MAP.

    Functions stored in KEYWORD_FUNCTION_MAP must return a replacement string.

    If ``html_escape`` is True, substituted values are HTML-escaped before
    insertion. Callers that render the output as HTML (e.g. the edx-ace
    bulk email body template, which wraps the substituted message in
    ``{% autoescape off %}``) MUST pass ``html_escape=True`` to prevent
    stored XSS via user-controlled fields such as ``%%USER_FULLNAME%%``.
    """

    # do this lazily to avoid unneeded database hits
    KEYWORD_FUNCTION_MAP = {
        '%%USER_ID%%': lambda: anonymous_id_from_user_id(user_id),
        '%%USER_FULLNAME%%': lambda: context.get('name'),
        '%%COURSE_DISPLAY_NAME%%': lambda: context.get('course_title'),
        '%%COURSE_END_DATE%%': lambda: context.get('course_end_date'),
    }

    for key in KEYWORD_FUNCTION_MAP.keys():  # lint-amnesty, pylint: disable=consider-iterating-dictionary
        if key in string:
            substitutor = KEYWORD_FUNCTION_MAP[key]
            value = substitutor()
            if html_escape and value is not None:
                value = html_escape_fn(value)
            string = string.replace(key, value)

    return string


def substitute_keywords_with_data(string, context, html_escape=False):
    """
    Given an email context, replaces all %%-encoded words in the given string
    `context` is a dictionary that should include `user_id` and `course_title`
    keys

    Pass ``html_escape=True`` when the output will be rendered as HTML so
    user-controlled substitution values are escaped (see
    ``substitute_keywords``).
    """

    # Do not proceed without parameters: Compatibility check with existing tests
    # that do not supply these parameters
    user_id = context.get('user_id')
    course_title = context.get('course_title')

    if user_id is None or course_title is None:
        return string

    return substitute_keywords(string, user_id, context, html_escape=html_escape)
