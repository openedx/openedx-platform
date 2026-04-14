"""
Custom field types for mongoengine
"""


import mongoengine
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locations import Location


class CourseKeyField(mongoengine.StringField):
    """
    Serializes and deserializes CourseKey's to mongo dbs which use mongoengine
    """
    def __init__(self, **kwargs):
        # it'd be useful to add init args such as support_deprecated, force_deprecated
        super().__init__(**kwargs)

    def to_mongo(self, course_key):  # lint-amnesty, pylint: disable=arguments-differ
        """
        For now saves the course key in the deprecated form
        """
        assert isinstance(course_key, (type(None), CourseKey))
        if course_key:
            # don't call super as base.BaseField.to_mongo calls to_python() for some odd reason
            return str(course_key)
        else:
            return None

    def to_python(self, course_key):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Deserialize to a CourseKey instance
        """
        # calling super b/c it decodes utf (and doesn't have circularity of from_python)
        course_key = super().to_python(course_key)
        assert isinstance(course_key, (type(None), (str,), CourseKey))
        if course_key == '':
            return None
        if isinstance(course_key, str):
            return CourseKey.from_string(course_key)
        else:
            return course_key

    def validate(self, value):
        assert isinstance(value, (type(None), str, CourseKey))
        if isinstance(value, CourseKey):
            return super().validate(str(value))
        else:
            return super().validate(value)

    def prepare_query_value(self, _opt, value):
        return self.to_mongo(value)


class UsageKeyField(mongoengine.StringField):
    """
    Represent a UsageKey as a single string in Mongo
    """
    def to_mongo(self, location):  # lint-amnesty, pylint: disable=arguments-differ
        """
        For now saves the usage key in the deprecated location i4x/c4x form
        """
        assert isinstance(location, (type(None), UsageKey))
        if location is None:
            return None
        return super().to_mongo(str(location))

    def to_python(self, location):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Deserialize to a UsageKey instance: for now it's a location missing the run
        """
        assert isinstance(location, (type(None), str, UsageKey))
        if location == '':
            return None
        if isinstance(location, str):
            location = super().to_python(location)
            return Location.from_string(location)
        else:
            return location

    def validate(self, value):
        assert isinstance(value, (type(None), str, UsageKey))
        if isinstance(value, UsageKey):
            return super().validate(str(value))
        else:
            return super().validate(value)

    def prepare_query_value(self, _opt, value):
        return self.to_mongo(value)
