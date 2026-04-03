"""
Utilities for django models.
"""

from typing import Any, Dict, Tuple

from django.conf import settings
from django.dispatch import Signal
from django_countries.fields import Country
from eventtracking import tracker

# The setting name used for events when "settings" (account settings, preferences, profile information) change.
USER_SETTINGS_CHANGED_EVENT_NAME = 'edx.user.settings.changed'
# Used to signal a field value change
# providing_args=["user", "table", "changed_values"]
USER_FIELDS_CHANGED = Signal()


def get_changed_fields_dict(instance, model_class):
    """
    Helper method for tracking field changes on a model.

    Given a model instance and class, return a dict whose keys are that
    instance's fields which differ from the last saved ones and whose values
    are the old values of those fields.  Related fields are not considered.

    Args:
        instance (Model instance): the model instance with changes that are
            being tracked
        model_class (Model class): the class of the model instance we are
            tracking

    Returns:
        dict: a mapping of field names to current database values of those
            fields, or an empty dict if the model is new
    """
    try:
        old_model = model_class.objects.get(pk=instance.pk)
    except model_class.DoesNotExist:
        # Object is new, so fields haven't technically changed.  We'll return
        # an empty dict as a default value.
        return {}
    else:
        # We want to compare all of the scalar fields on the model, but none of
        # the relations.
        field_names = [f.name for f in model_class._meta.get_fields() if not f.is_relation]
        changed_fields = {
            field_name: getattr(old_model, field_name) for field_name in field_names
            if getattr(old_model, field_name) != getattr(instance, field_name)
        }

        return changed_fields


def emit_field_changed_events(instance, user, db_table, excluded_fields=None, hidden_fields=None):
    """Emits a settings changed event for each field that has changed.

    Note that this function expects that a `_changed_fields` dict has been set
    as an attribute on `instance` (see `get_changed_fields_dict`.

    Args:
        instance (Model instance): the model instance that is being saved
        user (User): the user that this instance is associated with
        db_table (str): the name of the table that we're modifying
        excluded_fields (list): a list of field names for which events should
            not be emitted
        hidden_fields (list): a list of field names specifying fields whose
            values should not be included in the event (None will be used
            instead)

    Returns:
        None
    """
    def clean_field(field_name, value):
        """
        Prepare a field to be emitted in a JSON serializable format.  If
        `field_name` is a hidden field, return None.
        """
        if field_name in hidden_fields:
            return None
        # Country is not JSON serializable.  Return the country code.
        if isinstance(value, Country):
            if value.code:
                return value.code
            else:
                return None
        return value

    excluded_fields = excluded_fields or []
    hidden_fields = hidden_fields or []
    changed_fields = getattr(instance, '_changed_fields', {})
    clean_changed_fields = {}
    for field_name in changed_fields:
        if field_name not in excluded_fields:
            old_value = clean_field(field_name, changed_fields[field_name])
            new_value = clean_field(field_name, getattr(instance, field_name))
            clean_changed_fields[field_name] = (old_value, new_value)
    emit_settings_changed_event(user, db_table, clean_changed_fields)
    # Remove the now inaccurate _changed_fields attribute.
    if hasattr(instance, '_changed_fields'):
        del instance._changed_fields


def truncate_fields(old_value, new_value):
    """
    Truncates old_value and new_value for analytics event emission if necessary.

    Args:
        old_value(obj): the value before the change
        new_value(obj): the new value being saved

    Returns:
        a dictionary with the following fields:
            'old': the truncated old value
            'new': the truncated new value
            'truncated': the list of fields that have been truncated
    """
    # Compute the maximum value length so that two copies can fit into the maximum event size
    # in addition to all the other fields recorded.
    max_value_length = settings.TRACK_MAX_EVENT // 4

    serialized_old_value, old_was_truncated = _get_truncated_setting_value(old_value, max_length=max_value_length)
    serialized_new_value, new_was_truncated = _get_truncated_setting_value(new_value, max_length=max_value_length)
    truncated_values = []
    if old_was_truncated:
        truncated_values.append("old")
    if new_was_truncated:
        truncated_values.append("new")

    return {'old': serialized_old_value, 'new': serialized_new_value, 'truncated': truncated_values}


def emit_settings_changed_event(user, db_table, changed_fields: Dict[str, Tuple[Any, Any]]):
    """Emits an event for a change in a setting.

    Args:
        user (User): the user that this setting is associated with.
        db_table (str): the name of the table that we're modifying.
        changed_fields: all changed settings, with both their old and new values

    Returns:
        None
    """
    for (setting_name, (old_value, new_value)) in changed_fields.items():
        truncated_fields = truncate_fields(old_value, new_value)

        truncated_fields['setting'] = setting_name
        truncated_fields['user_id'] = user.id
        truncated_fields['table'] = db_table

        tracker.emit(
            USER_SETTINGS_CHANGED_EVENT_NAME,
            truncated_fields
        )

    # Announce field change
    USER_FIELDS_CHANGED.send(sender=None, user=user, table=db_table, changed_fields=changed_fields)


def _get_truncated_setting_value(value, max_length=None):
    """
    Returns the truncated form of a setting value.

    Returns:
        truncated_value (object): the possibly truncated version of the value.
        was_truncated (bool): returns true if the serialized value was truncated.
    """
    if isinstance(value, str) and max_length is not None and len(value) > max_length:
        return value[0:max_length], True
    else:
        return value, False
