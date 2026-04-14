"""Models providing Programs support for the LMS and Studio."""
from config_models.models import ConfigurationModel
from django.db import models
from django.utils.translation import gettext_lazy as _
from lti_consumer.models import LtiConfiguration
from model_utils.models import TimeStampedModel
from simple_history.models import HistoricalRecords


class ProgramsApiConfig(ConfigurationModel):
    """
    This model no longer fronts an API, but now sets a few config-related values for the idea of programs in general.

    A rename to ProgramsConfig would be more accurate, but costly in terms of developer time.

    .. no_pii:
    """
    class Meta:
        app_label = "programs"

    marketing_path = models.CharField(
        max_length=255,
        blank=True,
        help_text=_(
            'Path used to construct URLs to programs marketing pages (e.g., "/foo").'
        )
    )


class AbstractProgramLTIConfiguration(TimeStampedModel):
    """
    Associates a program with a LTI provider and configuration
    """
    class Meta:
        abstract = True

    program_uuid = models.CharField(
        primary_key=True,
        db_index=True,
        max_length=50,
        verbose_name=_("Program UUID"),
    )
    enabled = models.BooleanField(
        default=True,
        help_text=_("If disabled, the LTI in the associated program will be disabled.")
    )
    lti_configuration = models.ForeignKey(
        LtiConfiguration,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text=_("The LTI configuration data for this program/provider."),
    )
    provider_type = models.CharField(
        blank=False,
        max_length=50,
        verbose_name=_("LTI provider"),
        help_text=_("The LTI provider's id"),
    )

    def __str__(self):
        return f"Configuration(uuid='{self.program_uuid}', provider='{self.provider_type}', enabled={self.enabled})"

    @classmethod
    def get(cls, program_uuid):
        """
        Lookup a program discussion configuration by program uuid.
        """
        return cls.objects.filter(
            program_uuid=program_uuid
        ).first()


class ProgramLiveConfiguration(AbstractProgramLTIConfiguration):
    """
    .. no_pii:
    """
    history = HistoricalRecords()


class ProgramDiscussionsConfiguration(AbstractProgramLTIConfiguration):
    """
    .. no_pii:
    """
    history = HistoricalRecords()
