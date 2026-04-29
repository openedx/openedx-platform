"""
Utility functions for course_wiki.
"""


from django.core.exceptions import ObjectDoesNotExist

import lms.djangoapps.courseware
from xmodule import modulestore  # pylint: disable=wrong-import-order


def user_is_article_course_staff(user, article):
    """
    The root of a course wiki is /<course_number>. This means in case there
    are two courses which have the same course_number they will end up with
    the same course wiki root e.g. MITx/Phy101/Spring and HarvardX/Phy101/Fall
    will share /Phy101.

    This looks at the course wiki root of the article and returns True if
    the user belongs to a group whose name starts with 'instructor_' or
    'staff_' and contains '/<course_wiki_root_slug>/'. So if the user is
    staff on course MITx/Phy101/Spring they will be in
    'instructor_MITx/Phy101/Spring' or 'staff_MITx/Phy101/Spring' groups and
    so this will return True.
    """

    wiki_slug = article_course_wiki_root_slug(article)

    if wiki_slug is None:
        return False

    modstore = modulestore.django.modulestore()
    return _has_wiki_staff_access(user, wiki_slug, modstore)


def _has_wiki_staff_access(user, wiki_slug, modstore):
    """Returns whether the user has staff access to the wiki represented by wiki_slug"""
    course_keys = modstore.get_courses_for_wiki(wiki_slug)

    # The wiki expects article slugs to contain at least one non-digit so if
    # the course number is just a number the course wiki root slug is set to
    # be '<course_number>_'. This means slug '202_' can belong to either
    # course numbered '202_' or '202' and so we need to consider both.
    if wiki_slug.endswith('_') and slug_is_numerical(wiki_slug[:-1]):
        course_keys.extend(modstore.get_courses_for_wiki(wiki_slug[:-1]))

    for course_key in course_keys:
        course = modstore.get_course(course_key)
        if lms.djangoapps.courseware.access.has_access(user, 'staff', course, course_key):
            return True
    return False


def slug_is_numerical(slug):
    """Returns whether the slug can be interpreted as a number."""
    try:
        float(slug)
    except ValueError:
        return False

    return True


def course_wiki_slug(course):
    """Returns the slug for the course wiki root."""
    slug = course.wiki_slug

    # Django-wiki expects article slug to be non-numerical. In case the
    # course number is numerical append an underscore.
    if slug_is_numerical(slug):
        slug = slug + "_"

    return slug


def article_course_wiki_root_slug(article):
    """
    We assume the second level ancestor is the course wiki root. Examples:
    / returns None
    /Phy101 returns 'Phy101'
    /Phy101/Mechanics returns 'Phy101'
    /Chem101/Metals/Iron returns 'Chem101'

    Note that someone can create an article /random-article/sub-article on the
    wiki. In this case this function will return 'some-random-article' even
    if no course with course number 'some-random-article' exists.
    """

    try:
        urlpath = article.urlpath_set.get()
    except ObjectDoesNotExist:
        return None

    # Ancestors of /Phy101/Mechanics/Acceleration/ is a list of URLPaths
    # ['Root', 'Phy101', 'Mechanics']
    ancestors = urlpath.cached_ancestors

    course_wiki_root_urlpath = None

    if len(ancestors) == 0:  # It is the wiki root article.
        course_wiki_root_urlpath = None
    elif len(ancestors) == 1:  # It is a course wiki root article.
        course_wiki_root_urlpath = urlpath
    else:  # It is an article inside a course wiki.
        course_wiki_root_urlpath = ancestors[1]

    if course_wiki_root_urlpath is not None:
        return course_wiki_root_urlpath.slug

    return None
