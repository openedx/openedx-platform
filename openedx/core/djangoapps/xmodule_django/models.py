"""
Useful django models for implementing XBlock infrastructure in django.
"""


import logging  # noqa: I001
import warnings

import opaque_keys.edx.django.models
from django.db import models

from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)


class NoneToEmptyManager(models.Manager):
    """
    A :class:`django.db.models.Manager` that has a :class:`NoneToEmptyQuerySet`
    as its `QuerySet`, initialized with a set of specified `field_names`.
    """
    def get_queryset(self):
        """
        Returns the result of NoneToEmptyQuerySet instead of a regular QuerySet.
        """
        return NoneToEmptyQuerySet(self.model, using=self._db)


class NoneToEmptyQuerySet(models.query.QuerySet):
    """
    A :class:`django.db.query.QuerySet` that replaces `None` values passed to `filter` and `exclude`
    with the corresponding `Empty` value for all fields with an `Empty` attribute.

    This is to work around Django automatically converting `exact` queries for `None` into
    `isnull` queries before the field has a chance to convert them to queries for it's own
    empty value.
    """
    def _filter_or_exclude(self, *args, **kwargs):
        for field_object in self.model._meta.get_fields():
            direct = not field_object.auto_created or field_object.concrete
            if direct and hasattr(field_object, 'Empty'):
                for suffix in ('', '_exact'):
                    key = f'{field_object.name}{suffix}'
                    if key in kwargs and kwargs[key] is None:
                        kwargs[key] = field_object.Empty

        return super()._filter_or_exclude(*args, **kwargs)


class OpaqueKeyField(opaque_keys.edx.django.models.OpaqueKeyField):
    """
    A django field for storing OpaqueKeys.

    The baseclass will return the value from the database as a string, rather than an instance
    of an OpaqueKey, leaving the application to determine which key subtype to parse the string
    as.

    Subclasses must specify a KEY_CLASS attribute, in which case the field will use :meth:`from_string`
    to parse the key string, and will return an instance of KEY_CLASS.
    """
    def __init__(self, *args, **kwargs):
        warnings.warn("openedx.core.djangoapps.xmodule_django.models.OpaqueKeyField is deprecated. "
                      "Please use opaque_keys.edx.django.models.OpaqueKeyField instead.", stacklevel=2)
        super().__init__(*args, **kwargs)


class CourseKeyField(opaque_keys.edx.django.models.CourseKeyField):
    """
    A django Field that stores a CourseKey object as a string.
    """
    def __init__(self, *args, **kwargs):
        warnings.warn("openedx.core.djangoapps.xmodule_django.models.LocationKeyField is deprecated. "
                      "Please use opaque_keys.edx.django.models.UsageKeyField instead.", stacklevel=2)
        super().__init__(*args, **kwargs)


class UsageKeyField(opaque_keys.edx.django.models.UsageKeyField):
    """
    A django Field that stores a UsageKey object as a string.
    """
    def __init__(self, *args, **kwargs):
        warnings.warn("openedx.core.djangoapps.xmodule_django.models.UsageKeyField is deprecated. "
                      "Please use opaque_keys.edx.django.models.UsageKeyField instead.", stacklevel=2)
        super().__init__(*args, **kwargs)


class UsageKeyWithRunField(opaque_keys.edx.django.models.UsageKeyField):
    """
    Subclass of UsageKeyField that automatically fills in
    missing `run` values, for old Mongo courses.
    """
    def to_python(self, value):
        value = super().to_python(value)
        if value is not None and value.run is None:
            value = value.replace(course_key=modulestore().fill_in_run(value.course_key))
        return value


class BlockTypeKeyField(opaque_keys.edx.django.models.BlockTypeKeyField):
    """
    A django Field that stores a BlockTypeKey object as a string.
    """
    def __init__(self, *args, **kwargs):
        warnings.warn("openedx.core.djangoapps.xmodule_django.models.BlockTypeKeyField is deprecated. "
                      "Please use opaque_keys.edx.django.models.BlockTypeKeyField instead.", stacklevel=2)
        super().__init__(*args, **kwargs)
