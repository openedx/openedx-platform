"""Published bodies of the registration, sign-in and password reset endpoints of version 2 of the User API."""

from drf_spectacular.utils import OpenApiResponse
from rest_framework.settings import api_settings

FORM_MEDIA_TYPE = "application/x-www-form-urlencoded"
MULTIPART_MEDIA_TYPE = "multipart/form-data"


def read_as_a_form(shape):
    """
    Name a shape under the two media types a handler reading the submitted form accepts.

    Such a handler reads the fields a form carries and nothing else, so a body
    sent as JSON reaches it as an empty form and is refused for what it lacks.
    """
    return {FORM_MEDIA_TYPE: shape, MULTIPART_MEDIA_TYPE: shape}


def read_in_every_form(shape):
    """Name a shape under each media type a view that declares no parsers reads a body in."""
    return {parser.media_type: shape for parser in api_settings.DEFAULT_PARSER_CLASSES}


_TEXT = {"type": "string"}

FORM_FIELD = {
    "type": "object",
    "description": "One input of the form.",
    "properties": {
        "name": {"type": "string", "description": "Name the value is submitted under."},
        "label": {
            "type": "string",
            "nullable": True,
            "description": "Label to show beside the input, or null for an input the site adds that is given none.",
        },
        "type": {
            "type": "string",
            "description": (
                "Kind of input, such as `text`, `email`, `password`, `select`, `textarea`, `checkbox` "
                "or `plaintext`."
            ),
        },
        "defaultValue": {
            "description": (
                "Value the input starts with: text, or true or false for a checkbox. Null for an input the site "
                "adds that is given none."
            ),
        },
        "placeholder": {
            "type": "string",
            "nullable": True,
            "description": (
                "Hint to show in the empty input. An input the site adds carries the value it starts with here, "
                "or null where it is given none."
            ),
        },
        "instructions": {"type": "string", "description": "Help text to show with the input."},
        "exposed": {"type": "boolean", "description": "Whether to show the input although it is optional."},
        "required": {"type": "boolean", "description": "Whether the form cannot be submitted without a value."},
        "restrictions": {
            "type": "object",
            "description": "Limits the value is held to, such as `min_length` and `max_length`.",
        },
        "errorMessages": {
            "type": "object",
            "additionalProperties": _TEXT,
            "description": "Messages to show for a refused value, by the reason it was refused.",
        },
        "supplementalLink": {"type": "string", "description": "Address of further help, or an empty string."},
        "supplementalText": {"type": "string", "description": "Text of the link to further help."},
        "loginIssueSupportLink": {"type": "string", "description": "Address of help with signing in."},
        "options": {
            "type": "array",
            "description": "Choices a `select` input offers. Absent from every other kind of input.",
            "items": {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "description": "Value submitted for this choice."},
                    "name": {"type": "string", "description": "Text to show for this choice."},
                    "default": {"type": "boolean", "description": "Whether this choice starts selected."},
                },
                "required": ["value", "name", "default"],
            },
        },
    },
    "required": [
        "name",
        "label",
        "type",
        "defaultValue",
        "placeholder",
        "instructions",
        "exposed",
        "required",
        "restrictions",
        "errorMessages",
        "supplementalLink",
        "supplementalText",
        "loginIssueSupportLink",
    ],
}

FORM_DESCRIPTION = {
    "type": "object",
    "description": "A form a client builds its page from, field by field.",
    "properties": {
        "method": {"type": "string", "description": "HTTP method the form is submitted with."},
        "submit_url": {"type": "string", "description": "Address the form is submitted to."},
        "fields": {"type": "array", "items": FORM_FIELD, "description": "The inputs, in the order to show them."},
    },
    "required": ["method", "submit_url", "fields"],
}

#: A body any DRF view answers a refused request with.
REFUSAL = {
    "type": "object",
    "properties": {"detail": {"type": "string", "description": "Why the request was refused."}},
    "required": ["detail"],
}

#: Published by every operation here. Which representations an answer can be
#: given in is settled as the request enters, before the handler runs.
NOT_ACCEPTABLE = OpenApiResponse(
    REFUSAL,
    description="The `Accept` header of the request names no representation this endpoint can answer in.",
)

#: Published by every operation here that reads a body, which is parsed before the handler reads it.
UNREADABLE_BODY = OpenApiResponse(REFUSAL, description="The request body is in a media type this endpoint cannot read.")


def or_unparsed(shape):
    """
    Offer ``shape`` beside the body a request whose body cannot be parsed is refused with.

    The body is parsed when the handler first reads it, so one that cannot be
    parsed in the media type it is sent as is refused with ``detail`` alone,
    before the handler can answer with a body of its own.
    """
    return {"oneOf": [shape, REFUSAL]}

REGISTRATION_FORM_RESPONSES = {
    200: OpenApiResponse(
        FORM_DESCRIPTION,
        description=(
            "The registration form. Which fields it lists, and which of them are required, depend on how the "
            "site is configured."
        ),
    ),
    406: NOT_ACCEPTABLE,
}

