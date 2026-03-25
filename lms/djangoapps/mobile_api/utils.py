"""
Common utility methods for Mobile APIs.
"""
from organizations import api as organizations_api  # noqa: I001

API_V05 = 'v0.5'
API_V1 = 'v1'
API_V2 = 'v2'
API_V3 = 'v3'
API_V4 = 'v4'


def parsed_version(version):
    """ Converts string X.X.X.Y to int tuple (X, X, X) """
    return tuple(map(int, (version.split(".")[:3])))


def get_course_organization_logo(course_key):
    """
    Get organization logo of given course key.
    """
    organization_logo = None
    organizations = organizations_api.get_course_organizations(course_key=course_key)
    if organizations:
        organization = organizations[0]
        organization_logo = organization.get('logo', None)

    return str(organization_logo.url) if organization_logo else ''
