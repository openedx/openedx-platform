"""Management command to modify certificate templates."""
import logging
import shlex
from argparse import RawDescriptionHelpFormatter

from django.core.management.base import BaseCommand, CommandError

from lms.djangoapps.certificates.models import ModifiedCertificateTemplateCommandConfiguration
from lms.djangoapps.certificates.tasks import handle_modify_cert_template

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """Management command to modify certificate templates.
    Example usage:
    ./manage.py lms modify_cert_template --old-text "</head>" --new text "<p>boo!</p></head>" --templates 867 3509
    """

    help = """Modify one or more certificate templates.
    This is DANGEROUS.
    * This uses string replacement to modify HTML-like templates, because the presence of
      Django Templating makes it impossible to do true parsing.
    * This isn't parameterizing the replacement text, for the same reason.  It has
      no way of knowing what is template language and what is HTML.
    Do not trust that this will get the conversion right without verification,
    and absolutely do not accepted untrusted user input for the replacement text. This is
    to be run by trusted users only.
    Always run this with dry-run or in a reliable test environment.
    """

    def add_arguments(self, parser):
        parser.formatter_class = RawDescriptionHelpFormatter
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Just show a preview of what would happen.",
        )
        parser.add_argument(
            "--old-text",
            help="Text to replace in the template.",
        )
        parser.add_argument(
            "--new-text",
            help="Replacement text for the template.",
        )
        parser.add_argument(
            "--templates",
            nargs="+",
            help="Certificate templates to modify.",
        )
        parser.add_argument(
            "--args-from-database",
            action="store_true",
            help="Use arguments from the ModifyCertificateTemplateConfiguration model instead of the command line.",
        )

    def get_args_from_database(self):
        """
        Returns an options dictionary from the current ModifiedCertificateTemplateCommandConfiguration instance.
        """
        config = ModifiedCertificateTemplateCommandConfiguration.current()
        if not config.enabled:
            raise CommandError(
                "ModifyCertificateTemplateCommandConfiguration is disabled, but --args-from-database was requested"
            )
        args = shlex.split(config.arguments)
        parser = self.create_parser("manage.py", "modify_cert_template")
        return vars(parser.parse_args(args))

    def handle(self, *args, **options):
        # database args will override cmd line args
        if options["args_from_database"]:
            options = self.get_args_from_database()
        # Check required arguments here. We can't rely on marking args "required" because they might come from django
        if not (options["old_text"] and options["new_text"] and options["templates"]):
            raise CommandError(
                "The following arguments are required: --old-text, --new-text, --templates"
            )
        log.info(
            "modify_cert_template starting, dry-run={dry_run}, templates={templates}, "
            "old-text={old}, new-text={new}".format(
                dry_run=options["dry_run"],
                templates=options["templates"],
                old=options["old_text"],
                new=options["new_text"],
            )
        )
        handle_modify_cert_template.delay(options)