REGISTRATION_FIELDS = {
    "type": "object",
    "description": (
        "The fields of the registration form, each under its name. The fields listed as required are the ones "
        "a site requires unless it is configured otherwise; the form description lists the ones this site asks "
        "for, and every field it lists is accepted here. A registration carries a password, unless it completes "
        "a sign-in through another provider, for which the server generates the password: the sign-in under way "
        "in the caller's session, named in `social_auth_provider`, or one the client made with the provider "
        "itself, named in `provider` with the access token the provider issued. Both ways apply only where the "
        "site lets people sign in through other providers."
    ),
    "properties": {
        "name": {"type": "string", "description": "Full name of the person registering."},
        "username": {"type": "string", "description": "Name to register the account under."},
        "email": {"type": "string", "description": "Email address of the account."},
        "confirm_email": {
            "type": "string",
            "description": "The email address again, where the site asks for it.",
        },
        "password": {
            "type": "string",
            "description": (
                "Password of the account. Left out of a registration that completes a sign-in through another "
                "provider, for which the server generates one and ignores any value sent."
            ),
            "format": "password",
        },
        "social_auth_provider": {
            "type": "string",
            "description": (
                "Name of the provider of the sign-in under way in the caller's session, which the form lists "
                "while one is under way. A registration naming one after that sign-in has expired is refused."
            ),
        },
        "provider": {
            "type": "string",
            "description": (
                "Backend of the provider the client signed the person in with itself, such as `google-oauth2`."
            ),
        },
        "access_token": {
            "type": "string",
            "description": "Access token that provider issued, through which the new account is linked to it.",
        },
        "honor_code": {"type": "string", "description": "`true` once the site's terms are accepted."},
        "terms_of_service": {
            "type": "string",
            "description": "`true` once the terms of service are accepted. Taken from `honor_code` when absent.",
        },
        "country": {"type": "string", "description": "Two-letter code of the country of residence."},
    },
    "required": ["name", "username", "email", "honor_code"],
    "anyOf": [
        {"required": ["password"]},
        {"required": ["social_auth_provider"]},
        {"required": ["provider", "access_token"]},
    ],
    "additionalProperties": _TEXT,
}

REGISTRATION_REQUEST = read_as_a_form(REGISTRATION_FIELDS)

REGISTERED = {
    "type": "object",
    "properties": {
        "authenticated_user": {
            "type": "object",
            "description": "The account created, which the caller is now signed in as.",
            "properties": {
                "username": {"type": "string", "description": "Name the account was registered under."},
                "full_name": {"type": "string", "description": "Full name on the account's profile."},
                "user_id": {"type": "integer", "description": "Identifier of the account."},
            },
            "required": ["username", "full_name", "user_id"],
        },
        "success": {"type": "boolean", "description": "Always true."},
        "redirect_url": {"type": "string", "description": "Where to send the person next."},
    },
    "required": ["authenticated_user", "success", "redirect_url"],
}

REGISTRATION_REFUSAL = {
    "type": "object",
    "description": (
        "Each field that was refused, under its name, with the messages to show for it, and what the "
        "refusal was."
    ),
    "properties": {
        "error_code": {
            "type": "string",
            "description": (
                "What the refusal was, such as `validation-error`, `invalid-country`, `duplicate-email`, "
                "`duplicate-username`, `duplicate-email-username` or `forbidden-request`."
            ),
        },
        "username_suggestions": {
            "type": "array",
            "items": _TEXT,
            "description": "Usernames still free to register, offered when the one sent is taken.",
        },
    },
    "additionalProperties": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"user_message": {"type": "string", "description": "Message to show for the field."}},
            "required": ["user_message"],
        },
    },
}

REGISTRATION_RESPONSES = {
    200: OpenApiResponse(REGISTERED, description="The account was created and the caller is signed in as it."),
    400: OpenApiResponse(
        or_unparsed(REGISTRATION_REFUSAL),
        description=(
            "A field is missing or not acceptable, the country is not one the site knows, or a sign-in through "
            "another provider the registration relied on has expired. A body that cannot be parsed in the media "
            "type it is sent as is refused with `detail` alone."
        ),
    ),
    403: OpenApiResponse(
        REGISTRATION_REFUSAL,
        description=(
            "Too many requests to register were sent from the address the request came from, whether or not "
            "they created an account (`error_code` is `forbidden-request`), or a step the site adds to "
            "registration refused it. Where the site requires signing in through another provider, or creates "
            "no accounts here, the refusal is an HTML page instead of this body."
        ),
    ),
    406: NOT_ACCEPTABLE,
    409: OpenApiResponse(
        REGISTRATION_REFUSAL,
        description="An account already has that email address or that username.",
    ),
    415: UNREADABLE_BODY,
}

PASSWORD_RESET_FORM_RESPONSES = {
    200: OpenApiResponse(
        FORM_DESCRIPTION,
        description="The form a password reset is asked for with, which asks for the email address of the account.",
    ),
    406: NOT_ACCEPTABLE,
}

