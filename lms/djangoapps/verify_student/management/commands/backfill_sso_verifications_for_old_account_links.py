"""
Management command to backfill verification records for preexisting account links

Meant to facilitate the alteration of a particular
third_party_auth_samlproviderconfig to flip on the
enable_sso_id_verification bit, which would ordinarily leave any
preexisting account links without the corresponding resultant ID
verification record.

This also manually triggers the same signal which is sent on creation
of SSO IDV records.
"""

from django.core.management.base import BaseCommand, CommandError
from social_django.models import UserSocialAuth

from common.djangoapps.third_party_auth.api.utils import filter_user_social_auth_queryset_by_provider
from common.djangoapps.third_party_auth.provider import Registry
from lms.djangoapps.verify_student.models import SSOVerification


class Command(BaseCommand):
    """
    Management command to backfill verification records for preexisting account links

    Meant to facilitate the alteration of a particular
    third_party_auth_samlproviderconfig to flip on the
    enable_sso_id_verification bit, which would ordinarily leave any
    preexisting account links without the corresponding resultant ID
    verification record.

    Example usage:
        $ ./manage.py lms backfill_sso_verifications_for_old_account_links --provider-slug=saml-gatech
    """
    help = 'Backfills SSO verification records for the given SAML provider slug'

    def add_arguments(self, parser):
        parser.add_argument(
            '--provider-slug',
            required=True,
        )

    def filter_user_social_auth_queryset_by_ssoverification_existence(self, query_set):
        return query_set.filter(user__ssoverification__isnull=True)

    def handle(self, *args, **options):
        provider_slug = options.get('provider_slug', None)

        try:
            provider = Registry.get(provider_slug)
        except ValueError as e:  # pylint: disable=unused-variable  # noqa: F841
            raise CommandError(f'provider slug {provider_slug} does not exist')  # pylint: disable=raise-missing-from  # noqa: B904

        query_set = UserSocialAuth.objects.select_related('user__profile')
        query_set = filter_user_social_auth_queryset_by_provider(query_set, provider)
        query_set = self.filter_user_social_auth_queryset_by_ssoverification_existence(query_set)
        for user_social_auth in query_set:
            verification = SSOVerification.objects.create(
                user=user_social_auth.user,
                status="approved",
                name=user_social_auth.user.profile.name,
                identity_provider_type=provider.full_class_name,
                identity_provider_slug=provider.slug,
            )
            # Send a signal so users who have already passed their courses receive credit
            verification.send_approval_signal(provider.slug)
