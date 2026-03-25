"""
Things commonly needed in Enterprise tests.
"""


from django.conf import settings  # noqa: I001

FEATURES_WITH_ENTERPRISE_ENABLED = settings.FEATURES.copy()
FEATURES_WITH_ENTERPRISE_ENABLED['ENABLE_ENTERPRISE_INTEGRATION'] = True

FAKE_ENTERPRISE_CUSTOMER = {
    'active': True,
    'branding_configuration': None,
    'catalog': None,
    'enable_audit_enrollment': False,
    'enable_data_sharing_consent': False,
    'enforce_data_sharing_consent': 'at_enrollment',
    'enterprise_customer_entitlements': [],
    'identity_provider': None,
    'name': 'EnterpriseCustomer',
    'replace_sensitive_sso_username': True,
    'site': {'domain': 'example.com', 'name': 'example.com'},
    'uuid': '1cbf230f-f514-4a05-845e-d57b8e29851c'
}