TOKEN_VALIDATION_REQUEST = read_in_every_form({
    "type": "object",
    "properties": {
        "token": {"type": "string", "description": "The token the password reset email carries."},
    },
})

TOKEN_VALIDATION_RESPONSES = {
    200: OpenApiResponse(
        {
            "type": "object",
            "properties": {"is_valid": {"type": "boolean", "description": "Whether the token is valid."}},
            "required": ["is_valid"],
        },
        description="Whether the token is valid. A token that cannot be read is answered as not valid.",
    ),
    400: OpenApiResponse(REFUSAL, description="The body could not be parsed."),
    401: OpenApiResponse(REFUSAL, description="The credentials sent could not be read."),
    403: OpenApiResponse(
        REFUSAL,
        description=(
            "A caller signed in with a session sent no valid CSRF token, or the JWT sent was issued to a "
            "restricted application."
        ),
    ),
    406: NOT_ACCEPTABLE,
    415: UNREADABLE_BODY,
    429: OpenApiResponse(REFUSAL, description="Too many tokens were checked; `detail` says when to try again."),
}

REGISTRATION_VALIDATION_REQUEST = read_in_every_form({
    "type": "object",
    "description": "The values to check, each under the name of the field it would be registered in.",
    "properties": {
        "name": _TEXT,
        "username": _TEXT,
        "email": _TEXT,
        "confirm_email": _TEXT,
        "password": _TEXT,
        "country": _TEXT,
        "form_field_key": {
            "type": "string",
            "description": "Name of the one field to check, leaving the others sent unchecked.",
        },
        "reset_password_page": {
            "type": "string",
            "description": "`true` where the password is checked for a password reset rather than a registration.",
        },
    },
})

REGISTRATION_VALIDATION_RESPONSES = {
    200: OpenApiResponse(
        {
            "type": "object",
            "properties": {
                "validation_decisions": {
                    "type": "object",
                    "additionalProperties": _TEXT,
                    "description": (
                        "The decision on each field checked: an empty string where the value is acceptable, "
                        "otherwise the message to show."
                    ),
                },
                "username_suggestions": {
                    "type": "array",
                    "items": _TEXT,
                    "description": "Usernames still free to register, offered for a taken username or a name.",
                },
            },
            "required": ["validation_decisions"],
        },
        description="The decision on each value sent.",
    ),
    400: OpenApiResponse(REFUSAL, description="The body could not be parsed."),
    403: OpenApiResponse(
        REFUSAL, description="Too many checks were sent from the address the request came from.",
    ),
    406: NOT_ACCEPTABLE,
    415: UNREADABLE_BODY,
}

LOGIN_FORM_RESPONSES = {
    200: OpenApiResponse(FORM_DESCRIPTION, description="The form the site's sign-in page is built from."),
    406: NOT_ACCEPTABLE,
}

LOGIN_REQUEST = read_as_a_form({
    "type": "object",
    "properties": {
        "email_or_username": {
            "type": "string",
            "description": "The account to sign in as, by its email address or its username.",
        },
        "password": {"type": "string", "description": "Password of the account.", "format": "password"},
    },
    "required": ["email_or_username", "password"],
})

LOGIN_REFUSAL = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean", "description": "Always false."},
        "value": {"type": "string", "description": "Message to show."},
        "error_code": {"type": "string", "description": "What the refusal was, where it has a name."},
        "email": {"type": "string", "nullable": True, "description": "The account the request named, if any."},
        "context": {"type": "object", "description": "Details of the refusal for the message, by name."},
        "redirect": {"type": "string", "description": "Where to send the person instead."},
        "redirect_url": {
            "type": "string",
            "nullable": True,
            "description": "Where to send the person instead, such as to change a password.",
        },
    },
    "required": ["success"],
}

LOGIN_RESPONSES = {
    200: OpenApiResponse(
        {
            "type": "object",
            "properties": {
                "success": {"type": "boolean", "description": "Always true."},
                "redirect_url": {
                    "type": "string",
                    "nullable": True,
                    "description": "Where to send the person next, or null to leave that to the client.",
                },
            },
            "required": ["success", "redirect_url"],
        },
        description="The caller is signed in.",
    ),
    400: OpenApiResponse(
        or_unparsed(LOGIN_REFUSAL),
        description=(
            "Signing in failed: the account or the password is wrong, the account is not active, a field is "
            "missing, a step the site adds to signing in refused it, or signing in is refused for a while after "
            "too many failed attempts. A body that cannot be parsed in the media type it is sent as is refused "
            "with `detail` alone."
        ),
    ),
    403: OpenApiResponse(
        LOGIN_REFUSAL,
        description=(
            "The caller signed in through another provider with an account that is not linked to one here. "
            "A request carrying no valid CSRF token, or a password sent where the site requires signing in "
            "through another provider, is refused with an HTML page instead of this body."
        ),
    ),
    406: NOT_ACCEPTABLE,
    415: UNREADABLE_BODY,
}
