"""Schema helpers for version 2 of the User API."""

from drf_spectacular.serializers import PolymorphicProxySerializerExtension
from drf_spectacular.utils import PolymorphicProxySerializer


class ResponseShapes(PolymorphicProxySerializer):
    """
    The set of bodies one operation returns for the same status code.

    An endpoint whose caller can reduce the response answers with the default
    object, with a preset, or with a selection of properties. A reduced body
    satisfies more than one of those descriptions, so they are published as
    alternatives a body may match in any combination.
    """

    def __init__(self, component_name, serializers, **kwargs):
        # The generator re-instantiates a declaration from the arguments it
        # retained, so the discriminator is only supplied when it is missing.
        kwargs.setdefault("resource_type_field_name", None)
        super().__init__(component_name=component_name, serializers=serializers, **kwargs)


class ResponseShapesExtension(PolymorphicProxySerializerExtension):
    """Publishes the alternatives of a ``ResponseShapes`` declaration."""

    target_class = ResponseShapes

    def map_serializer(self, auto_schema, direction):
        """Return the alternatives as a set a body may match more than one of."""
        return {"anyOf": super().map_serializer(auto_schema, direction)["oneOf"]}
