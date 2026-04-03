"""
LTI Providers for course live module
"""
from abc import ABC
from typing import Dict, List

from django.conf import settings


class LiveProvider(ABC):
    """
    Defines basic structure of lti provider
    """
    id: str
    name: str
    features: List[str] = []
    requires_username: bool = False
    requires_email: bool = False
    additional_parameters: List[str] = []

    @property
    def has_free_tier(self) -> bool:
        """
        Property defines if provider has free tier
        """
        return False

    def requires_pii_sharing(self):
        """
        Check if provider requires any PII ie username or email
        """
        return self.requires_email or self.requires_username

    def requires_custom_email(self):
        """
        Check if provider requires custom instructor email
        """
        return 'custom_instructor_email' in self.additional_parameters

    @property
    def is_enabled(self):
        """
        To check if provider is enabled
        To be implemented in subclasses
        """
        raise NotImplementedError()

    def __dict__(self):
        return {
            'name': self.name,
            'has_free_tier': self.has_free_tier,
            'features': self.features,
            'pii_sharing': {
                'username': self.requires_username,
                'email': self.requires_email,
            },
            'additional_parameters': self.additional_parameters
        }


class HasGlobalCredentials(ABC):
    """
    Defines structure for providers with global credentials
    """
    key: str
    secret: str
    url: str

    @staticmethod
    def get_global_keys() -> Dict:
        """
        Get keys from settings
        """
        raise NotImplementedError()

    def has_valid_global_keys(self) -> bool:
        """
        Check if keys are valid and not None
        """
        raise NotImplementedError()


class Zoom(LiveProvider):
    """
    Zoom LTI PRO live provider
    """
    id = 'zoom'
    name = 'Zoom LTI PRO'
    additional_parameters = [
        'custom_instructor_email'
    ]

    @property
    def is_enabled(self):
        return True


class BigBlueButton(LiveProvider, HasGlobalCredentials):
    """
    Big Blue Button LTI provider
    """
    id = 'big_blue_button'
    name = 'Big Blue Button'
    requires_username: bool = True

    def __init__(self):
        """
        initialize BigBlueButton object
        """
        super().__init__()
        self.has_valid_global_keys()

    @property
    def has_free_tier(self) -> bool:
        """
        Check if free tier is enabled by checking for valid keys
        """
        return self.has_valid_global_keys()

    @property
    def is_enabled(self) -> bool:
        return True

    @staticmethod
    def get_global_keys() -> Dict:
        """
        Get keys from settings
        """
        try:
            return settings.COURSE_LIVE_GLOBAL_CREDENTIALS.get('BIG_BLUE_BUTTON', {})
        except AttributeError:
            return {}

    def has_valid_global_keys(self) -> bool:
        """
        Check if keys are valid and not None
        """
        credentials = self.get_global_keys()
        if credentials:
            self.key = credentials.get('KEY')
            self.secret = credentials.get('SECRET')
            self.url = credentials.get('URL')
            return bool(self.key and self.secret and self.url)
        return False


class ProviderManager:
    """
    This class provides access to all available provider objects
    """
    providers: Dict[str, LiveProvider]

    def __init__(self):
        # auto detect live providers.
        self.providers = {provider.id: provider() for provider in LiveProvider.__subclasses__()}

    def get_enabled_providers(self) -> Dict[str, LiveProvider]:
        """
        Get Enabled providers
        """
        return {key: provider for (key, provider) in self.providers.items() if provider.is_enabled}
