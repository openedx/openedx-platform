"""
Permission definitions for the certificates djangoapp
"""

from bridgekeeper import perms  # noqa: I001

from lms.djangoapps.courseware.rules import HasAccessRule

PREVIEW_CERTIFICATES = 'certificates.preview_certificates'
perms[PREVIEW_CERTIFICATES] = HasAccessRule('staff')
VIEW_ALL_CERTIFICATES = 'certificates.view_all_certificates'
perms[VIEW_ALL_CERTIFICATES] = HasAccessRule('certificates')
GENERATE_ALL_CERTIFICATES = 'certificates.generate_all_certificates'
perms[GENERATE_ALL_CERTIFICATES] = HasAccessRule('certificates')
