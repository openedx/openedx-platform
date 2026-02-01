"""
Useful ConfigurationModel subclasses

StackedConfigurationModel: A ConfigurationModel that can be overridden at site, org and course levels
"""

# -*- coding: utf-8 -*-


from collections import defaultdict
from enum import Enum

import crum
from config_models.models import ConfigurationModel
from django.conf import settings
from django.contrib.sites.models import Site
from django.contrib.sites.requests import RequestSite
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from edx_django_utils.cache.utils import TieredCache

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration
from openedx.core.lib.cache_utils import request_cached


class Provenance(Enum):
    """
    Provenance enum
    """
    run = 'Course Run'
    org_course = 'Org/Course'
    org = 'Org'
    site = 'Site'
    global_ = 'Global'
    default = 'Default'


def validate_course_in_org(value):
    if value.count('+') != 1:
        raise ValidationError(
            _('%(value)s should have the form ORG+COURSE'),
            params={'value': value},
        )


class StackedConfigurationModel(ConfigurationModel):
    """
    A ConfigurationModel that stacks Global, Site, Org, Course, and Course Run level
    configuration values.
    """
    class Meta:
        abstract = True
        indexes = [
            # This index optimizes the .object.current_set() query
            # by preventing a filesort
            models.Index(fields=['site', 'org', 'course']),
            models.Index(fields=['site', 'org', 'org_course', 'course'])
        ]

    KEY_FIELDS = ('site', 'org', 'org_course', 'course')
    STACKABLE_FIELDS = ('enabled',)

    enabled = models.BooleanField(default=None, verbose_name=_("Enabled"), null=True)
    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text=_(
            "Configure values for all course runs associated with this site."
        ),
    )
    org = models.CharField(
        max_length=255,
        db_index=True,
        null=True,
        blank=True,
        help_text=_(
            "Configure values for all course runs associated with this "
            "Organization. This is the organization string (i.e. edX, MITx)."
        )
    )
    org_course = models.CharField(
        max_length=255,
        db_index=True,
        null=True,
        blank=True,
        verbose_name=_("Course in Org"),
        help_text=_(
            "Configure values for all course runs associated with this course. "
            "This is should be formatted as 'org+course' (i.e. MITx+6.002x, HarvardX+CS50)."
        ),
        validators=[validate_course_in_org],
    )
    course = models.ForeignKey(
        CourseOverview,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        verbose_name=_("Course Run"),
        help_text=_(
            "Configure values for this course run. This should be "
            "formatted as the CourseKey (i.e. course-v1://MITx+6.002x+2019_Q1)"
        ),
    )

    @classmethod
    def current(cls, site=None, org=None, org_course=None, course_key=None):  # pylint: disable=arguments-differ
        """
        Return the current overridden configuration at the specified level.

        Only one level may be specified at a time. Specifying multiple levels
        will result in a ValueError.

        For example, considering the following set of requirements:

            Global: Feature Disabled
            edx.org (Site): Feature Enabled
            HarvardX (org): Feature Disabled
            HarvardX/CS50 (org_course): Feature Enabled
            CS50 in 2018_Q1 (course run): Feature Disabled

        Assuming these values had been properly configured, these calls would result

            MyStackedConfig.current()  # False
            MyStackedConfig.current(site=Site(domain='edx.org'))  # True
            MyStackedConfig.current(site=Site(domain='whitelabel.edx.org')) # False -- value derived from global setting
            MyStackedConfig.current(org='HarvardX')  # False
            MyStackedConfig.current(org='MITx')  # True -- value derived from edx.org site
            MyStackedConfig.current(org_course='HarvardX/CS50')  # True
            MyStackedConfig.current(org_course='HarvardX/Bio101')  # False -- value derived from HarvardX setting
            MyStackedConfig.current(course_key=CourseKey(org='HarvardX', course='CS50', run='2018_Q1'))  # False
            MyStackedConfig.current(
                course_key=CourseKey(org='HarvardX', course='CS50', run='2019_Q1')
            )  # True -- value derived from HarvardX/CS50 setting

            bio101 = CourseKey(org='HarvardX', course='Bio101', run='2018_Q1')
            MyStackedConfig.current(course_key=cs50)  # False -- value derived from HarvardX org

        The following calls would result in errors due to overspecification:

            MyStackedConfig.current(site=Site(domain='edx.org'), org='HarvardX')
            MyStackedConfig.current(site=Site(domain='edx.org'), course=cs50)
            MyStackedConfig.current(org='HarvardX', course=cs50)

        Arguments:
            site: The Site to check current values for
            org: The org to check current values for
            org_course: The course in a specific org to check current values for
            course_key: The course to check current values for

        Returns:
            An instance of :class:`cls.attribute_tuple()` with the overridden values
            specified down to the level of the supplied argument (or global values if
            no arguments are supplied).
        """
        cache_key_name = cls.cache_key_name(site, org, org_course, course_key)
        cached_response = TieredCache.get_cached_response(cache_key_name)
        if cached_response.is_found and cached_response.value is not None:
            return cached_response.value

        # Raise an error if more than one of site/org/course are specified simultaneously.
        if len([arg for arg in [site, org, org_course, course_key] if arg is not None]) > 1:
            raise ValueError("Only one of site, org, org_course, and course can be specified")

        if org_course is None and course_key is not None:
            org_course = cls._org_course_from_course_key(course_key)

        if org is None and org_course is not None:
            org = cls._org_from_org_course(org_course)

        if site is None and org is not None:
            site = cls._site_from_org(org)

        stackable_fields = [cls._meta.get_field(field_name) for field_name in cls.STACKABLE_FIELDS]
        field_defaults = {
            field.name: field.get_default()
            for field in stackable_fields
        }

        values = field_defaults.copy()

        # Build a multi_filter_query that defaults to querying for the global-level setting and adds queries
        # for the stacked-level settings when applicable.
        # Note: Django2+ requires checking for 'isnull' rather than passing 'None' in the queries.
        multi_filter_query = Q(site__isnull=True, org__isnull=True, org_course__isnull=True, course_id__isnull=True)
        if site:
            multi_filter_query |= Q(site=site, org__isnull=True, org_course__isnull=True, course_id__isnull=True)
        if org:
            multi_filter_query |= Q(site__isnull=True, org=org, org_course__isnull=True, course_id__isnull=True)
        if org_course:
            multi_filter_query |= Q(site__isnull=True, org__isnull=True, org_course=org_course, course_id__isnull=True)
        if course_key:
            multi_filter_query |= Q(site__isnull=True, org__isnull=True, org_course__isnull=True, course_id=course_key)

        overrides = cls.objects.current_set().filter(multi_filter_query)

        provenances = defaultdict(lambda: Provenance.default)
        # We are sorting in python to avoid doing a filesort in the database for
        # what will only be 4 rows at maximum

        def sort_key(override):
            """
            Sort overrides in increasing specificity.

            This particular sort order sorts None before not-None (because False < True)
            It sorts global first (because all entries are None), then site entries
            (because course_id and org are None), then org, org_course and course (by the same logic)
            """
            return (
                override.course_id is not None,
                override.org_course is not None,
                override.org is not None,
                override.site_id is not None
            )

        for override in sorted(overrides, key=sort_key):
            for field in stackable_fields:
                value = field.value_from_object(override)
                if value != field_defaults[field.name]:
                    values[field.name] = value
                    if override.course_id is not None:
                        provenances[field.name] = Provenance.run
                    elif override.org_course is not None:
                        provenances[field.name] = Provenance.org_course
                    elif override.org is not None:
                        provenances[field.name] = Provenance.org
                    elif override.site_id is not None:
                        provenances[field.name] = Provenance.site
                    else:
                        provenances[field.name] = Provenance.global_

        current = cls(**values)
        current.provenances = {field.name: provenances[field.name] for field in stackable_fields}  # pylint: disable=attribute-defined-outside-init

        TieredCache.set_all_tiers(cache_key_name, current, cls.cache_timeout)
        return current

    @classmethod
    def all_current_course_configs(cls):
        """
        Return configuration for all courses
        """
        all_courses = CourseOverview.objects.all()
        all_site_configs = SiteConfiguration.objects.filter(
            site_values__contains='course_org_filter', enabled=True
        ).select_related('site')

        try:
            default_site = Site.objects.get(id=settings.SITE_ID)
        except Site.DoesNotExist:
            default_site = RequestSite(crum.get_current_request())

        sites_by_org = defaultdict(lambda: default_site)
        site_cfg_org_filters = (
            (site_cfg.site, site_cfg.site_values['course_org_filter'])
            for site_cfg in all_site_configs
        )
        sites_by_org.update({
            org: site
            for (site, orgs) in site_cfg_org_filters
            for org in (orgs if isinstance(orgs, list) else [orgs])
        })

        all_overrides = cls.objects.current_set()
        overrides = {
            (override.site_id, override.org, override.org_course, override.course_id): override
            for override in all_overrides
        }

        stackable_fields = [cls._meta.get_field(field_name) for field_name in cls.STACKABLE_FIELDS]
        field_defaults = {
            field.name: field.get_default()
            for field in stackable_fields
        }

        def provenance(course, field):
            """
            Return provenance for given field
            """
            org_course = cls._org_course_from_course_key(course.id)
            org = cls._org_from_org_course(org_course)

            for (config_key, provenance) in [
                ((None, None, None, course.id), Provenance.run),
                ((None, None, org_course, None), Provenance.org_course),
                ((None, org, None, None), Provenance.org),
                ((sites_by_org[course.id.org].id, None, None, None), Provenance.site),
                ((None, None, None, None), Provenance.global_),
            ]:
                config = overrides.get(config_key)
                if config is None:
                    continue
                value = field.value_from_object(config)
                if value != field_defaults[field.name]:
                    return (value, provenance)

            return (field_defaults[field.name], Provenance.default)

        return {
            course.id: {
                field.name: provenance(course, field)
                for field in stackable_fields
            }
            for course in all_courses
        }

    @classmethod
    def cache_key_name(cls, site, org, org_course, course_key):  # pylint: disable=arguments-differ
        if site is None:
            site_id = None
        else:
            site_id = site.id

        return super().cache_key_name(site_id, org, org_course, course_key)

    @classmethod
    def _org_from_org_course(cls, org_course):
        return org_course.partition('+')[0]

    @classmethod
    def _org_course_from_course_key(cls, course_key):
        return f"{course_key.org}+{course_key.course}"

    @classmethod
    @request_cached()
    def _site_from_org(cls, org):  # lint-amnesty, pylint: disable=missing-function-docstring

        configuration = SiteConfiguration.get_configuration_for_org(org, select_related=['site'])
        if configuration is None:
            try:
                return Site.objects.get(id=settings.SITE_ID)
            except Site.DoesNotExist:
                return RequestSite(crum.get_current_request())
        else:
            return configuration.site

    def clean(self):
        # fail validation if more than one of site/org/course are specified simultaneously
        if len([arg for arg in [self.site, self.org, self.org_course, self.course] if arg is not None]) > 1:
            raise ValidationError(
                _('Configuration may not be specified at more than one level at once.')
            )
