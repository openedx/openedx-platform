"""
Slightly customized python-social-auth backend for SAML 2.0 support
"""

import logging
from copy import deepcopy

import requests
from django.contrib.sites.models import Site
from django.http import Http404
from django.utils.datastructures import MultiValueDictKeyError
from django.utils.functional import cached_property
from django_countries import countries
from onelogin.saml2.settings import OneLogin_Saml2_Settings
from social_core.backends.saml import OID_EDU_PERSON_ENTITLEMENT, SAMLAuth, SAMLIdentityProvider
from social_core.exceptions import AuthForbidden, AuthInvalidParameter, AuthMissingParameter

from common.djangoapps.third_party_auth.exceptions import IncorrectConfigurationException
from openedx.core.djangoapps.theming.helpers import get_current_request

STANDARD_SAML_PROVIDER_KEY = 'standard_saml_provider'
SAP_SUCCESSFACTORS_SAML_KEY = 'sap_success_factors'
log = logging.getLogger(__name__)


class SAMLAuthBackend(SAMLAuth):  # pylint: disable=abstract-method
    """
    Customized version of SAMLAuth that gets the list of IdPs from common.djangoapps.third_party_auth's list of
    enabled providers.
    """
    name = "tpa-saml"

    def get_idp(self, idp_name):
        """ Given the name of an IdP, get a SAMLIdentityProvider instance """
        from .models import SAMLProviderConfig
        return SAMLProviderConfig.current(idp_name).get_config(self)

    def setting(self, name, default=None):
        """ Get a setting, from SAMLConfiguration """
        try:
            return self._config.get_setting(name)
        except KeyError:
            return self.strategy.setting(name, default, backend=self)

    def get_idp_setting(self, idp, name, default=None):
        try:
            return idp.saml_sp_configuration.get_setting(name)
        except KeyError:
            return self.setting(name, default)

    def generate_saml_config(self, idp=None):
        """
        Override of SAMLAuth.generate_saml_config to use an idp's configured saml_sp_configuration if given.
        """
        if idp:
            abs_completion_url = self.redirect_uri
            config = {
                'contactPerson': {
                    'technical': self.get_idp_setting(idp, 'TECHNICAL_CONTACT'),
                    'support': self.get_idp_setting(idp, 'SUPPORT_CONTACT')
                },
                'debug': True,
                'idp': idp.saml_config_dict if idp else {},
                'organization': self.get_idp_setting(idp, 'ORG_INFO'),
                'security': {
                    'metadataValidUntil': '',
                    'metadataCacheDuration': 'P10D',  # metadata valid for ten days
                },
                'sp': {
                    'assertionConsumerService': {
                        'url': abs_completion_url,
                        # python-saml only supports HTTP-POST
                        'binding': 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST'
                    },
                    'entityId': self.get_idp_setting(idp, 'SP_ENTITY_ID'),
                    'x509cert': self.get_idp_setting(idp, 'SP_PUBLIC_CERT'),
                    'privateKey': self.get_idp_setting(idp, 'SP_PRIVATE_KEY'),
                },
                'strict': True,  # We must force strict mode - for security
            }
            config["security"].update(self.get_idp_setting(idp, "SECURITY_CONFIG", {}))
            config["sp"].update(self.get_idp_setting(idp, "SP_EXTRA", {}))
            return config
        else:
            return super().generate_saml_config()

    def auth_complete(self, *args, **kwargs):
        """
        Handle exceptions that happen during SAML authentication
        """
        try:
            return super().auth_complete(*args, **kwargs)
        # We are seeing errors of MultiValueDictKeyError looking for the parameter 'RelayState'.
        # We would like to have a more specific error to handle for observability purposes.
        except MultiValueDictKeyError as e:
            raise AuthMissingParameter(self.name, e.args[0]) from e

    def get_user_id(self, details, response):
        """
        Calling the parent function and handling the exception properly.
        """
        try:
            return super().get_user_id(details, response)
        except (KeyError, IndexError, AuthInvalidParameter) as ex:  # Add AuthInvalidParameter here
            log.warning(
                '[THIRD_PARTY_AUTH] Error in SAML authentication flow. '
                'Provider: {idp_name}, Message: {message}'.format(
                    message=str(ex),
                    idp_name=response.get('idp_name')
                )
            )
            raise IncorrectConfigurationException(self) from ex

    def generate_metadata_xml(self, idp_name=None):  # pylint: disable=arguments-differ
        """
        Override of SAMLAuth.generate_metadata_xml to accept an optional idp parameter.
        """
        idp = self.get_idp(idp_name) if idp_name else None
        config = self.generate_saml_config(idp)
        saml_settings = OneLogin_Saml2_Settings(
            config,
            sp_validation_only=True
        )
        metadata = saml_settings.get_sp_metadata()
        errors = saml_settings.validate_metadata(metadata)
        return metadata, errors

    def auth_url(self):
        """
        Check that SAML is enabled and that the request includes an 'idp'
        parameter before getting the URL to which we must redirect in order to
        authenticate the user.

        raise Http404 if SAML authentication is disabled.
        """
        if not self._config.enabled:
            log.error('[THIRD_PARTY_AUTH] SAML authentication is not enabled')
            raise Http404

        return super().auth_url()

    def disconnect(self, *args, **kwargs):
        """
        Override of SAMLAuth.disconnect to emit a signal when a user disconnects their SAML account.
        """
        from common.djangoapps.third_party_auth.signals import SAMLAccountDisconnected
        # Emit the signal before super().disconnect() so that handlers (e.g. enterprise
        # user unlinking) run while the social auth record still exists.
        user = kwargs['user']  # Upstream social_core always passes a non-None user.
        log.info(
            '[THIRD_PARTY_AUTH] Emitting SAMLAccountDisconnected signal for user_id=%s, backend=%s',
            user.id, self.name,
        )
        SAMLAccountDisconnected.send(
            sender=self.__class__,
            request=self.strategy.request,
            user=user,
            saml_backend=self,
        )
        return super().disconnect(*args, **kwargs)

    def _check_entitlements(self, idp, attributes):
        """
        Check if we require the presence of any specific eduPersonEntitlement.

        raise AuthForbidden if the user should not be authenticated, or do nothing
        to allow the login pipeline to continue.
        """
        if "requiredEntitlements" in idp.conf:
            entitlements = attributes.get(OID_EDU_PERSON_ENTITLEMENT, [])
            for expected in idp.conf['requiredEntitlements']:
                if expected not in entitlements:
                    log.warning(
                        '[THIRD_PARTY_AUTH] SAML user rejected due to missing eduPersonEntitlement. '  # noqa: UP032
                        'Provider: {provider}, Entitlement: {entitlement}'.format(
                            provider=idp.name,
                            entitlement=expected)
                    )
                    raise AuthForbidden(self)

    def _create_saml_auth(self, idp):
        """
        Get an instance of OneLogin_Saml2_Auth

        idp: The Identity Provider - a social_core.backends.saml.SAMLIdentityProvider instance
        """
        # We only override this method so that we can add extra debugging when debug_mode is True
        # Note that auth_inst is instantiated just for the current HTTP request, then is destroyed
        auth_inst = super()._create_saml_auth(idp)
        from .models import SAMLProviderConfig
        if SAMLProviderConfig.current(idp.name).debug_mode:
            def wrap_with_logging(method_name, action_description, xml_getter, request_data, next_url):
                """ Wrap the request and response handlers to add debug mode logging """
                method = getattr(auth_inst, method_name)

                def wrapped_method(*args, **kwargs):
                    """ Wrapped login or process_response method """
                    result = method(*args, **kwargs)
                    log.info(
                        "SAML login %s for IdP %s. Data: %s. Next url %s. XML is:\n%s",
                        action_description, idp.name, request_data, next_url, xml_getter()
                    )
                    return result

                setattr(auth_inst, method_name, wrapped_method)

            request_data = self.strategy.request_data()
            next_url = self.strategy.session_get('next')
            wrap_with_logging("login", "request", auth_inst.get_last_request_xml, request_data, next_url)
            wrap_with_logging("process_response", "response", auth_inst.get_last_response_xml, request_data, next_url)

        return auth_inst

    @cached_property
    def _config(self):
        from .models import SAMLConfiguration
        return SAMLConfiguration.current(Site.objects.get_current(get_current_request()), 'default')


