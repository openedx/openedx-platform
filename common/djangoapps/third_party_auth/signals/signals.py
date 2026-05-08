"""
Signals defined by the third_party_auth app.
"""
from django.dispatch import Signal

# Signal fired when a user disconnects a SAML account.
#
# Keyword arguments sent with this signal:
#   request (HttpRequest): The HTTP request during which the disconnect occurred.
#   user (User): The Django User disconnecting the social auth account.
#   saml_backend (social_core.backends.saml.SAMLAuth): The SAMLAuth backend instance (has a ``name`` attribute).
SAMLAccountDisconnected = Signal()
