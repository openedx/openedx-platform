"""
Default Authentication classes that are ONLY meant to be used by
DEFAULT_AUTHENTICATION_CLASSES for observability purposes.
"""
from edx_django_utils.monitoring import set_custom_attribute
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework.authentication import SessionAuthentication


class DefaultSessionAuthentication(SessionAuthentication):
    """ Default SessionAuthentication with observability """

    def authenticate(self, request):
        # .. custom_attribute_name: using_default_auth_classes
        # .. custom_attribute_description: This custom attribute will always be
        #     True (if not NULL), and signifies that a default authentication
        #     class was used. This can be used to find endpoints using the
        #     default authentication classes.
        set_custom_attribute('using_default_auth_classes', True)

        try:
            user_and_auth = super().authenticate(request)
            if user_and_auth:
                # .. custom_attribute_name: session_auth_result
                # .. custom_attribute_description: The result of session auth, represented
                #      by: 'success', 'failure', or 'n/a'.
                set_custom_attribute('session_auth_result', 'success')
            else:
                set_custom_attribute('session_auth_result', 'n/a')
            return user_and_auth
        except Exception as exception:  # noqa: F841
            set_custom_attribute('session_auth_result', 'failure')
            raise


class DefaultJwtAuthentication(JwtAuthentication):
    """
    Default JwtAuthentication with observability

    Note that the plan is to add JwtAuthentication as a default, but it
    is not yet used. This class will be used during the transition.
    """

    def authenticate(self, request):
        # .. custom_attribute_name: using_default_auth_classes
        # .. custom_attribute_description: This custom attribute will always be
        #     True (if not NULL), and signifies that a default authentication
        #     class was used. This can be used to find endpoints using the
        #     default authentication classes.
        set_custom_attribute('using_default_auth_classes', True)

        # Unlike the other DRF authentication classes, JwtAuthentication already
        # includes a jwt_auth_result custom attribute, so we do not need to
        # reimplement that observability in this class.
        return super().authenticate(request)
