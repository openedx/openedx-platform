"""
Settings for Pact Verification Tests.
"""

from .test import *  # pylint: disable=wildcard-import, unused-wildcard-import  # noqa: F403

#### Allow Pact Provider States URL ####
PROVIDER_STATES_URL = True

#### Default User name for Pact Requests Authentication #####
MOCK_USERNAME = 'Mock User'

######################### Add Authentication Middleware for Pact Verification Calls #########################
MIDDLEWARE = MIDDLEWARE + ['common.test.pacts.middleware.AuthenticationMiddleware', ]  # noqa: F405
