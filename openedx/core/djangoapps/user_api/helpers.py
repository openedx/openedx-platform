"""
Helper functions for the account/profile Python APIs.
This is NOT part of the public API.
"""


import json
import logging
import traceback
from collections import defaultdict
from functools import wraps

from django import forms
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.encoding import force_str
from django.utils.functional import Promise

LOGGER = logging.getLogger(__name__)


def intercept_errors(api_error, ignore_errors=None):
    """
    Function decorator that intercepts exceptions
    and translates them into API-specific errors (usually an "internal" error).

    This allows callers to gracefully handle unexpected errors from the API.

    This method will also log all errors and function arguments to make
    it easier to track down unexpected errors.

    Arguments:
        api_error (Exception): The exception to raise if an unexpected error is encountered.

    Keyword Arguments:
        ignore_errors (iterable): List of errors to ignore.  By default, intercept every error.

    Returns:
        function

    """
    def _decorator(func):
        """
        Function decorator that intercepts exceptions and translates them into API-specific errors.
        """
        @wraps(func)
        def _wrapped(*args, **kwargs):
            """
            Wrapper that evaluates a function, intercepting exceptions and translating them into
            API-specific errors.
            """
            try:
                return func(*args, **kwargs)
            except Exception as ex:
                # Raise and log the original exception if it's in our list of "ignored" errors
                for ignored in ignore_errors or []:
                    if isinstance(ex, ignored):
                        msg = (
                            "A handled error occurred when calling '{func_name}' "
                            "with arguments '{args}' and keyword arguments '{kwargs}': "
                            "{exception}"
                        ).format(
                            func_name=func.__name__,
                            args=args,
                            kwargs=kwargs,
                            exception=ex.developer_message if hasattr(ex, 'developer_message') else repr(ex)  # pylint: disable=no-member
                        )
                        LOGGER.warning(msg)
                        raise

                caller = traceback.format_stack(limit=2)[0]

                # Otherwise, log the error and raise the API-specific error
                msg = (
                    "An unexpected error occurred when calling '{func_name}' "
                    "with arguments '{args}' and keyword arguments '{kwargs}' from {caller}: "
                    "{exception}"
                ).format(
                    func_name=func.__name__,
                    args=args,
                    kwargs=kwargs,
                    exception=ex.developer_message if hasattr(ex, 'developer_message') else repr(ex),  # pylint: disable=no-member
                    caller=caller.strip(),
                )
                LOGGER.exception(msg)
                raise api_error(msg)  # pylint: disable=raise-missing-from  # noqa: B904
        return _wrapped
    return _decorator


class InvalidFieldError(Exception):
    """The provided field definition is not valid. """


