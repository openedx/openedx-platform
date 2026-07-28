"""Provides management command calling info to tracking context."""


from django.core.management.base import BaseCommand
from eventtracking import tracker


class TrackedCommand(BaseCommand):  # pylint: disable=abstract-method
    """
    Provides management command calling info to tracking context.

    Information provided to context includes the following value:

    'command': the program name and the subcommand used to run a management command.

    In future, other values (such as args and options) could be added as needed.

    An example tracking log entry resulting from running the 'create_user' management command:

    {
        "username": "anonymous",
        "host": "",
        "event_source": "server",
        "event_type": "edx.course.enrollment.activated",
        "context": {
            "course_id": "edX/Open_DemoX/edx_demo_course",
            "org_id": "edX",
            "command": "./manage.py create_user",
        },
        "time": "2014-01-06T15:59:49.599522+00:00",
        "ip": "",
        "event": {
            "course_id": "edX/Open_DemoX/edx_demo_course",
            "user_id": 29,
            "mode": "verified"
        },
        "agent": "",
        "page": null
    }

    The name of the context used to add (and remove) these values is "edx.mgmt.command".
    The context name is used to allow the context additions to be scoped, but doesn't
    appear in the context itself.
    """
    prog_name = 'unknown'

    def create_parser(self, prog_name, subcommand):  # pylint: disable=arguments-differ
        """Wraps create_parser to snag command line info."""
        self.prog_name = f"{prog_name} {subcommand}"
        return super().create_parser(prog_name, subcommand)  # pylint: disable=super-with-arguments

    def execute(self, *args, **options):
        """Wraps base execute() to add command line to tracking context."""
        context = {
            'command': self.prog_name,
        }
        COMMAND_CONTEXT_NAME = 'edx.mgmt.command'
        with tracker.get_tracker().context(COMMAND_CONTEXT_NAME, context):
            super().execute(*args, **options)  # pylint: disable=super-with-arguments