class EdXSAMLIdentityProvider(SAMLIdentityProvider):
    """
    Customized version of SAMLIdentityProvider that can retrieve details beyond the standard
    details supported by the canonical upstream version.
    """

    def get_user_details(self, attributes):
        """
        Overrides `get_user_details` from the base class; retrieves those details,
        then updates the dict with values from whatever additional fields are desired.
        """
        details = super().get_user_details(attributes)
        extra_field_definitions = self.conf.get('extra_field_definitions', [])
        details.update({
            field['name']: attributes[field['urn']][0] if field['urn'] in attributes else None
            for field in extra_field_definitions
        })
        return details

    def get_attr(
        self,
        attributes: dict[str, str | list[str] | None],
        conf_key: str,
        default_attributes: tuple[str, ...],
        *,
        validate_defaults: bool = False,
    ):
        """
        This override is compatible with the new social-core base class
        (which passes a tuple of default_attributes) and preserves the
        'attr_defaults' fallback logic.
        """
        try:
            key = self.conf[conf_key]
        except KeyError:
            for key in default_attributes:
                if key in attributes:
                    break  # Found a matching default
            else:
                key = None

        if key is None:
            return self.conf.get('attr_defaults', {}).get(conf_key) or None
        try:
            value = attributes[key]
        except KeyError:
            return self.conf.get('attr_defaults', {}).get(conf_key) or None

        if isinstance(value, list):
            try:
                return value[0]
            except IndexError:
                log.warning(
                    '[THIRD_PARTY_AUTH] SAML attribute value not found. '
                    'The attribute %s was present but the list was empty.',
                    key
                )
        else:
            return value
        return self.conf.get('attr_defaults', {}).get(conf_key) or None

    @property
    def saml_sp_configuration(self):
        """Get the SAMLConfiguration for this IdP"""
        return self.conf['saml_sp_configuration']