class FormDescription:
    """Generate a JSON representation of a form. """

    ALLOWED_TYPES = ["text", "email", "select", "textarea", "checkbox", "plaintext", "password", "hidden"]

    ALLOWED_RESTRICTIONS = {
        "text": ["min_length", "max_length"],
        "password": ["min_length", "max_length", "min_upper", "min_lower",
                     "min_punctuation", "min_symbol", "min_numeric", "min_alphabetic"],
        "email": ["min_length", "max_length", "readonly"],
    }

    FIELD_TYPE_MAP = {
        forms.CharField: "text",
        forms.PasswordInput: "password",
        forms.ChoiceField: "select",
        forms.TypedChoiceField: "select",
        forms.Textarea: "textarea",
        forms.BooleanField: "checkbox",
        forms.EmailField: "email",
    }

    OVERRIDE_FIELD_PROPERTIES = [
        "label", "type", "defaultValue", "placeholder",
        "instructions", "required", "restrictions",
        "options", "supplementalLink", "supplementalText"
    ]

    def __init__(self, method, submit_url):
        """Configure how the form should be submitted.

        Args:
            method (unicode): The HTTP method used to submit the form.
            submit_url (unicode): The URL where the form should be submitted.

        """
        self.method = method
        self.submit_url = submit_url
        self.fields = []
        self._field_overrides = defaultdict(dict)

    def add_field(
            self, name, label="", field_type="text", default="",
            placeholder="", instructions="", exposed=None, required=True, restrictions=None,
            options=None, include_default_option=False, error_messages=None,
            supplementalLink="", supplementalText=""
    ):
        """Add a field to the form description.

        Args:
            name (unicode): The name of the field, which is the key for the value
                to send back to the server.

        Keyword Arguments:
            label (unicode): The label for the field (e.g. "E-mail" or "Username")

            field_type (unicode): The type of the field.  See `ALLOWED_TYPES` for
                acceptable values.

            default (unicode): The default value for the field.

            placeholder (unicode): Placeholder text in the field
                (e.g. "user@example.com" for an email field)

            instructions (unicode): Short instructions for using the field
                (e.g. "This is the email address you used when you registered.")

            exposed (boolean): Whether the field is shown if not required.
                If the field is not set, the field will be visible if it's required.

            required (boolean): Whether the field is required or optional.

            restrictions (dict): Validation restrictions for the field.
                See `ALLOWED_RESTRICTIONS` for acceptable values.

            options (list): For "select" fields, a list of tuples
                (value, display_name) representing the options available to
                the user.  `value` is the value of the field to send to the server,
                and `display_name` is the name to display to the user.
                If the field type is "select", you *must* provide this kwarg.

            include_default_option (boolean): If True, include a "default" empty option
                at the beginning of the options list.

            error_messages (dict): Custom validation error messages.
                Currently, the only supported key is "required" indicating
                that the messages should be displayed if the user does
                not provide a value for a required field.

            supplementalLink (unicode): A qualified URL to provide supplemental information
                for the form field. An example may be a link to documentation for creating
                strong passwords.

            supplementalText (unicode): The visible text for the supplemental link above.

        Raises:
            InvalidFieldError

        """
        if field_type not in self.ALLOWED_TYPES:
            msg = "Field type '{field_type}' is not a valid type.  Allowed types are: {allowed}.".format(
                field_type=field_type,
                allowed=", ".join(self.ALLOWED_TYPES)
            )
            raise InvalidFieldError(msg)

        if exposed is None:
            exposed = required

        field_dict = {
            "name": name,
            "label": label,
            "type": field_type,
            "defaultValue": default,
            "placeholder": placeholder,
            "instructions": instructions,
            "exposed": exposed,
            "required": required,
            "restrictions": {},
            "errorMessages": {},
            "supplementalLink": supplementalLink,
            "supplementalText": supplementalText,
            "loginIssueSupportLink": settings.LOGIN_ISSUE_SUPPORT_LINK,
        }

        field_override = self._field_overrides.get(name, {})

        if field_type == "select":
            if options is not None:
                field_dict["options"] = []

                # Get an existing default value from the field override
                existing_default_value = field_override.get('defaultValue')

                # Include an empty "default" option at the beginning of the list;
                # preselect it if there isn't an overriding default.
                if include_default_option:
                    field_dict["options"].append({
                        "value": "",
                        "name": "--",
                        "default": existing_default_value is None
                    })
                field_dict["options"].extend([
                    {
                        'value': option_value,
                        'name': option_name,
                        'default': option_value == existing_default_value
                    } for option_value, option_name in options
                ])
            else:
                raise InvalidFieldError("You must provide options for a select field.")

        if restrictions is not None:
            allowed_restrictions = self.ALLOWED_RESTRICTIONS.get(field_type, [])
            for key, val in restrictions.items():
                if key in allowed_restrictions:
                    field_dict["restrictions"][key] = val
                else:
                    msg = f"Restriction '{key}' is not allowed for field type '{field_type}'"
                    raise InvalidFieldError(msg)

        if error_messages is not None:
            field_dict["errorMessages"] = error_messages

        # If there are overrides for this field, apply them now.
        # Any field property can be overwritten (for example, the default value or placeholder)
        field_dict.update(field_override)

        self.fields.append(field_dict)

    def to_json(self):
        """Create a JSON representation of the form description.

        Here's an example of the output:
        {
            "method": "post",
            "submit_url": "/submit",
            "fields": [
                {
                    "name": "cheese_or_wine",
                    "label": "Cheese or Wine?",
                    "defaultValue": "cheese",
                    "type": "select",
                    "exposed": True,
                    "required": True,
                    "placeholder": "",
                    "instructions": "",
                    "options": [
                        {"value": "cheese", "name": "Cheese", "default": False},
                        {"value": "wine", "name": "Wine", "default": False}
                    ]
                    "restrictions": {},
                    "errorMessages": {},
                },
                {
                    "name": "comments",
                    "label": "comments",
                    "defaultValue": "",
                    "type": "text",
                    "exposed": False,
                    "required": False,
                    "placeholder": "Any comments?",
                    "instructions": "Please enter additional comments here."
                    "restrictions": {
                        "max_length": 200
                    }
                    "errorMessages": {},
                },
                ...
            ]
        }

        If the field is NOT a "select" type, then the "options"
        key will be omitted.

        Returns:
            unicode
        """
        return json.dumps({
            "method": self.method,
            "submit_url": self.submit_url,
            "fields": self.fields
        }, cls=LocalizedJSONEncoder)

    def override_field_properties(self, field_name, **kwargs):
        """Override properties of a field.

        The overridden values take precedence over the values provided
        to `add_field()`.

        Field properties not in `OVERRIDE_FIELD_PROPERTIES` will be ignored.

        Arguments:
            field_name (str): The name of the field to override.

        Keyword Args:
            Same as to `add_field()`.

        """
        # Transform kwarg "field_type" to "type" (a reserved Python keyword)
        if "field_type" in kwargs:
            kwargs["type"] = kwargs["field_type"]

        # Transform kwarg "default" to "defaultValue", since "default"
        # is a reserved word in JavaScript
        if "default" in kwargs:
            kwargs["defaultValue"] = kwargs["default"]

        self._field_overrides[field_name].update({
            property_name: property_value
            for property_name, property_value in kwargs.items()
            if property_name in self.OVERRIDE_FIELD_PROPERTIES
        })


class LocalizedJSONEncoder(DjangoJSONEncoder):
    """
    JSON handler that evaluates gettext_lazy promises.
    """
    # pylint: disable=method-hidden

    def default(self, obj):  # pylint: disable=arguments-differ
        """
        Forces evaluation of gettext_lazy promises.
        """
        if isinstance(obj, Promise):
            return force_str(obj)
        super().default(obj)


def serializer_is_dirty(preference_serializer):
    """
    Return True if saving the supplied (Raw)UserPreferenceSerializer would change the database.
    """
    return (
        preference_serializer.instance is None or
        preference_serializer.instance.value != preference_serializer.validated_data['value']
    )
