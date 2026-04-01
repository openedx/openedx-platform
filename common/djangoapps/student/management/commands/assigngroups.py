# lint-amnesty, pylint: disable=missing-module-docstring

import datetime
import json
import random
import sys
from textwrap import dedent
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand

from common.djangoapps.student.models import UserTestGroup

# Examples:
# python manage.py assigngroups summary_test:0.3,skip_summary_test:0.7 log.txt "Do previews of future materials help?"
# python manage.py assigngroups skip_capacitor:0.3,capacitor:0.7 log.txt "Do we show capacitor in linearity tutorial?"


def group_from_value(groups, v):
    """
    Given group: (('a',0.3),('b',0.4),('c',0.3)) And random value
    in [0,1], return the associated group (in the above case, return
    'a' if v<0.3, 'b' if 0.3<=v<0.7, and 'c' if v>0.7
    """
    curr_sum = 0
    for (group, p_value) in groups:
        curr_sum = curr_sum + p_value
        if curr_sum > v:
            return group
    return group  # For round-off errors  # lint-amnesty, pylint: disable=undefined-loop-variable


class Command(BaseCommand):  # lint-amnesty, pylint: disable=missing-class-docstring
    help = dedent("""
        Assign users to test groups. Takes a list of groups:
        a:0.3,b:0.4,c:0.3 file.txt "Testing something"
        Will assign each user to group a, b, or c with
        probability 0.3, 0.4, 0.3. Probabilities must
        add up to 1.

        Will log what happened to file.txt.
    """)

    def add_arguments(self, parser):
        parser.add_argument('group_and_score')
        parser.add_argument('log_name')
        parser.add_argument('description')

    def handle(self, *args, **options):
        # Extract groups from string
        group_strs = [x.split(':') for x in options['group_and_score'].split(',')]
        groups = [(group, float(value)) for group, value in group_strs]
        print("Groups", groups)

        # Confirm group probabilities add up to 1
        total = sum(zip(*groups)[1])  # lint-amnesty, pylint: disable=unsubscriptable-object
        print("Total:", total)
        if abs(total - 1) > 0.01:
            print("Total not 1")
            sys.exit(-1)

        # Confirm groups don't already exist
        for group in dict(groups):
            if UserTestGroup.objects.filter(name=group).count() != 0:
                print(group, "already exists!")
                sys.exit(-1)

        group_objects = {}

        f = open(options['log_name'], "a+")  # lint-amnesty, pylint: disable=consider-using-with

        # Create groups
        for group in dict(groups):
            utg = UserTestGroup()
            utg.name = group
            utg.description = json.dumps({"description": options['description']},  # lint-amnesty, pylint: disable=too-many-function-args
                                         {"time": datetime.datetime.now(ZoneInfo("UTC")).isoformat()})
            group_objects[group] = utg
            group_objects[group].save()

        # Assign groups
        users = list(User.objects.all())
        count = 0
        for user in users:
            if count % 1000 == 0:
                print(count)
            count = count + 1
            v = random.uniform(0, 1)
            group = group_from_value(groups, v)
            group_objects[group].users.add(user)
            f.write("Assigned user {name} ({id}) to {group}\n".format(
                name=user.username,
                id=user.id,
                group=group
            ).encode('utf-8'))

        # Save groups
        for group in group_objects:
            group_objects[group].save()
        f.close()
