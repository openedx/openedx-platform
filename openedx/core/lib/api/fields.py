"""Fields useful for edX API implementations."""

from rest_framework.serializers import Field, URLField


class ExpandableField(Field):  # pylint: disable=abstract-method
    """Field that can dynamically use a more detailed serializer based on a user-provided "expand" parameter.

    Kwargs:
      collapsed_serializer (Serializer): the serializer to use for a non-expanded representation.
      expanded_serializer (Serializer): the serializer to use for an expanded representation.
    """

    def __init__(self, **kwargs):
        """Sets up the ExpandableField with the collapsed and expanded versions of the serializer."""
        assert 'collapsed_serializer' in kwargs and 'expanded_serializer' in kwargs  # noqa: PT018
        self.collapsed = kwargs.pop('collapsed_serializer')
        self.expanded = kwargs.pop('expanded_serializer')
        super().__init__(**kwargs)

    def to_representation(self, obj):  # pylint: disable=arguments-differ
        """
        Return a representation of the field that is either expanded or collapsed.
        """
        should_expand = self.field_name in self.context.get("expand", [])
        field = self.expanded if should_expand else self.collapsed

        # Avoid double-binding the field, otherwise we'll get
        # an error about the source kwarg being redundant.
        if field.source is None:
            field.bind(self.field_name, self)

            if should_expand:
                self.expanded.context["expand"] = set(field.context.get("expand", []))

        return field.to_representation(obj)


class AbsoluteURLField(URLField):
    """
    Field that serializes values to absolute URLs based on the current request.

    If the value to be serialized is already a URL, that value will returned.
    """

    def to_representation(self, value):
        request = self.context.get('request', None)

        assert request is not None, (
            "`%s` requires the request in the serializer  context. "  # noqa: UP031
            "Add `context={'request': request}` when instantiating the serializer." % self.__class__.__name__
        )

        if value.startswith(('http:', 'https:')):
            return value

        return request.build_absolute_uri(value)
