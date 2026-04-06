"""
Django models for site configurations.
"""


import collections
from logging import getLogger

from django.contrib.sites.models import Site
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from jsonfield.fields import JSONField
from model_utils.models import TimeStampedModel

logger = getLogger(__name__)  # pylint: disable=invalid-name


class SiteConfiguration(models.Model):
    """
    Model for storing site configuration. These configuration override OpenEdx configurations and settings.
    e.g. You can override site name, logo image, favicon etc. using site configuration.

    Fields:
        site (OneToOneField): one to one field relating each configuration to a single site
        site_values (JSONField):  json field to store configurations for a site

    .. no_pii:
    """
    site = models.OneToOneField(Site, related_name='configuration', on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False, verbose_name="Enabled")
    site_values = JSONField(
        null=False,
        blank=True,
        # The actual default value is determined by calling the given callable.
        # Therefore, the default here is just {}, since that is the result of
        # calling `dict`.
        default=dict,
        load_kwargs={'object_pairs_hook': collections.OrderedDict}
    )

    def __str__(self):
        return f"<SiteConfiguration: {self.site} >"  # xss-lint: disable=python-wrap-html

    def __repr__(self):
        return self.__str__()

    def get_value(self, name, default=None):
        """
        Return Configuration value for the key specified as name argument.

        Function logs a message if configuration is not enabled or if there is an error retrieving a key.

        Args:
            name (str): Name of the key for which to return configuration value.
            default: default value tp return if key is not found in the configuration

        Returns:
            Configuration value for the given key or returns `None` if configuration is not enabled.
        """
        if self.enabled:
            try:
                return self.site_values.get(name, default)
            except AttributeError as error:
                logger.exception('Invalid JSON data. \n [%s]', error)
        else:
            logger.info("Site Configuration is not enabled for site (%s).", self.site)

        return default

    @classmethod
    def get_configuration_for_org(cls, org, select_related=None):
        """
        This returns a SiteConfiguration object which has an org_filter that matches
        the supplied org

        Args:
            org (str): Org to use to filter SiteConfigurations
            select_related (list or None): A list of values to pass as arguments to select_related
        """
        query = cls.objects.filter(site_values__contains=org, enabled=True).all()
        if select_related is not None:
            query = query.select_related(*select_related)
        for configuration in query:
            course_org_filter = configuration.get_value('course_org_filter', [])
            # The value of 'course_org_filter' can be configured as a string representing
            # a single organization or a list of strings representing multiple organizations.
            if not isinstance(course_org_filter, list):
                course_org_filter = [course_org_filter]
            if org in course_org_filter:
                return configuration
        return None

    @classmethod
    def get_value_for_org(cls, org, name, default=None):
        """
        This returns site configuration value which has an org_filter that matches
        what is passed in,

        Args:
            org (str): Course ord filter, this value will be used to filter out the correct site configuration.
            name (str): Name of the key for which to return configuration value.
            default: default value tp return if key is not found in the configuration

        Returns:
            Configuration value for the given key.
        """
        configuration = cls.get_configuration_for_org(org)
        if configuration is None:
            return default
        else:
            return configuration.get_value(name, default)

    @classmethod
    def get_all_orgs(cls):
        """
        This returns all of the orgs that are considered in site configurations, This can be used,
        for example, to do filtering.

        Returns:
            A set of all organizations present in site configuration.
        """
        org_filter_set = set()

        for configuration in cls.objects.filter(site_values__contains='course_org_filter', enabled=True).all():
            course_org_filter = configuration.get_value('course_org_filter', [])
            if not isinstance(course_org_filter, list):
                course_org_filter = [course_org_filter]
            org_filter_set.update(course_org_filter)
        return org_filter_set

    @classmethod
    def has_org(cls, org):
        """
        Check if the given organization is present in any of the site configuration.

        Returns:
            True if given organization is present in site configurations otherwise False.
        """
        return org in cls.get_all_orgs()


def save_siteconfig_without_historical_record(siteconfig, *args, **kwargs):
    """
    Save model without saving a historical record

    Make sure you know what you're doing before you use this method.

    Note: this method is copied verbatim from django-simple-history.
    """
    siteconfig.skip_history_when_saving = True
    try:
        ret = siteconfig.save(*args, **kwargs)
    finally:
        del siteconfig.skip_history_when_saving
    return ret


class SiteConfigurationHistory(TimeStampedModel):
    """
    This is an archive table for SiteConfiguration, so that we can maintain a history of
    changes. Note that the site field is not unique in this model, compared to SiteConfiguration.

    Fields:
        site (ForeignKey): foreign-key to django Site
        site_values (JSONField): json field to store configurations for a site

    .. no_pii:
    """
    site = models.ForeignKey(Site, related_name='configuration_histories', on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False, verbose_name="Enabled")
    site_values = JSONField(
        null=False,
        blank=True,
        load_kwargs={'object_pairs_hook': collections.OrderedDict}
    )

    class Meta:
        get_latest_by = 'modified'
        ordering = ('-modified', '-created',)

    def __str__(self):
        # pylint: disable=line-too-long
        return "<SiteConfigurationHistory: {site}, Last Modified: {modified} >".format(  # xss-lint: disable=python-wrap-html
            modified=self.modified,
            site=self.site,
        )

    def __repr__(self):
        return self.__str__()


@receiver(post_save, sender=SiteConfiguration)
def update_site_configuration_history(sender, instance, created, **kwargs):  # pylint: disable=unused-argument
    """
    Add site configuration changes to site configuration history.

    Recording history on updates and deletes can be skipped by first setting
    the `skip_history_when_saving` attribute on the instace, e.g.:

      site_config.skip_history_when_saving = True
      site_config.save()

    Args:
        sender: sender of the signal i.e. SiteConfiguration model
        instance: SiteConfiguration instance associated with the current signal
        created (bool): True if a new record was created.
        **kwargs: extra key word arguments
    """
    # Skip writing history when asked by the caller.  This skip feature only
    # works for non-creates.
    if created or not hasattr(instance, "skip_history_when_saving"):
        SiteConfigurationHistory.objects.create(
            site=instance.site,
            site_values=instance.site_values,
            enabled=instance.enabled,
        )
