"""
Tasks that operate on course certificates for a user
"""

from difflib import unified_diff
from logging import getLogger
from typing import Any, Dict, List  # noqa: UP035

from celery import shared_task
from celery_utils.persist_on_failure import LoggedPersistOnFailureTask, LoggedTask
from django.contrib.auth import get_user_model
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.generation import generate_course_certificate
from lms.djangoapps.certificates.models import CertificateTemplate

log = getLogger(__name__)
User = get_user_model()

# Certificate generation is delayed in case the caller is still completing their changes
# (for example a certificate regeneration reacting to a post save rather than post commit signal)
CERTIFICATE_DELAY_SECONDS = 2


@shared_task(
    base=LoggedPersistOnFailureTask, bind=True, default_retry_delay=30, max_retries=2
)
@set_code_owner_attribute
def generate_certificate(self, **kwargs):  # pylint: disable=unused-argument
    """
    Generates a certificate for a single user.

    kwargs:
        - student: The student for whom to generate a certificate. Required.
        - course_key: The course key for the course that the student is
            receiving a certificate in. Required.
        - status: Certificate status (value from the CertificateStatuses model). Defaults to 'downloadable'.
        - enrollment_mode: User's enrollment mode (ex. verified). Required.
        - course_grade: User's course grade. Defaults to ''.
        - generation_mode: Used when emitting an event. Options are "self" (implying the user generated the cert
            themself) and "batch" for everything else. Defaults to 'batch'.
    """
    student = User.objects.get(id=kwargs.pop("student"))
    course_key = CourseKey.from_string(kwargs.pop("course_key"))
    status = kwargs.pop("status", CertificateStatuses.downloadable)
    enrollment_mode = kwargs.pop("enrollment_mode")
    course_grade = kwargs.pop("course_grade", "")
    generation_mode = kwargs.pop("generation_mode", "batch")

    generate_course_certificate(
        user=student,
        course_key=course_key,
        status=status,
        enrollment_mode=enrollment_mode,
        course_grade=course_grade,
        generation_mode=generation_mode,
    )


@shared_task(base=LoggedTask, ignore_result=True)
@set_code_owner_attribute
def handle_modify_cert_template(options: Dict[str, Any]) -> None:  # noqa: UP006
    """
    Celery task to handle the modify_cert_template management command.

    Args:
        old_text (string): Text in the template of which the first instance should be changed
        new_text (string): Replacement text for old_text
        template_ids (list[string]): List of template IDs for this run.
        dry_run (boolean): Don't do the work, just report the changes that would happen
    """

    template_ids = options["templates"]
    if not template_ids:
        template_ids = []

    log.info(
        "[modify_cert_template] Attempting to modify {num} templates".format(  # noqa: UP032
            num=len(template_ids)
        )
    )

    templates_changed = get_changed_cert_templates(options)
    for template in templates_changed:
        template.save()


def get_changed_cert_templates(options: Dict[str, Any]) -> List[CertificateTemplate]:  # noqa: UP006
    """
    Loop through the templates and return instances with changed template text.

    Args:
        old_text (string): Text in the template of which the first instance should be changed
        new_text (string): Replacement text for old_text
        template_ids (list[string]): List of template IDs for this run.
        dry_run (boolean): Don't do the work, just report the changes that would happen
    """
    template_ids = options["templates"]
    if not template_ids:
        template_ids = []

    log.info(
        "[modify_cert_template] Attempting to modify {num} templates".format(  # noqa: UP032
            num=len(template_ids)
        )
    )
    dry_run = options.get("dry_run", None)
    templates_changed = []

    for template_id in template_ids:
        template = None
        try:
            template = CertificateTemplate.objects.get(id=template_id)
        except CertificateTemplate.DoesNotExist:
            log.warning(f"Template {template_id} could not be found")
        if template is not None:
            log.info(
                "[modify_cert_template] Calling for template {template_id} : {name}".format(  # noqa: UP032
                    template_id=template_id, name=template.description
                )
            )
            new_template = template.template.replace(
                options["old_text"], options["new_text"], 1
            )
            if template.template == new_template:
                log.info(
                    "[modify_cert_template] No changes to {template_id}".format(  # noqa: UP032
                        template_id=template_id
                    )
                )
            else:
                if not dry_run:
                    log.info(
                        "[modify_cert_template] Modifying template {template} ({description})".format(  # noqa: UP032
                            template=template_id,
                            description=template.description,
                        )
                    )
                    template.template = new_template
                    templates_changed.append(template)
                else:
                    log.info(
                        "DRY-RUN: Not making the following template change to {id}.".format(  # noqa: UP032
                            id=template_id
                        )
                    )
                    log.info(
                        "\n".join(
                            unified_diff(
                                template.template.splitlines(),
                                new_template.splitlines(),
                                lineterm="",
                                fromfile="old_template",
                                tofile="new_template",
                            )
                        ),
                    )
    log.info(
        "[modify_cert_template] Modified {num} templates".format(  # noqa: UP032
            num=len(templates_changed)
        )
    )

    return templates_changed
