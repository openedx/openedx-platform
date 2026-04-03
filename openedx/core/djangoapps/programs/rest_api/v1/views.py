"""Views for the Programs REST API v1."""

import logging
from typing import TYPE_CHECKING, Any

from django.db.models.query import EmptyQuerySet
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.student.api import get_course_enrollments
from openedx.core.djangoapps.programs.utils import (
    ProgramProgressMeter,
    get_certificates,
    get_industry_and_credit_pathways,
    get_program_and_course_data,
    get_program_urls,
)
from openedx.features.enterprise_support.api import enterprise_is_enabled, get_enterprise_course_enrollments

if TYPE_CHECKING:
    from django.contrib.auth.models import AnonymousUser, User  # pylint: disable=imported-auth-user
    from django.contrib.sites.models import Site
    from django.db.models.query import QuerySet
    from django.http import HttpRequest, HttpResponse

    from common.djangoapps.student.models import CourseEnrollment

logger = logging.getLogger(__name__)


class Programs(APIView):
    """Program endpoints"""

    permission_classes = (IsAuthenticated,)

    def get(self, request: "HttpRequest", enterprise_uuid: str = "") -> "HttpResponse":
        """For a learner, get list of enrolled programs with progress.
        If an enterprise UUID ias provided, filter out all non-enterprise enrollments for the learner.

        **Example Request**

            GET /api/dashboard/v1/programs/{enterprise_uuid}/

        **Parameters**

        * `enterprise_uuid`: UUID of an enterprise customer.

        **Example Response**

            [
                {
                    "uuid": "ff41a5eb-2a73-4933-8e80-a1c66068ed2c",
                    "title": "Demonstration Program",
                    "type": "MicroMasters",
                    "banner_image": {
                        "large": {
                            "url": "http://example.com/images/foo.large.jpg",
                            "width": 1440,
                            "height": 480
                        },
                        "medium": {
                            "url": "http://example.com/images/foo.medium.jpg",
                            "width": 726,
                            "height": 242
                        },
                        "small": {
                            "url": "http://example.com/images/foo.small.jpg",
                            "width": 435,
                            "height": 145
                        },
                        "x-small": {
                            "url": "http://example.com/images/foo.x-small.jpg",
                            "width": 348,
                            "height": 116
                        }
                    },
                    "authoring_organizations": [
                        {
                            "key": "example"
                        }
                    ],
                    "progress": {
                        "uuid": "ff41a5eb-2a73-4933-8e80-a1c66068ed2c",
                        "completed": 0,
                        "in_progress": 0,
                        "not_started": 2
                    }
                }
            ]
        """
        user: "AnonymousUser | User" = request.user

        if enterprise_uuid:
            enrollments = list(self._get_enterprise_course_enrollments(enterprise_uuid, user))
        else:
            enrollments = list(get_course_enrollments(user))

        # return empty reponse if no enrollments exists for a user
        if not enrollments:
            return Response([])

        meter = ProgramProgressMeter(
            request.site,
            user,
            enrollments=enrollments,
            mobile_only=False,
            include_course_entitlements=False,
        )
        engaged_programs = meter.engaged_programs
        progress = meter.progress(programs=engaged_programs)
        programs = self._extract_minimal_required_programs_data(engaged_programs)
        programs = self._combine_programs_data_and_progress(programs, progress)

        return Response(programs)

    def _combine_programs_data_and_progress(
        self,
        programs_data: list[dict | None],
        programs_progress: list[dict | None],
    ) -> list[dict | None]:
        """
        Return the combined program and progress data so that api clinet can easily process the data.
        """
        for program_data in programs_data:
            program_progress = next(
                (item for item in programs_progress if item["uuid"] == program_data["uuid"]),  # type: ignore[index]
                None,
            )
            program_data["progress"] = program_progress  # type: ignore[index]

        return programs_data

    def _extract_minimal_required_programs_data(self, programs_data: list[dict | None]) -> list[dict[str, Any] | None]:
        """
        Return only the minimal required program data need for program listing page.
        """

        def transform(key, value):
            transformers = {"authoring_organizations": transform_authoring_organizations}

            if key in transformers:
                return transformers[key](value)

            return value

        def transform_authoring_organizations(authoring_organizations) -> list[dict[str, Any]]:
            """
            Extract only the required data for `authoring_organizations` for a program
            """
            transformed_authoring_organizations = []
            for authoring_organization in authoring_organizations:
                transformed_authoring_organizations.append(
                    {
                        "key": authoring_organization["key"],
                        "logo_image_url": authoring_organization["logo_image_url"],
                    }
                )

            return transformed_authoring_organizations

        program_data_keys = [
            "uuid",
            "title",
            "type",
            "banner_image",
            "authoring_organizations",
        ]
        programs: list[dict[str, Any] | None] = []
        for program_data in programs_data:
            program = {}
            for program_data_key in program_data_keys:
                program[program_data_key] = transform(
                    program_data_key,
                    program_data[program_data_key],  # type: ignore[index]
                )

            programs.append(program)

        return programs

    @enterprise_is_enabled(otherwise=EmptyQuerySet)
    def _get_enterprise_course_enrollments(
        self, enterprise_uuid: str, user: "AnonymousUser | User"
    ) -> "QuerySet[CourseEnrollment]":
        """
        Return only enterprise enrollments for a user.
        """
        enterprise_enrollment_course_ids = (
            get_enterprise_course_enrollments(user)
            .filter(enterprise_customer_user__enterprise_customer__uuid=enterprise_uuid)
            .values_list("course_id", flat=True)
        )

        course_enrollments = get_course_enrollments(user, True, list(enterprise_enrollment_course_ids))

        return course_enrollments


