"""
Methods to interact with the Jenkins API to perform various tasks.
"""

import logging
import math
import os.path
import shutil
import sys

import backoff
from jenkinsapi.custom_exceptions import JenkinsAPIException
from jenkinsapi.jenkins import Jenkins
from jenkinsapi.utils.crumb_requester import CrumbRequester
from requests.exceptions import HTTPError

from scripts.user_retirement.utils.exception import BackendError

logging.basicConfig(stream=sys.stdout, level=logging.INFO)
LOG = logging.getLogger(__name__)


def _recreate_directory(directory):
    """
    Deletes an existing directory recursively (if exists) and (re-)creates it.
    """
    if os.path.exists(directory):
        shutil.rmtree(directory)
    os.mkdir(directory)


def export_learner_job_properties(learners, directory):
    """
    Creates a Jenkins properties file for each learner in order to make
    a retirement slave job for each learner.

    Args:
        learners (list of dicts): List of learners for which to create properties files.
        directory (str): Directory in which to create the properties files.
    """
    _recreate_directory(directory)

    for learner in learners:
        learner_name = learner['original_username'].lower()
        filename = os.path.join(directory, 'learner_retire_{}'.format(learner_name))  # noqa: UP032
        with open(filename, 'w') as learner_prop_file:
            learner_prop_file.write('RETIREMENT_USERNAME={}\n'.format(learner['original_username']))


def _poll_giveup(data):
    u""" Raise an error when the polling tries are exceeded."""  # noqa: UP025
    orig_args = data.get(u'args')  # noqa: UP025
    # The Build object was the only parameter to the original method call,
    # and so it's the first and only item in the args.
    build = orig_args[0]
    msg = u'Timed out waiting for build {} to finish.'.format(build.name)  # noqa: UP025, UP032
    raise BackendError(msg)


def _backoff_timeout(timeout, base=2, factor=1):
    u"""
    Return a tuple of (wait_gen, max_tries) so that backoff will only try up to `timeout` seconds.

    |timeout (s)|max attempts|wait durations        |
    |----------:|-----------:|---------------------:|
    |1          |2           |1                     |
    |5          |4           |1, 2, 2               |
    |10         |5           |1, 2, 4, 3            |
    |30         |6           |1, 2, 4, 8, 13        |
    |60         |8           |1, 2, 4, 8, 16, 32, 37|
    |300        |10          |1, 2, 4, 8, 16, 32, 64|
    |           |            |128, 44               |
    |600        |11          |1, 2, 4, 8, 16, 32, 64|
    |           |            |128, 256, 89          |
    |3600       |13          |1, 2, 4, 8, 16, 32, 64|
    |           |            |128, 256, 512, 1024,  |
    |           |            |1553                  |

    """  # noqa: UP025
    # Total duration of sum(factor * base ** n for n in range(K)) = factor*(base**K - 1)/(base - 1),
    # where K is the number of retries, or max_tries - 1 (since the first try doesn't require a wait)
    #
    # Solving for K, K = log(timeout * (base - 1) / factor + 1, base)
    #
    # Using the next smallest integer K will give us a number of elements from
    # the exponential sequence to take and still be less than the timeout.
    tries = int(math.log(timeout * (base - 1) / factor + 1, base))

    remainder = timeout - (factor * (base ** tries - 1)) / (base - 1)

    def expo():
        u"""Compute an exponential backoff wait period, but capped to an expected max timeout"""  # noqa: UP025
        # pylint: disable=invalid-name
        n = 0
        while True:
            a = factor * base ** n
            if n >= tries:
                yield remainder
            else:
                yield a
                n += 1

    # tries tells us the largest standard wait using the standard progression (before being capped)
    # tries + 1 because backoff waits one fewer times than max_tries (the first attempt has no wait time).
    # If a remainder, then we need to make one last attempt to get the target timeout (so tries + 2)
    if remainder == 0:
        return expo, tries + 1
    else:
        return expo, tries + 2


def trigger_build(base_url, user_name, user_token, job_name, job_token,
                  job_cause=None, job_params=None, timeout=60 * 30):
    u"""
    Trigger a jenkins job/project (note that jenkins uses these terms interchangeably)

    Args:
        base_url (str): The base URL for the jenkins server, e.g. https://test-jenkins.testeng.edx.org
        user_name (str): The jenkins username
        user_token (str): API token for the user. Available at {base_url}/user/{user_name)/configure
        job_name (str): The Jenkins job name, e.g. test-project
        job_token (str): Jobs must be configured with the option "Trigger builds remotely" selected.
            Under this option, you must provide an authorization token (configured in the job)
            in the form of a string so that only those who know it would be able to remotely
            trigger this project's builds.
        job_cause (str): Text that will be included in the recorded build cause
        job_params (set of tuples): Parameter names and their values to pass to the job
        timeout (int): The maximum number of seconds to wait for the jenkins build to complete (measured
            from when the job is triggered.)

    Returns:
        A the status of the build that was triggered

    Raises:
        BackendError: if the Jenkins job could not be triggered successfully
    """  # noqa: UP025

    @backoff.on_predicate(
        backoff.constant,
        interval=60,
        max_tries=timeout / 60 + 1,
        on_giveup=_poll_giveup,
        # We aren't worried about concurrent access, so turn off jitter
        jitter=None,
    )
    def poll_build_for_result(build):
        u"""
        Poll for the build running, with exponential backoff, capped to ``timeout`` seconds.
        The on_predicate decorator is used to retry when the return value
        of the target function is True.
        """  # noqa: UP025
        return not build.is_running()

    # Create a dict with key/value pairs from the job_params
    # that were passed in like this:  --param FOO bar --param BAZ biz
    # These will get passed to the job as string parameters like this:
    # {u'FOO': u'bar', u'BAX': u'biz'}
    request_params = {}
    for param in job_params:
        request_params[param[0]] = param[1]

    # Contact jenkins, log in, and get the base data on the system.
    try:
        crumb_requester = CrumbRequester(
            baseurl=base_url, username=user_name, password=user_token,
            ssl_verify=True
        )
        jenkins = Jenkins(
            base_url, username=user_name, password=user_token,
            requester=crumb_requester
        )
    except (JenkinsAPIException, HTTPError) as err:
        raise BackendError(str(err))  # noqa: B904

    if not jenkins.has_job(job_name):
        msg = u'Job not found: {}.'.format(job_name)  # noqa: UP025, UP032
        msg += u' Verify that you have permissions for the job and double check the spelling of its name.'  # noqa: UP025
        raise BackendError(msg)

    # This will start the job and will return a QueueItem object which can be used to get build results
    job = jenkins[job_name]
    queue_item = job.invoke(securitytoken=job_token, build_params=request_params, cause=job_cause)
    LOG.info(u'Added item to jenkins. Server: {} Job: {} '.format(  # noqa: UP025, UP032
        jenkins.base_server_url(), queue_item
    ))

    # Block this script until we are through the queue and the job has begun to build.
    queue_item.block_until_building()
    build = queue_item.get_build()
    LOG.info(u'Created build {}'.format(build))  # noqa: UP025, UP032
    LOG.info(u'See {}'.format(build.baseurl))  # noqa: UP025, UP032

    # Now block until you get a result back from the build.
    poll_build_for_result(build)

    # Update the build's internal state, so that the final status is available
    build.poll()

    status = build.get_status()
    LOG.info(u'Build status: {status}'.format(status=status))  # noqa: UP025, UP032
    return status
