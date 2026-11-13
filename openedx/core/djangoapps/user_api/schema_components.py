"""Parts of this API's documents that are written out rather than generated."""

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


# How a caller sends each credential this API takes beside the session cookie.
# The generator describes the cookie by itself, from the class that reads it,
# and has nothing to say about the other two, so they are written out here and
# appended by the service. A client is generated from a document, and it offers
# the caller a way to send a credential only where the document describes one,
# so an operation taking a token and publishing no scheme for it leaves the
# callers holding that token with no generated client they can use.
CREDENTIAL_SCHEMES = {
    'jwt': {
        'type': 'apiKey',
        'name': 'Authorization',
        'in': 'header',
        'description': 'A token this platform signed, sent as `JWT <token>`.',
    },
    'bearer': {
        'type': 'http',
        'scheme': 'bearer',
        'description': (
            "An access token obtained from this platform's authorization endpoint, sent as "
            '`Bearer <token>`.'
        ),
    },
}