class ProgramProgressDetailView(APIView):
    """Endpoints For Program Progress Meter"""

    permission_classes = (IsAuthenticated,)

    def get(self, request: "HttpRequest", program_uuid: str) -> "HttpResponse":
        """Retrieves progress details of a learner in a specified program.

        **Example Request**

            GET api/dashboard/v1/programs/{program_uuid}/progress_details/

        **Parameters**

        * `program_uuid`: A string representation of the uuid of the program.

        **Response Values**

        If the request for information about the program is successful, an HTTP 200 "OK" response
        is returned.

        The HTTP 200 response has the following values.

        * `urls`: Urls to enroll/purchase a course or view program record.

        * `program_data`: Holds meta information about the program.

        * `course_data`: Learner's progress details for all courses in the program (in-progress/remaining/completed).

        * `certificate_data`: Details about learner's certificates status for all courses in the program and the
            program itself.

        * `industry_pathways`: Industry pathways for the program, comes under additional credit opportunities.

        * `credit_pathways`: Credit pathways for the program, comes under additional credit opportunities.

        **Example Response**

            {
                "urls": {
                    "program_listing_url": "/dashboard/programs/",
                    "track_selection_url": "/course_modes/choose/",
                    "commerce_api_url": "/api/commerce/v1/baskets/",
                    "buy_button_url": "http://example.com/basket/add/?",
                    "program_record_url": "https://example.com/records/programs/8675309"
                },
                "program_data": {
                    "uuid": "a156a6e2-de91-4ce7-947a-888943e6b12a",
                    "title": "Demonstration Program",
                    "subtitle": "",
                    "type": "MicroMasters",
                    "status": "active",
                    "marketing_slug": "demo-program",
                    "marketing_url": "micromasters/demo-program",
                    "authoring_organizations": [],
                    "card_image_url": "http://example.com/asset-v1:DemoX+Demo_Course.jpg",
                    "is_program_eligible_for_one_click_purchase": false,
                    "pathway_ids": [
                        1,
                        2
                    ],
                    "is_learner_eligible_for_one_click_purchase": false,
                    "skus": ["AUD90210"],
                },
                "course_data": {
                    "uuid": "a156a6e2-de91-4ce7-947a-888943e6b12a",
                    "completed": [],
                    "in_progress": [],
                    "not_started": [
                        {
                            "key": "example+DemoX",
                            "uuid": "fe1a9ad4-a452-45cd-80e5-9babd3d43f96",
                            "title": "Demonstration Course",
                            "course_runs": [],
                            "entitlements": [],
                            "owners": [],
                            "image": "",
                            "short_description": "",
                            "type": "457f07ec-a78f-45b4-ba09-5fb176520d8a",
                        }
                    ],
                },
                "certificate_data": [{
                    "type": "course",
                    "title": "Demo Course",
                    'url': "/certificates/8675309",
                }],
                "industry_pathways": [
                    {
                        "id": 2,
                        "uuid": "1b8fadf1-f6aa-4282-94e3-325b922a027f",
                        "name": "Demo Industry Pathway",
                        "org_name": "example",
                        "email": "example@example.com",
                        "description": "Sample demo industry pathway",
                        "destination_url": "http://example.edu/online/pathways/example-methods",
                        "pathway_type": "industry",
                        "program_uuids": [
                            "a156a6e2-de91-4ce7-947a-888943e6b12a"
                        ]
                    }
                ],
                "credit_pathways": [
                    {
                        "id": 1,
                        "uuid": "86b9701a-61e6-48a2-92eb-70a824521c1f",
                        "name": "Demo Credit Pathway",
                        "org_name": "example",
                        "email": "example@example.com",
                        "description": "Sample demo credit pathway!",
                        "destination_url": "http://example.edu/online/pathways/example-thinking",
                        "pathway_type": "credit",
                        "program_uuids": [
                            "a156a6e2-de91-4ce7-947a-888943e6b12a"
                        ]
                    }
                ]
            }
        """
        user: "AnonymousUser | User" = request.user
        site: "Site" = request.site
        program_data, course_data = get_program_and_course_data(site, user, program_uuid)
        if not program_data:
            return Response(status=404, data={"error_code": "No program data available."})

        certificate_data = get_certificates(user, program_data)
        program_data.pop("courses")

        urls = get_program_urls(program_data)
        if not certificate_data:
            urls["program_record_url"] = None

        industry_pathways, credit_pathways = get_industry_and_credit_pathways(program_data, site)

        return Response(
            {
                "urls": urls,
                "program_data": program_data,
                "course_data": course_data,
                "certificate_data": certificate_data,
                "industry_pathways": industry_pathways,
                "credit_pathways": credit_pathways,
            }
        )
