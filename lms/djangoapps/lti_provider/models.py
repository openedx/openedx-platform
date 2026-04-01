"""
Database models for the LTI provider feature.

This app uses migrations. If you make changes to this model, be sure to create
an appropriate migration file and check it in at the same time as your model
changes. To do that,

1. Go to the edx-platform dir
2. ./manage.py lms schemamigration lti_provider --auto "description" --settings=devstack
"""


import logging

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.db import models
from django.utils.translation import gettext as _
from opaque_keys.edx.django.models import CourseKeyField, UsageKeyField

from openedx.core.djangolib.fields import CharNullField
from openedx.core.lib.hash_utils import short_token

log = logging.getLogger("edx.lti_provider")


class LtiConsumer(models.Model):
    """
    Database model representing an LTI consumer. This model stores the consumer
    specific settings, such as the OAuth key/secret pair and any LTI fields
    that must be persisted.

    .. no_pii:
    """
    consumer_name = models.CharField(max_length=255, unique=True)
    consumer_key = models.CharField(max_length=32, unique=True, db_index=True, default=short_token)
    consumer_secret = models.CharField(max_length=32, unique=True, default=short_token)
    instance_guid = CharNullField(max_length=255, blank=True, null=True, unique=True)
    require_user_account = models.BooleanField(blank=True, default=False, help_text=_(
        "When checked, the LTI content will load only for learners who have an account "
        "in this instance. This is required only for linking learner accounts with "
        "the LTI consumer. See the Open edX LTI Provider documentation for more details."
    ))
    use_lti_pii = models.BooleanField(blank=True, default=False, help_text=_(
        "When checked, the platform will automatically use any personal information provided "
        "via LTI to create an account. Otherwise an anonymous account will be created."
    ))

    @staticmethod
    def get_or_supplement(instance_guid, consumer_key):
        """
        The instance_guid is the best way to uniquely identify an LTI consumer.
        However according to the LTI spec, the instance_guid field is optional
        and so cannot be relied upon to be present.

        This method first attempts to find an LtiConsumer by instance_guid.
        Failing that, it tries to find a record with a matching consumer_key.
        This can be the case if the LtiConsumer record was created as the result
        of an LTI launch with no instance_guid.

        If the instance_guid is now present, the LtiConsumer model will be
        supplemented with the instance_guid, to more concretely identify the
        consumer.

        In practice, nearly all major LTI consumers provide an instance_guid, so
        the fallback mechanism of matching by consumer key should be rarely
        required.
        """
        consumer = None
        if instance_guid:
            try:
                consumer = LtiConsumer.objects.get(instance_guid=instance_guid)
            except LtiConsumer.DoesNotExist:
                # The consumer may not exist, or its record may not have a guid
                pass

        # Search by consumer key instead of instance_guid. If there is no
        # consumer with a matching key, the LTI launch does not have permission
        # to access the content.
        if not consumer:
            consumer = LtiConsumer.objects.get(
                consumer_key=consumer_key,
            )

        # Add the instance_guid field to the model if it's not there already.
        if instance_guid and not consumer.instance_guid:
            consumer.instance_guid = instance_guid
            consumer.save()
        return consumer


class OutcomeService(models.Model):
    """
    Model for a single outcome service associated with an LTI consumer. Note
    that a given consumer may have more than one outcome service URL over its
    lifetime, so we need to store the outcome service separately from the
    LtiConsumer model.

    An outcome service can be identified in two ways, depending on the
    information provided by an LTI launch. The ideal way to identify the service
    is by instance_guid, which should uniquely identify a consumer. However that
    field is optional in the LTI launch, and so if it is missing we can fall
    back on the consumer key (which should be created uniquely for each consumer
    although we don't have a technical way to guarantee that).

    Some LTI-specified fields use the prefix lis_; this refers to the IMS
    Learning Information Services standard from which LTI inherits some
    properties

    .. no_pii:
    """
    lis_outcome_service_url = models.CharField(max_length=255, unique=True)
    lti_consumer = models.ForeignKey(LtiConsumer, on_delete=models.CASCADE)


class GradedAssignment(models.Model):
    """
    Model representing a single launch of a graded assignment by an individual
    user. There will be a row created here only if the LTI consumer may require
    a result to be returned from the LTI launch (determined by the presence of
    the lis_result_sourcedid parameter in the launch POST). There will be only
    one row created for a given usage/consumer combination; repeated launches of
    the same content by the same user from the same LTI consumer will not add
    new rows to the table.

    Some LTI-specified fields use the prefix lis_; this refers to the IMS
    Learning Information Services standard from which LTI inherits some
    properties

    .. no_pii:
    """
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    course_key = CourseKeyField(db_index=True)
    usage_key = UsageKeyField(db_index=True)
    outcome_service = models.ForeignKey(OutcomeService, on_delete=models.CASCADE)
    lis_result_sourcedid = models.CharField(max_length=255, db_index=True)
    version_number = models.IntegerField(default=0)

    class Meta:
        unique_together = ('outcome_service', 'lis_result_sourcedid')


class LtiUser(models.Model):
    """
    Model mapping the identity of an LTI user to an account on the edX platform.
    The LTI user_id field is guaranteed to be unique per LTI consumer (per
    to the LTI spec), so we guarantee a unique mapping from LTI to edX account
    by using the lti_consumer/lti_user_id tuple.

    .. no_pii:
    """
    lti_consumer = models.ForeignKey(LtiConsumer, on_delete=models.CASCADE)
    lti_user_id = models.CharField(max_length=255)
    edx_user = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        unique_together = ('lti_consumer', 'lti_user_id')
