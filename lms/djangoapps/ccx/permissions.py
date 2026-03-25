"""
Permission definitions for the ccx djangoapp
"""

from bridgekeeper import perms  # noqa: I001

from lms.djangoapps.courseware.rules import HasAccessRule

VIEW_CCX_COACH_DASHBOARD = 'ccx.view_ccx_coach_dashboard'
perms[VIEW_CCX_COACH_DASHBOARD] = HasAccessRule('staff')
