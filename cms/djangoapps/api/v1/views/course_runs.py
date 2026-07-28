"""HTTP endpoints for the Course Run API."""


from django.conf import settings
from django.http import Http404
from opaque_keys.edx.keys import CourseKey
from rest_framework import parsers, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from cms.djangoapps.contentstore.views.course import _accessible_courses_iter, get_course_and_check_access

from ..serializers.course_runs import (
    CourseCloneSerializer,
    CourseRunCreateSerializer,
    CourseRunImageSerializer,
    CourseRunRerunSerializer,
    CourseRunSerializer,
)


class CourseRunViewSet(viewsets.GenericViewSet):  # pylint: disable=missing-class-docstring
    lookup_value_regex = settings.COURSE_KEY_REGEX
    permission_classes = (permissions.IsAdminUser,)
    serializer_class = CourseRunSerializer
    queryset = []

    def get_object(self):
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field

        assert lookup_url_kwarg in self.kwargs, (
            'Expected view %s to be called with a URL keyword argument '  # noqa: UP031
            'named "%s". Fix your URL conf, or set the `.lookup_field` '
            'attribute on the view correctly.' %
            (self.__class__.__name__, lookup_url_kwarg)
        )

        course_run_key = CourseKey.from_string(self.kwargs[lookup_url_kwarg])
        course_run = get_course_and_check_access(course_run_key, self.request.user)
        if course_run:
            return course_run

        raise Http404

    def list(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        course_runs, __ = _accessible_courses_iter(request)
        page = self.paginate_queryset(list(course_runs))
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    def retrieve(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        course_run = self.get_object()
        serializer = self.get_serializer(course_run)
        return Response(serializer.data)

    def update(self, request, *args, **kwargs):  # pylint: disable=missing-function-docstring, unused-argument
        course_run = self.get_object()

        partial = kwargs.pop('partial', False)
        serializer = self.get_serializer(course_run, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        serializer = CourseRunCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=['post', 'put'],
        parser_classes=(parsers.FormParser, parsers.MultiPartParser,),
        serializer_class=CourseRunImageSerializer)
    def images(self, request, *args, **kwargs):  # pylint: disable=missing-function-docstring, unused-argument
        course_run = self.get_object()
        serializer = CourseRunImageSerializer(course_run, data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def rerun(self, request, *args, **kwargs):  # pylint: disable=missing-function-docstring, unused-argument
        course_run = self.get_object()
        serializer = CourseRunRerunSerializer(course_run, data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        new_course_run = serializer.save()
        serializer = self.get_serializer(new_course_run)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def clone(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        """
        **Use Case**

            This endpoint can be used for course cloning.

            Unlike reruns, cloning a course allows creating a copy of an existing
            course under a different organization name and with a different course
            name.

        **Example Request**

            POST /api/v1/course_runs/clone/ {
                "source_course_id": "course-v1:edX+DemoX+Demo_Course",
                "destination_course_id": "course-v1:newOrg+newDemoX+Demo_Course_Clone"
            }

            **POST Parameters**

                * source_course_id: a full course id of the course that will be
                  cloned. Has to be an id of an existing course.
                * destination_course_id: a full course id of the destination
                  course. The organization, course name and course run of the
                  new course will be determined from the provided id. Has to be
                  an id of a course that doesn't exist yet.

        **Response Values**

            If the request parameters are valid and a course has been cloned
            successfully, an HTTP 201 "Created" response is returned.

            If source course id and/or destination course id are invalid, or
            source course doesn't exist, or destination course already exist,
            an HTTP 400 "Bad Request" response is returned.

            If the user that is making the request doesn't have the access to
            either of the courses, an HTTP 401 "Unauthorized" response is
            returned.
        """
        serializer = CourseCloneSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        new_course_run = serializer.save()
        serializer = self.get_serializer(new_course_run)
        return Response({"message": "Course cloned successfully."}, status=status.HTTP_201_CREATED)
