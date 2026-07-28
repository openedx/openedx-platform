""" User Authn related Exceptions. """


from openedx.core.djangolib.markup import Text


class AuthFailedError(Exception):
    """
    This is a helper for the login view, allowing the various sub-methods to error out with an appropriate failure
    message.
    """
    def __init__(  # pylint: disable=dangerous-default-value
        self, value=None, redirect=None, redirect_url=None, error_code=None, context={},  # noqa: B006
    ):
        super().__init__()
        self.value = Text(value)
        self.redirect = redirect
        self.redirect_url = redirect_url
        self.error_code = error_code
        self.context = context

    def get_response(self):
        """ Returns a dict representation of the error. """
        resp = {'success': False}
        for attr in ('value', 'redirect', 'redirect_url', 'error_code', 'context'):
            if getattr(self, attr):
                resp[attr] = getattr(self, attr)

        return resp


class VulnerablePasswordError(Exception):
    """
    This is a helper for the login view, allowing the view to error out if password
    is vulnerable.
    """
    def __init__(self, value, error_code, redirect_url=None):
        super().__init__()
        self.value = value
        self.error_code = error_code
        self.redirect_url = redirect_url

    def get_response(self):
        return {
            'value': self.value,
            'error_code': self.error_code,
            'redirect_url': self.redirect_url,
            'success': False
        }
