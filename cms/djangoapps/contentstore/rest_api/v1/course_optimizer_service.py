"""Runs of the course optimizer: link checks and updates of links to a previous course run."""

import json

from rest_framework.exceptions import NotFound
from user_tasks.models import UserTaskArtifact, UserTaskStatus

from cms.djangoapps.contentstore.core.course_optimizer_provider import (
    _latest_course_link_update_task_status,
    _latest_task_status,
    generate_broken_links_descriptor,
    get_course_link_update_data,
    sort_course_sections,
)
from cms.djangoapps.contentstore.tasks import check_broken_links, update_course_rerun_links
from cms.djangoapps.contentstore.toggles import enable_course_optimizer_check_prev_run_links

# The state of a resource no run of which the caller may see.
UNINITIATED = "Uninitiated"
# The stages the link update task reports while it runs.
SCANNING = "Scanning"
UPDATING = "Updating"

# Every state a link check or a link update can report. Scanning and Updating
# are reported by link updates only.
RUN_STATES = [
    UNINITIATED,
    UserTaskStatus.PENDING,
    UserTaskStatus.IN_PROGRESS,
    SCANNING,
    UPDATING,
    UserTaskStatus.SUCCEEDED,
    UserTaskStatus.FAILED,
    UserTaskStatus.CANCELED,
    UserTaskStatus.RETRYING,
]

ALL_LINKS = "all"
SINGLE_LINKS = "single"

# Reported for a link check that failed or was canceled with an error recorded.
# The recorded text can be an exception's, so it is not returned.
LINK_CHECK_ERROR_MESSAGE = "The link check could not be completed."
# Reported for each link an update run could not change, in place of the
# recorded reason, which can be an exception's text.
LINK_NOT_UPDATED_MESSAGE = "The link could not be updated."
PREVIOUS_RUN_LINK_UPDATES_DISABLED_MESSAGE = "Updating links to a previous course run is not enabled for this course."

_FAILED_STATES = (UserTaskStatus.FAILED, UserTaskStatus.CANCELED)


def start_link_check(course_key, user, language):
    """Queue a check of the links in the course, as ``user``; return the state of the queued run."""
    check_broken_links.delay(user.id, str(course_key), language)
    return {"status": UserTaskStatus.PENDING}


def link_check_status(request, course_key, full=False):
    """
    Return the latest link check of the course that the requester may see.

    Callers see their own runs; superusers see everyone's. With ``full``, the
    report of a run that succeeded is added under ``output`` (``None``
    otherwise).
    """
    run = _latest_task_status(request, str(course_key))
    if run is None:
        status = {"status": UNINITIATED, "created_at": None, "error": None}
    else:
        failed = run.state in _FAILED_STATES and (
            UserTaskArtifact.objects.filter(status=run, name="Error").exclude(text="").exists()
        )
        status = {
            "status": run.state,
            "created_at": run.created,
            "error": LINK_CHECK_ERROR_MESSAGE if failed else None,
        }
    if full:
        succeeded = run is not None and run.state == UserTaskStatus.SUCCEEDED
        status["output"] = _link_check_report(request.user, course_key, run) if succeeded else None
    return status


def _link_check_report(user, course_key, run):
    """The flagged links a run found, sections in course order."""
    artifact = UserTaskArtifact.objects.get(status=run, name="BrokenLinks")
    with artifact.file as file:
        links = json.loads(file.read())
    report = generate_broken_links_descriptor(links, user, course_key)
    return sort_course_sections(course_key, {"LinkCheckOutput": report})["LinkCheckOutput"]


def ensure_previous_run_link_updates_enabled(course_key):
    """Raise ``NotFound`` unless links to a previous run may be updated in the course."""
    if not enable_course_optimizer_check_prev_run_links(course_key):
        raise NotFound(PREVIOUS_RUN_LINK_UPDATES_DISABLED_MESSAGE)


def start_previous_run_link_update(course_key, user, action, links, language):
    """
    Queue an update of the course's links to its previous run, as ``user``.

    ``action`` is ``all`` (every such link the course has) or ``single`` (the
    ``links`` given). Returns the state of the queued run. Callers check
    ``ensure_previous_run_link_updates_enabled`` first.
    """
    update_course_rerun_links.delay(user.id, str(course_key), action, [dict(link) for link in links], language)
    return {"status": UserTaskStatus.PENDING}


def previous_run_link_update_status(request, course_key, full=False):
    """
    Return the latest update of links to the previous run that the requester may see.

    Visibility is as for link checks. With ``full``, the outcome of each link
    of a run that succeeded is added under ``results`` (empty otherwise).
    Callers check ``ensure_previous_run_link_updates_enabled`` first.
    """
    if not full:
        run = _latest_course_link_update_task_status(request, str(course_key))
        return {"status": UNINITIATED if run is None else run.state}
    data = get_course_link_update_data(request, str(course_key))
    return {
        "status": UNINITIATED if data["status"] == "uninitiated" else data["status"],
        "results": [_link_update_result(result) for result in data["results"]],
    }


def _link_update_result(result):
    """One link's outcome, with a fixed message in place of the recorded failure reason."""
    success = bool(result["success"])
    return {
        "id": result["id"],
        "type": result["type"],
        "original_url": result.get("original_url"),
        "new_url": result["new_url"],
        "success": success,
        "error_message": None if success else LINK_NOT_UPDATED_MESSAGE,
    }
