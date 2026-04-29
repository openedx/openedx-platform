"""
Take the list of states from settings.RETIREMENT_STATES and forces the
RetirementState table to mirror it.

We use a foreign keyed table for this instead of just using the settings
directly to generate a `choices` tuple for the model because the states
need to be configurable by open source partners and modifying the
`choices` for a model field causes new migrations to be generated,
with a variety of unpleasant follow-on effects for the partner when
upgrading the model at a later date.
"""


import copy
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F

from openedx.core.djangoapps.user_api.models import RetirementState, UserRetirementStatus

LOGGER = logging.getLogger(__name__)

START_STATE = 'PENDING'
END_STATES = ['ERRORED', 'ABORTED', 'COMPLETE']
REQUIRED_STATES = copy.deepcopy(END_STATES)
REQUIRED_STATES.insert(0, START_STATE)
REQ_STR = ','.join(REQUIRED_STATES)


class Command(BaseCommand):
    """
    Implementation of the populate command
    """
    help = 'Populates the RetirementState table with the states present in settings.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry_run',
            action='store_true',
            help='Run checks without making any changes'
        )

    def _validate_new_states(self, new_states):
        """
        Check settings for existence of states, required states
        """
        if not new_states:
            raise CommandError('settings.RETIREMENT_STATES does not exist or is empty.')

        if not set(REQUIRED_STATES).issubset(set(new_states)):
            raise CommandError('settings.RETIREMENT_STATES ({}) does not contain all required states '  # noqa: UP032
                               '({})'.format(new_states, REQ_STR))

        # Confirm that the start and end states are in the right places
        if new_states.index(START_STATE) != 0:
            raise CommandError(f'{START_STATE} must be the first state')

        num_end_states = len(END_STATES)

        if new_states[-num_end_states:] != END_STATES:
            raise CommandError('The last {} states must be these (in this order): '  # noqa: UP032
                               '{}'.format(num_end_states, END_STATES))

    def _check_current_users(self):
        """
        Check UserRetirementStatus for users currently in progress
        """
        if UserRetirementStatus.objects.exclude(current_state__state_name__in=REQUIRED_STATES).exists():
            raise CommandError(
                'Users are currently being processed. All users must be in one of these states to run this command: '  # noqa: UP032
                '{}'.format(REQ_STR)
            )

    def _check_users_in_states_to_delete(self, states_to_delete):
        if UserRetirementStatus.objects.filter(current_state__state_name__in=states_to_delete).exists():
            raise CommandError('Users exist in a state that is marked for deletion! States to delete'  # noqa: UP032
                               'are: {}'.format(states_to_delete))

    def _delete_old_states_and_create_new(self, new_states, dry_run=False):
        """
        Wipes the RetirementState table and creates new entries based on new_states
        - Note that the state_execution_order is incremented by 10 for each entry
          this should allow manual insert of "in between" states via the Django admin
          if necessary, without having to manually re-sort all of the states.
        """

        # Save off old states before
        current_state_names = RetirementState.objects.all().values_list('state_name', flat=True)

        # Generate the diff
        set_current_states = set(current_state_names)
        set_new_states = set(new_states)

        states_to_create = set_new_states - set_current_states
        states_remaining = set_current_states.intersection(set_new_states)
        states_to_delete = set_current_states - set_new_states

        # If this is a dry run we have everything we need.
        if dry_run:
            return states_to_create, states_remaining, states_to_delete

        # In theory this should not happen, this would have failed _check_current_users
        # if the state was not required, and failed _validate_new_states if we're trying
        # to remove a required state, but playing it extra safe here since a state delete
        # will cascade and remove the UserRetirementState row as well if something slips
        # through.
        if states_to_delete:
            self._check_users_in_states_to_delete(states_to_delete)

        # Delete states slated for removal
        RetirementState.objects.filter(state_name__in=states_to_delete).delete()

        # Get all of our remaining states out of the way so we don't have
        # state_execution_order collisions
        RetirementState.objects.all().update(state_execution_order=F('state_execution_order') + 500)

        # Add new rows, with space in between to manually insert stages via Django admin if necessary
        curr_sort_order = 1
        for state in new_states:
            if state not in current_state_names:
                row = {
                    'state_name': state,
                    'state_execution_order': curr_sort_order,
                    'is_dead_end_state': state in END_STATES,
                    'required': state in REQUIRED_STATES
                }

                RetirementState.objects.create(**row)
            else:
                RetirementState.objects.filter(state_name=state).update(state_execution_order=curr_sort_order)

            curr_sort_order += 10

        return states_to_create, states_remaining, states_to_delete

    def handle(self, *args, **options):
        """
        Execute the command.
        """
        dry_run = options['dry_run']

        if dry_run:
            print("--- Dry run, no changes will be made ---")

        new_states = settings.RETIREMENT_STATES
        self._validate_new_states(new_states)
        self._check_current_users()
        created, existed, deleted = self._delete_old_states_and_create_new(new_states, dry_run=dry_run)

        # Report
        print("States have been synchronized. Differences:")
        print(f"   Added: {created}")
        print(f"   Removed: {deleted}")
        print(f"   Remaining: {existed}")
        print("States updated successfully. Current states:")

        for state in RetirementState.objects.all():
            print(state)
