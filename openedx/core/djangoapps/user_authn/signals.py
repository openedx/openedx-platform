"""
Signals for user_authn
"""

from typing import Any, Dict, Optional, Tuple  # noqa: UP035

from common.djangoapps.student.models import UserProfile
from common.djangoapps.track import segment


def user_fields_changed(
    user=None,
    table=None,
    changed_fields: Optional[Dict[str, Tuple[Any, Any]]] = None,  # noqa: UP006, UP045
    **_kwargs,
):
    """
    Update a collection of user profile fields in segment when they change in the database

    Args:
        user: The user object for the user being changed
        table: The name of the table being updated
        changed_fields: A mapping from changed field name to old and new values.
    """

    fields = {field: new_value for (field, (old_value, new_value)) in changed_fields.items()}
    # This mirrors the logic in ./views/register.py:_track_user_registration
    if table == 'auth_userprofile':
        if 'gender' in fields and fields['gender']:
            fields['gender'] = dict(UserProfile.GENDER_CHOICES)[fields['gender']]
        if 'country' in fields:
            fields['country'] = str(fields['country'])
        if 'level_of_education' in fields and fields['level_of_education']:
            fields['education'] = dict(UserProfile.LEVEL_OF_EDUCATION_CHOICES)[fields['level_of_education']]
        if 'year_of_birth' in fields:
            fields['yearOfBirth'] = fields.pop('year_of_birth')
        if 'mailing_address' in fields:
            fields['address'] = fields.pop('mailing_address')

    segment.identify(
        user.id,
        fields
    )
