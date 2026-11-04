"""Components of this API that a document carries although nothing in it refers to them."""

# Nothing is imported in this module. A settings module reads it while the
# settings are still being assembled, which is too early for any module that
# reads a setting of its own.

# The retirement-status model that a partial update of a version 1 retirement
# was published as reading. Where that operation publishes the object its
# handler really reads, the model has no reference left to generate it from,
# and a client generated from a document names a class after each model in it.
# The model is therefore named here, exactly as it was published, for such a
# service to append, so the class keeps its name in every document it builds.
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
