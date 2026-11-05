"""Components of this API that a document carries although nothing in it refers to them."""

# No imports: a settings module reads this while settings are still being built.

# The model the v1 retirement-status PATCH was published as reading, kept by
# name so generated clients keep the class once the operation no longer refers to it.
PATCHED_USER_RETIREMENT_STATUS = {
    'type': 'object',
    'description': 'Perform serialization for the RetirementStatus model',
    'properties': {
        'id': {'type': 'integer', 'readOnly': True},
        'user': {'allOf': [{'$ref': '#/components/schemas/RetirementUser'}], 'readOnly': True},
        'current_state': {'allOf': [{'$ref': '#/components/schemas/RetirementState'}], 'readOnly': True},
        'last_state': {'allOf': [{'$ref': '#/components/schemas/RetirementState'}], 'readOnly': True},
        'created': {'type': 'string', 'format': 'date-time', 'readOnly': True},
        'modified': {'type': 'string', 'format': 'date-time', 'readOnly': True},
        'original_username': {'type': 'string', 'maxLength': 150},
        'original_email': {'type': 'string', 'format': 'email', 'maxLength': 254},
        'original_name': {'type': 'string', 'maxLength': 255},
        'retired_username': {'type': 'string', 'maxLength': 150},
        'retired_email': {'type': 'string', 'format': 'email', 'maxLength': 254},
    },
}

#: Keyed by the name each component is published under, for a service to append.
KEPT_COMPONENT_SCHEMAS = {
    'PatchedUserRetirementStatus': PATCHED_USER_RETIREMENT_STATUS,
}
