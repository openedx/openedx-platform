"""Mock data for SAMLIdentityProvider"""


from social_core.backends.saml import OID_COMMON_NAME, OID_GIVEN_NAME, OID_MAIL, OID_SURNAME, OID_USERID

expected_user_details = {
    'username': 'myself',
    'fullname': 'Me Myself And I',
    'last_name': None,
    'first_name': 'Me Myself',
    'email': 'myself@testshib.org'
}

mock_attributes = {
    OID_USERID: ['myself'],
    OID_COMMON_NAME: ['Me Myself And I'],
    OID_SURNAME: [],                        # Assume user has not provided Last Name
    OID_GIVEN_NAME: ['Me Myself'],
    OID_MAIL: ['myself@testshib.org']
}

mock_conf = {
    'attr_defaults': {}
}
