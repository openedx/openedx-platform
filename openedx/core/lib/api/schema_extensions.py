"""
drf-spectacular extensions for serializers it cannot introspect on its own.

``BaseSerializer`` subclasses have no ``fields``, so drf-spectacular raises
``AttributeError`` when it tries to walk them. Each extension below declares
the type its serializer actually produces.

The extensions self-register on import. ``CommonInitializationConfig.ready()``
(``openedx.core.djangoapps.common_initialization.apps``) imports this module,
so they are registered at startup in both the LMS and the CMS.
"""

from drf_spectacular.extensions import OpenApiSerializerExtension
from drf_spectacular.plumbing import build_basic_type
from drf_spectacular.types import OpenApiTypes


class _StringSerializerExtension(OpenApiSerializerExtension):
    """Base for serializers whose ``to_representation`` returns ``str``."""

    def map_serializer(self, auto_schema, direction):
        return build_basic_type(OpenApiTypes.STR)


class _ObjectSerializerExtension(OpenApiSerializerExtension):
    """Base for serializers whose ``to_representation`` returns a dict."""

    def map_serializer(self, auto_schema, direction):
        return build_basic_type(OpenApiTypes.OBJECT)


class CourseKeySerializerExtension(_StringSerializerExtension):
    """``CourseKeySerializer`` serializes a CourseKey to its string form."""

    target_class = 'lms.djangoapps.course_api.serializers.CourseKeySerializer'


class PhoneNumberSerializerExtension(_StringSerializerExtension):
    """``PhoneNumberSerializer`` serializes a phone number to a digit string."""

    target_class = 'openedx.core.djangoapps.user_api.accounts.serializers.PhoneNumberSerializer'


class UsageKeyV2SerializerExtension(_StringSerializerExtension):
    """``UsageKeyV2Serializer`` serializes a LibraryUsageLocatorV2 to a string."""

    target_class = 'openedx.core.djangoapps.content_libraries.rest_api.serializers.UsageKeyV2Serializer'


class OpaqueKeySerializerExtension(_StringSerializerExtension):
    """``OpaqueKeySerializer`` serializes an OpaqueKey to a string."""

    target_class = 'openedx.core.djangoapps.content_libraries.rest_api.serializers.OpaqueKeySerializer'


class LegacySettingsSerializerExtension(_ObjectSerializerExtension):
    """``LegacySettingsSerializer`` serializes legacy discussion settings to a dict."""

    target_class = 'openedx.core.djangoapps.discussions.serializers.LegacySettingsSerializer'


class UserCourseOutlineDataSerializerExtension(_ObjectSerializerExtension):
    """``UserCourseOutlineDataSerializer`` serializes a course outline to a dict."""

    target_class = (
        'openedx.core.djangoapps.content.learning_sequences.views.'
        'CourseOutlineView.UserCourseOutlineDataSerializer'
    )
