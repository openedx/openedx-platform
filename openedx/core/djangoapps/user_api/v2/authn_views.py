"""Registration, sign-in and password reset endpoints of version 2 of the User API."""

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.permissions import AllowAny

from openedx.core.djangoapps.user_authn.views import login, password_reset, register

from . import authn_schemas
from .permissions import RestrictedApplicationGuardMixin

# Each view below is the authentication app's own view, published under a
# description of its own and nothing else. A request is answered by the method
# that view defines, with every decorator on it, so the limits on how often a
# request may be sent are counted together with the app's older addresses.
# Those limits are counted per decorated method and per the name of the class
# it runs on: a method written here would start counting afresh, and so would
# a class renamed here, which is why each keeps the name of the one it extends.
# Like every view of this version, each refuses a restricted application's token.


@extend_schema_view(
    get=extend_schema(
        summary="Describe the registration form",
        description=(
            "Returns the fields an account is registered with on this site, in the order to show them, with the "
            "label, kind, limits and choices of each, and the address the form is submitted to. Sets the CSRF "
            "cookie."
        ),
        responses=authn_schemas.REGISTRATION_FORM_RESPONSES,
    ),
    post=extend_schema(
        summary="Register an account",
        description=(
            "Creates an account from the fields the registration form lists and signs the caller in as it, "
            "setting the session cookies. The body is read as a submitted form; a body sent as JSON is read as "
            "an empty one. No CSRF token is needed, and a session the caller already holds is not consulted. "
            "Refused once too many accounts were registered from the address the request comes from."
        ),
        request=authn_schemas.REGISTRATION_REQUEST,
        responses=authn_schemas.REGISTRATION_RESPONSES,
    ),
)
class RegistrationView(RestrictedApplicationGuardMixin, register.RegistrationView):
    """Describes the registration form and registers an account from it, for any caller."""

    permission_classes = (AllowAny,)


@extend_schema_view(
    get=extend_schema(
        summary="Describe the password reset form",
        description=(
            "Returns the form a password reset is asked for with and the address it is submitted to. Sets the "
            "CSRF cookie."
        ),
        responses=authn_schemas.PASSWORD_RESET_FORM_RESPONSES,
    ),
)
class PasswordResetView(RestrictedApplicationGuardMixin, password_reset.PasswordResetView):
    """Describes the form a password reset is asked for with, for any caller."""

    permission_classes = (AllowAny,)


@extend_schema_view(
    post=extend_schema(
        summary="Check a password reset token",
        description=(
            "Says whether a token from a password reset email is still valid. A caller who is signed in is told "
            "a token is valid only when it belongs to their own account. A valid token for an inactive account "
            "activates the account. Callers who are not signed in may check only a few tokens in each period, "
            "and a caller signed in with a session sends the CSRF token."
        ),
        request=authn_schemas.TOKEN_VALIDATION_REQUEST,
        responses=authn_schemas.TOKEN_VALIDATION_RESPONSES,
    ),
)
class PasswordResetTokenValidation(RestrictedApplicationGuardMixin, password_reset.PasswordResetTokenValidation):
    """Says whether a password reset token is valid, to any caller."""

    permission_classes = (AllowAny,)


@extend_schema_view(
    post=extend_schema(
        summary="Check registration values",
        description=(
            "Checks the values a person is about to register with and answers with a decision for each field "
            "sent: an empty string where the value is acceptable, otherwise the message to show. Naming a field "
            "in `form_field_key` checks that field alone. No CSRF token is needed. Refused once too many checks "
            "were sent from the address the request comes from."
        ),
        request=authn_schemas.REGISTRATION_VALIDATION_REQUEST,
        responses=authn_schemas.REGISTRATION_VALIDATION_RESPONSES,
    ),
)
class RegistrationValidationView(RestrictedApplicationGuardMixin, register.RegistrationValidationView):
    """Checks values a person is about to register with, for any caller."""

    permission_classes = (AllowAny,)


@extend_schema_view(
    get=extend_schema(
        summary="Describe the sign-in form",
        description=(
            "Returns the form the site's sign-in page is built from and sets the CSRF cookie that signing in "
            "needs."
        ),
        responses=authn_schemas.LOGIN_FORM_RESPONSES,
    ),
    post=extend_schema(
        summary="Sign in",
        description=(
            "Signs the caller in with an account's password and sets the session cookies. The account is named "
            "in `email_or_username`, by its email address or its username. The body is read as a submitted "
            "form; a body sent as JSON is read as an empty one. The CSRF token is needed, in the `X-CSRFToken` "
            "header or the `csrfmiddlewaretoken` field, together with the cookie the sign-in form sets. A "
            "session the caller already holds is not consulted. After too many failed attempts, signing in is "
            "refused for a while."
        ),
        request=authn_schemas.LOGIN_REQUEST,
        responses=authn_schemas.LOGIN_RESPONSES,
        # The view it extends names a scheme for the CSRF token that no
        # published document declares; the token is described above instead.
        auth=[{}],
    ),
)
class LoginSessionView(RestrictedApplicationGuardMixin, login.LoginSessionView):
    """Describes the sign-in form and signs a caller in, for any caller."""

    permission_classes = (AllowAny,)