class SapSuccessFactorsIdentityProvider(EdXSAMLIdentityProvider):
    """
    Customized version of EdXSAMLIdentityProvider that knows how to retrieve user details
    from the SAPSuccessFactors OData API, rather than parse them directly off the
    SAML assertion that we get in response to a login attempt.
    """

    required_variables = (
        'sapsf_oauth_root_url',
        'sapsf_private_key',
        'odata_api_root_url',
        'odata_company_id',
        'odata_client_id',
    )

    # Define the relationships between SAPSF record fields and Open edX logistration fields.
    default_field_mapping = {
        'firstName': ['username', 'first_name'],
        'lastName': 'last_name',
        'defaultFullName': 'fullname',
        'email': 'email',
        'country': 'country',
    }

    defaults_value_mapping = {
        'defaultFullName': 'attr_full_name',
        'firstName': 'attr_first_name',
        'lastName': 'attr_last_name',
        'username': 'attr_username',
        'email': 'attr_email',
    }

    # Define a simple mapping to relate SAPSF values to Open edX-compatible values for
    # any given field. By default, this only contains the Country field, as SAPSF supplies
    # a country name, which has to be translated to a country code.
    country_mapping = {name: code for code, name in countries}

    # Unfortunately, not everything has a 1:1 name mapping between Open edX and SAPSF, so
    # we need some overrides. TODO: Fill in necessary mappings
    country_mapping.update({
        'United States': 'US',
    })

    default_value_mapping = {
        'country': country_mapping
    }

    def get_registration_fields(self, response):
        """
        Get a dictionary mapping registration field names to default values.
        """
        field_mapping = self.field_mappings
        value_defaults = self.conf.get('attr_defaults', {})
        value_defaults = {key: value_defaults.get(value, '') for key, value in self.defaults_value_mapping.items()}
        registration_fields = {}
        for odata_name, edx_name in field_mapping.items():
            if isinstance(edx_name, list):
                for value in edx_name:
                    registration_fields[value] = response['d'].get(odata_name, value_defaults.get(odata_name, ''))
            else:
                registration_fields[edx_name] = response['d'].get(odata_name, value_defaults.get(odata_name, ''))

        value_mapping = self.value_mappings
        for field, value in registration_fields.items():
            if field in value_mapping and value in value_mapping[field]:
                registration_fields[field] = value_mapping[field][value]
        return registration_fields

    @property
    def field_mappings(self):
        """
        Get a dictionary mapping the field names returned in an SAP SuccessFactors
        user entity to the field names with which those values should be used in
        the Open edX registration form.
        """
        overrides = self.conf.get('sapsf_field_mappings', {})
        base = self.default_field_mapping.copy()
        base.update(overrides)
        return base

    @property
    def value_mappings(self):
        """
        Get a dictionary mapping of field names to override objects which each
        map values received from SAP SuccessFactors to values expected in the
        Open edX platform registration form.
        """
        overrides = self.conf.get('sapsf_value_mappings', {})
        base = deepcopy(self.default_value_mapping)
        for field, override in overrides.items():
            if field in base:
                base[field].update(override)
            else:
                base[field] = override[field]
        return base

    @property
    def timeout(self):
        """
        The number of seconds OData API requests should wait for a response before failing.
        """
        return self.conf.get('odata_api_request_timeout', 10)

    @property
    def sapsf_idp_url(self):
        return self.conf['sapsf_oauth_root_url'] + 'idp'

    @property
    def sapsf_token_url(self):
        return self.conf['sapsf_oauth_root_url'] + 'token'

    @property
    def sapsf_private_key(self):
        return self.conf['sapsf_private_key']

    @property
    def odata_api_root_url(self):
        return self.conf['odata_api_root_url']

    @property
    def odata_company_id(self):
        return self.conf['odata_company_id']

    @property
    def odata_client_id(self):
        return self.conf['odata_client_id']

    @property
    def oauth_user_id(self):
        return self.conf.get('oauth_user_id')

    def invalid_configuration(self):
        """
        Check that we have all the details we need to properly retrieve rich data from the
        SAP SuccessFactors BizX OData API. If we don't, then we should log a warning indicating
        the specific variables that are missing.
        """
        if not all(var in self.conf for var in self.required_variables):
            missing = [var for var in self.required_variables if var not in self.conf]
            log.warning(
                '[THIRD_PARTY_AUTH] To retrieve rich user data for a SAP SuccessFactors identity provider, '  # noqa: UP032  # pylint: disable=line-too-long
                'the following keys in other_settings are required, but were missing. MissingKeys: {keys}'.format(
                    keys=missing
                )
            )
            return missing

    def log_bizx_api_exception(self, transaction_data, err):  # pylint: disable=missing-function-docstring
        try:
            sys_msg = err.response.content
        except AttributeError:
            sys_msg = 'Not available'
        try:
            headers = err.response.headers
        except AttributeError:
            headers = 'Not available'
        token_data = transaction_data.get('token_data')
        token_data = token_data if token_data else 'Not available'
        log_msg_template = (
            'SAPSuccessFactors exception received for {operation_name} request.  ' +
            'URL: {url}  ' +
            'Company ID: {company_id}.  ' +
            'User ID: {user_id}.  ' +
            'Error message: {err_msg}.  ' +
            'System message: {sys_msg}.  ' +
            'Headers: {headers}.  ' +
            'Token Data: {token_data}.'
        )
        log_msg = log_msg_template.format(
            operation_name=transaction_data['operation_name'],
            url=transaction_data['endpoint_url'],
            company_id=transaction_data['company_id'],
            user_id=transaction_data['user_id'],
            err_msg=str(err),
            sys_msg=sys_msg,
            headers=headers,
            token_data=token_data,
        )
        log.warning(log_msg, exc_info=True)

    def generate_bizx_oauth_api_saml_assertion(self, user_id):
        """
        Obtain a SAML assertion from the SAP SuccessFactors BizX OAuth2 identity provider service using
        information specified in the third party authentication configuration "Advanced Settings" section.
        Utilizes the OAuth user_id if defined in Advanced Settings in order to generate the SAML assertion,
        otherwise utilizes the user_id for the current user in context.
        """
        session = requests.Session()
        oauth_user_id = self.oauth_user_id if self.oauth_user_id else user_id
        transaction_data = {
            'token_url': self.sapsf_token_url,
            'client_id': self.odata_client_id,
            'user_id': oauth_user_id,
            'private_key': self.sapsf_private_key,
        }
        try:
            assertion = session.post(
                self.sapsf_idp_url,
                data=transaction_data,
                timeout=self.timeout,
            )
            assertion.raise_for_status()
        except requests.RequestException as err:
            transaction_data['operation_name'] = 'generate_bizx_oauth_api_saml_assertion'
            transaction_data['endpoint_url'] = self.sapsf_idp_url
            transaction_data['company_id'] = self.odata_company_id
            self.log_bizx_api_exception(transaction_data, err)
            return None
        return assertion.text

    def generate_bizx_oauth_api_access_token(self, user_id):
        """
        Request a new access token from the SuccessFactors BizX OAuth2 identity provider service
        using a valid SAML assertion (see generate_bizx_api_saml_assertion) and the infomration specified
        in the third party authentication configuration "Advanced Settings" section.
        """
        session = requests.Session()
        transaction_data = {
            'client_id': self.odata_client_id,
            'company_id': self.odata_company_id,
            'grant_type': 'urn:ietf:params:oauth:grant-type:saml2-bearer',
        }
        assertion = self.generate_bizx_oauth_api_saml_assertion(user_id)
        if not assertion:
            return None
        try:
            transaction_data['assertion'] = assertion
            token_response = session.post(
                self.sapsf_token_url,
                data=transaction_data,
                timeout=self.timeout,
            )
            token_response.raise_for_status()
        except requests.RequestException as err:
            transaction_data['operation_name'] = 'generate_bizx_oauth_api_access_token'
            transaction_data['endpoint_url'] = self.sapsf_token_url
            transaction_data['user_id'] = user_id
            self.log_bizx_api_exception(transaction_data, err)
            return None
        return token_response.json()

    def get_bizx_odata_api_client(self, user_id):  # pylint: disable=missing-function-docstring
        session = requests.Session()
        access_token_data = self.generate_bizx_oauth_api_access_token(user_id)
        if not access_token_data:
            return None
        token_string = access_token_data['access_token']
        session.headers.update({'Authorization': f'Bearer {token_string}', 'Accept': 'application/json'})
        session.token_data = access_token_data
        return session

    def get_user_details(self, attributes):
        """
        Attempt to get rich user details from the SAP SuccessFactors OData API. If we're missing any
        of the info we need to do that, or if the request triggers an exception, then fail nicely by
        returning the basic user details we're able to extract from just the SAML response.
        """
        basic_details = super().get_user_details(attributes)
        if self.invalid_configuration():
            return basic_details
        user_id = basic_details['username']
        # endpoint_url is constructed from field_mappings setting of SAML Provider config.
        # We convert field_mappings to make comma separated list of the fields which needs to be pulled from BizX
        fields = ','.join(self.field_mappings)
        endpoint_url = '{root_url}User(userId=\'{user_id}\')?$select={fields}'.format(  # noqa: UP032
            root_url=self.odata_api_root_url,
            user_id=user_id,
            fields=fields,
        )
        client = self.get_bizx_odata_api_client(user_id=user_id)
        if not client:
            return basic_details

        try:
            response = client.get(
                endpoint_url,
                timeout=self.timeout,
            )
            response.raise_for_status()
            response = response.json()
        except requests.RequestException as err:
            transaction_data = {
                'operation_name': 'get_user_details',
                'endpoint_url': endpoint_url,
                'user_id': user_id,
                'company_id': self.odata_company_id,
                'token_data': client.token_data,
            }
            self.log_bizx_api_exception(transaction_data, err)
            return basic_details

        log.info('[THIRD_PARTY_AUTH] BizX Odata response for user [%s] %s', user_id, response)
        return self.get_registration_fields(response)


def get_saml_idp_choices():
    """
    Get a list of the available SAMLIdentityProvider subclasses that can be used to process
    SAML requests, for use in the Django administration form.
    """
    return (
        (STANDARD_SAML_PROVIDER_KEY, 'Standard SAML provider'),
        (SAP_SUCCESSFACTORS_SAML_KEY, 'SAP SuccessFactors provider'),
    )


def get_saml_idp_class(idp_identifier_string):
    """
    Given a string ID indicating the type of identity provider in use during a given request, return
    the SAMLIdentityProvider subclass able to handle requests for that type of identity provider.
    """
    choices = {
        STANDARD_SAML_PROVIDER_KEY: EdXSAMLIdentityProvider,
        SAP_SUCCESSFACTORS_SAML_KEY: SapSuccessFactorsIdentityProvider,
    }
    if idp_identifier_string not in choices:
        log.error(
            '[THIRD_PARTY_AUTH] Invalid EdXSAMLIdentityProvider subclass--'  # noqa: UP032
            'using EdXSAMLIdentityProvider base class. Provider: {provider}'.format(provider=idp_identifier_string)
        )
    return choices.get(idp_identifier_string, EdXSAMLIdentityProvider)
