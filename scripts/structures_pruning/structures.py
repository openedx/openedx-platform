#! /usr/bin/env python3
"""
Script to detect and prune old Structure documents from the "Split" Modulestore
MongoDB (edxapp.modulestore.structures by default). See docstring/help for the
"make_plan" and "prune" commands for more details.
"""

import logging
import sys
from os import path

import click
import click_log

# Add top-level project path to sys.path before importing scripts code
sys.path.append(path.abspath(path.join(path.dirname(__file__), '../..')))

from scripts.structures_pruning.utils.splitmongo import ChangePlan, SplitMongoBackend

# Add top-level module path to sys.path before importing tubular code.
# sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# from tubular.splitmongo import ChangePlan, SplitMongoBackend  # pylint: disable=wrong-import-position

LOG = logging.getLogger('structures')
click_log.basic_config(LOG)


@click.group()
@click.option(
    '--connection',
    default="mongodb://localhost:27017",
    help=(
            'Connection string to the target mongo database. This defaults to '
            'localhost without password (that will work against devstack). '
            'You may need to use urllib.parse.quote_plus() to percent-escape '
            'your username and password.'
    )
)
@click.option(
    '--database-name',
    default='edxapp',
    help='Name of the edX Mongo database containing the course structures to prune.'
)
@click.pass_context
def cli(ctx, connection, database_name):
    """
    Recover space on MongoDB for edx-platform by deleting unreachable,
    historical course content data. To use, first make a change plan with the
    "make_plan" command, and then execute that plan against the database with
    the "prune" command.

    This script provides logic to clean up old, unused course content data for
    the DraftVersioningModuleStore modulestore, more commonly referred to as the
    "Split Mongo" or "Split" modulestore (DraftVersioningModuleStore subclasses
    SplitMongoModuleStore). All courses and assets that have newer style locator
    keys use DraftVersioningModuleStore. These keys start with "course-v1:",
    "ccx-v1:", or "block-v1:". Studio authored content data for this modulestore
    is saved as immutable data structures. The edx-platform code never cleans up
    old data however, meaning there is an unbounded history of a course's
    content revisions stored in MongoDB.

    The older modulestore is DraftModuleStore, sometimes called "Old Mongo".
    This code does not address that modulestore in any way. That modulestore
    handles courses that use the old "/" separator, such as
    "MITx/6.002x/2012_Spring", as well as assets starting with "i4x://".
    """
    if ctx.obj is None:
        ctx.obj = dict()

    ctx.obj['BACKEND'] = SplitMongoBackend(connection, database_name)


@cli.command("make_plan")
@click_log.simple_verbosity_option(default='INFO')
@click.argument('plan_file', type=click.File('w'))
@click.option(
    '--details',
    type=click.File('w'),
    default=None,
    help="Name of file to write the human-readable details of the Change Plan."
)
@click.option(
    '--retain',
    default=2,
    type=click.IntRange(0, None),
    help=("The maximum number of intermediate structures to preserve for any "
          "single branch of an active version. This value does not include the "
          "active or original structures (those are always preserved). Defaults "
          "to 2. Put 0 here if you want to prune as much as possible.")
)
@click.option(
    '--delay',
    default=15000,
    type=click.IntRange(0, None),
    help=("Delay in milliseconds between queries to fetch structures from MongoDB "
          "during plan creation. Tune to adjust load on the database.")
)
@click.option(
    '--batch-size',
    default=10000,
    type=click.IntRange(1, None),
    help="How many Structures do we fetch at a time?"
)
@click.option(
    '--ignore-missing/--no-ignore-missing',
    default=False,
    help=("Force plan creation, even if missing structures are found. "
          "Should repair invalid ids by repointing to original. "
          "Review of plan highly recommended")
)
@click.option(
    '--dump-structures/--no-dump-structures',
    default=False,
    help="Dump all strucutres to stderr for debugging or recording state before cleanup."
)
@click.pass_context
def make_plan(ctx, plan_file, details, retain, delay, batch_size, ignore_missing, dump_structures):
    """
    Create a Change Plan JSON file describing the operations needed to prune the
    database. This command is read-only and does not alter the database.

    The Change Plan JSON is a dictionary with two keys:

    "delete" - A sorted array of Structure document IDs to delete. Since MongoDB
    object IDs are created in ascending order by timestamp, this means that the
    oldest documents come earlier in the list.

    "update_parents" - A list of [Structure ID, New Parent/Previous ID] pairs.
    This is used to re-link the oldest preserved Intermediate Structure back to
    the Original Structure, so that we don't leave the database in a state where
    a Structure's "previous_version" points to a deleted Structure.

    Specifying a --details file will generate a more verbose, human-readable
    text description of the Change Plan for verification purposes. The details
    file will only display Structures that are reachable from an Active Version,
    so any Structures that are "orphaned" as a result of partial runs of this
    script or Studio race conditions will not be reflected. That being said,
    orphaned Structures are detected and properly noted in the Change Plan JSON.
    """
    structures_graph = ctx.obj['BACKEND'].structures_graph(delay / 1000.0, batch_size)

    # This will create the details file as a side-effect, if specified.
    change_plan = ChangePlan.create(structures_graph, retain, ignore_missing, dump_structures, details)
    change_plan.dump(plan_file)


@cli.command()
@click_log.simple_verbosity_option(default='INFO')
@click.argument('plan_file', type=click.File('r'))
@click.option(
    '--delay',
    default=15000,
    type=click.IntRange(0, None),
    help=("Delay in milliseconds between batch deletions during pruning. Tune to "
          "adjust load on the database.")
)
@click.option(
    '--batch-size',
    default=1000,
    type=click.IntRange(1, None),
    help=("How many Structures do we delete at a time? Tune to adjust load on "
          "the database.")
)
@click.option(
    '--start',
    default=None,
    help=("Structure ID to start deleting from. Specifying a Structure ID that "
          "is not in the Change Plan is an error. Specifying a Structure ID that "
          "has already been deleted is NOT an error, so it's safe to re-run.")
)
@click.pass_context
def prune(ctx, plan_file, delay, batch_size, start):
    """
    Prune the MongoDB database according to a Change Plan file.

    This command tries to be as safe as possible. It executes parent updates
    before deletes, so an interruption at any point should be safe in that it
    won't leave the structure graphs in an inconsistent state. It should also
    be safe to resume pruning with the same Change Plan in the event of an
    interruption.

    It's also safe to run while Studio is still operating, though you should be
    careful to test and tweak the delay and batch_size options to throttle load
    on your database.
    """
    change_plan = ChangePlan.load(plan_file)
    if start is not None and start not in change_plan.delete:
        raise click.BadParameter(
            "{} is not in the Change Plan {}".format(
                start, click.format_filename(plan_file.name)
            ),
            param_hint='--start'
        )
    ctx.obj['BACKEND'].update(change_plan, delay / 1000.0, batch_size, start)


if __name__ == '__main__':
    # pylint doesn't grok click magic, but this is straight from their docs...
    cli(obj={})  # pylint: disable=no-value-for-parameter, unexpected-keyword-arg
