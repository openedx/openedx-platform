"""Exceptions for the embargo app."""


class InvalidAccessPoint(Exception):
    """The requested access point is not supported. """

    def __init__(self, access_point, *args, **kwargs):
        msg = (  # noqa: UP032
            "Access point '{access_point}' should be either 'enrollment' or 'courseware'"
        ).format(access_point=access_point)
        super().__init__(msg, *args, **kwargs)
