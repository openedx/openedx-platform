"""
Middleware that checks user standing for the purpose of keeping users with
disabled accounts from accessing the site.
"""


from django.conf import settings
from django.http import HttpResponseForbidden
from django.utils.deprecation import MiddlewareMixin
from django.utils.translation import gettext as _

from common.djangoapps.student.models import UserStanding
from openedx.core.djangolib.markup import HTML, Text


class UserStandingMiddleware(MiddlewareMixin):
    """
    Checks a user's standing on request. Returns a 403 if the user's
    status is 'disabled'.
    """
    def process_request(self, request):  # lint-amnesty, pylint: disable=missing-function-docstring
        user = request.user
        try:
            user_account = UserStanding.objects.get(user=user.id)
            # because user is a unique field in UserStanding, there will either be
            # one or zero user_accounts associated with a UserStanding
        except UserStanding.DoesNotExist:
            pass
        else:
            if user_account.account_status == UserStanding.ACCOUNT_DISABLED:
                msg = Text(_(
                    'Your account has been disabled. If you believe '
                    'this was done in error, please contact us at '
                    '{support_email}'
                )).format(
                    support_email=HTML('<a href="mailto:{address}?subject={subject_line}">{address}</a>').format(
                        address=settings.DEFAULT_FEEDBACK_EMAIL,
                        subject_line=_('Disabled Account'),
                    ),
                )
                return HttpResponseForbidden(msg)
