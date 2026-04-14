"""
Management command to re-write UIDs identifying learners in external organizations' systems

Intented for use in production environments, to help support migration
of existing SSO learners into our most-recent program enrollment flow
without needing to manually re-link their account.
"""


import json
import logging
from io import open as py3_open
from textwrap import dedent

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Updates UserSocialAuth records to use UIDs provided in the supplied JSON file

    Example usage:
        $ ./manage.py lms migrate_saml_uids.py \
          --uid-mapping=path/to/file.json
          --saml-provider-slug=default
    """
    help = dedent(__doc__).strip()

    def add_arguments(self, parser):
        parser.add_argument(
            '--uid-mapping',
            help='path to utf-8-encoded json file containing an array of objects with keys email and student_key'
        )
        parser.add_argument(
            '--saml-provider-slug',
            help='slug of SAMLProvider for which records should be updated'
        )

    def _count_results(self, email_map, uid_mappings):
        """
        Iterate over the input file to count results.

        returns:
            not_previously_linked (int): items in uid_mappings which were unnecessary,
                                         since the learner hasn't linked their account
            updated (int): counts items in the uid_mappings which were processed
            duplicated_in_mapping (int): counts items in the uid_mappings with emails
                                         that were already processed, such that the
                                         latest-occurring one was the only one that
                                         was processed
        """
        not_previously_linked = 0
        updated = 0
        duplicated_in_mapping = 0
        for mapping in uid_mappings:
            info_for_email = email_map[mapping['email']]
            if not info_for_email['counted']:
                not_previously_linked += not info_for_email['updated']
                updated += info_for_email['updated']
                info_for_email['counted'] = True
            else:
                duplicated_in_mapping += 1
        return not_previously_linked, updated, duplicated_in_mapping

    def handle(self, *args, **options):
        """
        Performs the re-writing
        """
        User = get_user_model()
        with py3_open(options['uid_mapping'], 'r', encoding='utf-8') as f:  # noqa: UP020
            uid_mappings = json.load(f)
        slug = options['saml_provider_slug']

        email_map = {m['email']: {'uid': m['student_key'], 'updated': False, 'counted': False} for m in uid_mappings}
        users = User.objects.prefetch_related('social_auth').filter(social_auth__uid__startswith=slug + ':')

        missed = 0
        for user in users:
            email = user.email
            try:
                info_for_email = email_map[email]
            except KeyError:
                missed += 1
                continue
            info_for_email['updated'] = True
            uid = info_for_email['uid']
            auths = user.social_auth.filter(uid__startswith=slug + ':')
            auth = auths[0]
            if auths.count() > 1:
                log.info('User {email} has multiple {slug} UserSocialAuth entries, '  # noqa: UP032
                         'updating only one of them'.format(
                             email=email,
                             slug=slug
                         ))
            auth.uid = f'{slug}:{uid}'
            auth.save()

        not_previously_linked, updated, duplicated_in_mapping = \
            self._count_results(email_map, uid_mappings)

        log.info(
            'Number of users with {slug} UserSocialAuth records for which there was '  # noqa: UP032
            'no mapping in the provided file: {missed}'.format(
                slug=slug,
                missed=missed
            )
        )
        log.info(
            'Number of users identified in the mapping file without {slug}'  # noqa: UP032
            ' UserSocialAuth records: {not_previously_linked}'.format(
                slug=slug,
                not_previously_linked=not_previously_linked
            )
        )
        log.info(
            'Number of mappings in the mapping file where the identified'  # noqa: UP032
            ' user has already been processed: {duplicated_in_mapping}'.format(
                duplicated_in_mapping=duplicated_in_mapping
            )
        )
        log.info(
            'Number of mappings in the mapping file updated: '  # noqa: UP032
            '{updated}'.format(
                updated=updated
            )
        )
