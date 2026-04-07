"""
Management commands for third_party_auth
"""


import logging

from django.core.management.base import BaseCommand, CommandError

from common.djangoapps.third_party_auth.models import SAMLConfiguration, SAMLProviderConfig
from common.djangoapps.third_party_auth.tasks import fetch_saml_metadata


class Command(BaseCommand):
    """ manage.py commands to manage SAML/Shibboleth SSO """
    help = '''Configure/maintain/update SAML-based SSO'''

    def add_arguments(self, parser):
        parser.add_argument('--pull', action='store_true', help="Pull updated metadata from external IDPs")
        parser.add_argument(
            '--run-checks',
            action='store_true',
            help="Run checks on SAMLProviderConfig configurations and report potential issues"
        )

    def handle(self, *args, **options):
        should_pull_saml_metadata = options.get('pull', False)
        should_run_checks = options.get('run_checks', False)

        if should_pull_saml_metadata:
            self._handle_pull_metadata()
            return

        if should_run_checks:
            self._handle_run_checks()
            return

        raise CommandError("Command must be used with '--pull' or '--run-checks' option.")

    def _handle_pull_metadata(self):
        """
        Handle the --pull option to fetch and update SAML metadata from external providers.
        This sets up logging and calls the fetch_saml_metadata task.
        """
        log_handler = logging.StreamHandler(self.stdout)
        log_handler.setLevel(logging.DEBUG)
        log = logging.getLogger('common.djangoapps.third_party_auth.tasks')
        log.propagate = False
        log.addHandler(log_handler)
        total, skipped, attempted, updated, failed, failure_messages = fetch_saml_metadata()
        self.stdout.write(
            "\nDone."
            "\n{total} provider(s) found in database."
            "\n{skipped} skipped and {attempted} attempted."
            "\n{updated} updated and {failed} failed.\n".format(
                total=total,
                skipped=skipped, attempted=attempted,
                updated=updated, failed=failed,
            )
        )

        if failed > 0:
            raise CommandError(
                "Command finished with the following exceptions:\n\n{failures}".format(
                    failures="\n\n".join(failure_messages)
                )
            )

    def _handle_run_checks(self):
        """
        Handle the --run-checks option for checking SAMLProviderConfig configuration issues.

        This is a report-only command that identifies potential configuration problems.
        """
        metrics = self._check_provider_configurations()
        self._report_check_summary(metrics)

    def _check_provider_configurations(self):
        """
        Check each provider configuration for potential issues:
        - Outdated configuration references
        - Site ID mismatches
        - Missing configurations (no direct config and no default)
        - Disabled providers and configurations
        Also reports informational data such as slug mismatches.

        See code comments near each log output for possible resolution details.
        Returns a dictionary of metrics about the found issues.
        """
        outdated_count = 0
        site_mismatch_count = 0
        slug_mismatch_count = 0
        null_config_count = 0
        disabled_config_count = 0
        error_count = 0
        total_providers = 0

        provider_configs = SAMLProviderConfig.objects.current_set()

        self.stdout.write(self.style.SUCCESS("SAML Configuration Check Report"))
        self.stdout.write("=" * 50)
        self.stdout.write("")

        for provider_config in provider_configs:
            total_providers += 1

            # Check if provider is disabled
            provider_disabled = not provider_config.enabled
            disabled_status = ", enabled=False" if provider_disabled else ""

            provider_info = (
                f"Provider (id={provider_config.id}, "
                f"name={provider_config.name}, slug={provider_config.slug}, "
                f"site_id={provider_config.site_id}{disabled_status})"
            )

            # Provider disabled status is already included in provider_info format

            try:
                if not provider_config.saml_configuration:
                    null_config_count, disabled_config_count = self._check_no_config(
                        provider_config, provider_info, null_config_count, disabled_config_count
                    )
                    continue

                # Check if SAML configuration is disabled
                if not provider_config.saml_configuration.enabled:
                    # Resolution: Enable the SAML configuration in Django admin
                    # or assign a different configuration
                    self.stdout.write(
                        f"[WARNING] {provider_info} "
                        f"has SAML config (id={provider_config.saml_configuration_id}, enabled=False)."
                    )
                    disabled_config_count += 1

                # Check configuration currency
                current_config = SAMLConfiguration.current(
                    provider_config.saml_configuration.site_id,
                    provider_config.saml_configuration.slug
                )

                if current_config and (current_config.id != provider_config.saml_configuration_id):
                    # Resolution: Update the provider's saml_configuration_id to the current config ID
                    self.stdout.write(
                        f"[WARNING] {provider_info} "
                        f"has outdated SAML config (id={provider_config.saml_configuration_id}) which "
                        f"should be updated to the current SAML config (id={current_config.id})."
                    )
                    outdated_count += 1

                # Check site ID match
                if provider_config.saml_configuration.site_id != provider_config.site_id:
                    config_site_id = provider_config.saml_configuration.site_id
                    # Resolution: Create a new SAML configuration for the correct site
                    # or move the provider to the matching site
                    self.stdout.write(
                        f"[WARNING] {provider_info} "
                        f"SAML config (id={provider_config.saml_configuration_id}, "
                        f"site_id={config_site_id}) does not match the provider's site_id."
                    )
                    site_mismatch_count += 1

                # Check slug match
                if provider_config.saml_configuration.slug not in (provider_config.slug, 'default'):
                    config_id = provider_config.saml_configuration_id
                    saml_configuration_slug = provider_config.saml_configuration.slug
                    config_disabled_status = ", enabled=False" if not provider_config.saml_configuration.enabled else ""
                    # Resolution: This is informational only - provider can use
                    # a different slug configuration
                    self.stdout.write(
                        f"[INFO] {provider_info} has "
                        f"SAML config (id={config_id}, slug='{saml_configuration_slug}'{config_disabled_status}) "
                        "that does not match the provider's slug."
                    )
                    slug_mismatch_count += 1

            except Exception as e:  # pylint: disable=broad-except
                self.stderr.write(f"[ERROR] Error processing {provider_info}: {e}")
                error_count += 1

        metrics = {
            'total_providers': {'count': total_providers, 'requires_attention': False},
            'outdated_count': {'count': outdated_count, 'requires_attention': True},
            'site_mismatch_count': {'count': site_mismatch_count, 'requires_attention': True},
            'slug_mismatch_count': {'count': slug_mismatch_count, 'requires_attention': False},
            'null_config_count': {'count': null_config_count, 'requires_attention': False},
            'disabled_config_count': {'count': disabled_config_count, 'requires_attention': True},
            'error_count': {'count': error_count, 'requires_attention': True},
        }

        return metrics

    def _check_no_config(self, provider_config, provider_info, null_config_count, disabled_config_count):
        """Helper to check providers with no direct SAML configuration."""
        default_config = SAMLConfiguration.current(provider_config.site_id, 'default')
        if not default_config or default_config.id is None:
            # Resolution: Create/Link a SAML configuration for this provider
            # or create/link a default configuration for the site
            self.stdout.write(
                f"[WARNING] {provider_info} has no direct SAML configuration and "
                "no matching default configuration was found."
            )
            null_config_count += 1

        elif not default_config.enabled:
            # Resolution: Enable the provider's linked SAML configuration
            # or create/link a specific configuration for this provider
            self.stdout.write(
                f"[WARNING] {provider_info} has no direct SAML configuration and "
                f"the default configuration (id={default_config.id}, enabled=False)."
            )
            disabled_config_count += 1

        return null_config_count, disabled_config_count

    def _report_check_summary(self, metrics):
        """
        Print a summary of the check results.
        """
        total_requiring_attention = sum(
            metric_data['count'] for metric_data in metrics.values()
            if metric_data['requires_attention']
        )

        self.stdout.write(self.style.SUCCESS("CHECK SUMMARY:"))
        self.stdout.write(f"  Providers checked: {metrics['total_providers']['count']}")
        self.stdout.write("")

        # Informational only section
        self.stdout.write("Informational only:")
        self.stdout.write(f"  Slug mismatches: {metrics['slug_mismatch_count']['count']}")
        self.stdout.write(f"  Missing configs: {metrics['null_config_count']['count']}")
        self.stdout.write("")

        # Issues requiring attention section
        if total_requiring_attention > 0:
            self.stdout.write("Issues requiring attention:")
            self.stdout.write(f"  Outdated: {metrics['outdated_count']['count']}")
            self.stdout.write(f"  Site mismatches: {metrics['site_mismatch_count']['count']}")
            self.stdout.write(f"  Disabled configs: {metrics['disabled_config_count']['count']}")
            self.stdout.write(f"  Errors: {metrics['error_count']['count']}")
            self.stdout.write("")
            self.stdout.write(f"Total issues requiring attention: {total_requiring_attention}")
        else:
            self.stdout.write(self.style.SUCCESS("No configuration issues found!"))
