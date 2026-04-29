"""
Common methods for cms commands to use
"""


from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from opaque_keys.edx.keys import CourseKey

from xmodule.modulestore.django import modulestore


def user_from_str(identifier):
    """
    Return a user identified by the given string. The string could be an email
    address, or a stringified integer corresponding to the ID of the user in
    the database. If no user could be found, a User.DoesNotExist exception
    will be raised.
    """
    try:
        user_id = int(identifier)
    except ValueError:
        return User.objects.get(email=identifier)

    return User.objects.get(id=user_id)


def get_course_versions(course_key):
    """
    Fetches the latest course versions
    :param course_key:
    :return: { 'draft-branch' : value1, 'published-branch' : value2}
    """
    course_locator = CourseKey.from_string(course_key)
    store = modulestore()._get_modulestore_for_courselike(course_locator)  # pylint: disable=protected-access
    index_entry = store.get_course_index(course_locator)
    if index_entry is not None:
        return {
            'draft-branch': index_entry['versions']['draft-branch'],
            'published-branch': index_entry['versions']['published-branch']
        }

    return None
