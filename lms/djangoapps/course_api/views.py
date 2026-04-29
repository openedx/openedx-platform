"""
Course API Views
"""


from django.core.exceptions import ValidationError
from django.core.paginator import InvalidPage
from edx_django_utils.monitoring import function_trace
from edx_rest_framework_extensions.paginators import NamespacedPageNumberPagination
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.throttling import UserRateThrottle

from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes

from . import USE_RATE_LIMIT_2_FOR_COURSE_LIST_API, USE_RATE_LIMIT_10_FOR_COURSE_LIST_API
from .api import course_detail, list_course_keys, list_courses
from .forms import CourseDetailGetForm, CourseIdListGetForm, CourseListGetForm
from .serializers import CourseDetailSerializer, CourseKeySerializer, CourseSerializer


@view_auth_classes(is_authenticated=False)
class CourseDetailView(DeveloperErrorViewMixin, RetrieveAPIView):
    """
    **Use Cases**

        Request details for a course

    **Example Requests**

        GET /api/courses/v1/courses/{course_key}/

    **Response Values**

        Body consists of the following fields:

        * effort: A textual description of the weekly hours of effort expected
            in the course.
        * end: Date the course ends, in ISO 8601 notation
        * enrollment_end: Date enrollment ends, in ISO 8601 notation
        * enrollment_start: Date enrollment begins, in ISO 8601 notation
        * id: A unique identifier of the course; a serialized representation
            of the opaque key identifying the course.
        * media: An object that contains named media items.  Included here:
            * course_image: An image to show for the course.  Represented
              as an object with the following fields:
                * uri: The location of the image
        * name: Name of the course
        * number: Catalog number of the course
        * org: Name of the organization that owns the course
        * overview: A possibly verbose HTML textual description of the course.
            Note: this field is only included in the Course Detail view, not
            the Course List view.
        * short_description: A textual description of the course
        * start: Date the course begins, in ISO 8601 notation
        * start_display: Readably formatted start of the course
        * start_type: Hint describing how `start_display` is set. One of:
            * `"string"`: manually set by the course author
            * `"timestamp"`: generated from the `start` timestamp
            * `"empty"`: no start date is specified
        * pacing: Course pacing. Possible values: instructor, self
        * certificate_available_date (optional): Date the certificate will be available,
            in ISO 8601 notation if the `certificates.auto_certificate_generation`
            waffle switch is enabled

        Deprecated fields:

        * blocks_url: Used to fetch the course blocks
        * course_id: Course key (use 'id' instead)

    **Parameters:**

        username (optional):
            The username of the specified user for whom the course data
            is being accessed. The username is not only required if the API is
            requested by an Anonymous user.

    **Returns**

        * 200 on success with above fields.
        * 400 if an invalid parameter was sent or the username was not provided
          for an authenticated request.
        * 403 if a user who does not have permission to masquerade as
          another user specifies a username other than their own.
        * 404 if the course is not available or cannot be seen.

        Example response:

            {
                "blocks_url": "/api/courses/v1/blocks/?course_id=edX%2Fexample%2F2012_Fall",
                "media": {
                    "course_image": {
                        "uri": "/c4x/edX/example/asset/just_a_test.jpg",
                        "name": "Course Image"
                    }
                },
                "description": "An example course.",
                "end": "2015-09-19T18:00:00Z",
                "enrollment_end": "2015-07-15T00:00:00Z",
                "enrollment_start": "2015-06-15T00:00:00Z",
                "course_id": "edX/example/2012_Fall",
                "name": "Example Course",
                "number": "example",
                "org": "edX",
                "overview: "<p>A verbose description of the course.</p>"
                "start": "2015-07-17T12:00:00Z",
                "start_display": "July 17, 2015",
                "start_type": "timestamp",
                "pacing": "instructor",
                "certificate_available_date": "2015-08-14T00:00:00Z"
            }
    """

    serializer_class = CourseDetailSerializer

    def get_object(self):
        """
        Return the requested course object, if the user has appropriate
        permissions.
        """
        requested_params = self.request.query_params.copy()
        requested_params.update({'course_key': self.kwargs['course_key_string']})
        form = CourseDetailGetForm(requested_params, initial={'requesting_user': self.request.user})
        if not form.is_valid():
            raise ValidationError(form.errors)

        return course_detail(
            self.request,
            form.cleaned_data['username'],
            form.cleaned_data['course_key'],
        )


class CourseListUserThrottle(UserRateThrottle):
    """Limit the number of requests users can make to the course list API."""
    # The course list endpoint is likely being inefficient with how it's querying
    # various parts of the code and can take courseware down, it needs to be rate
    # limited until optimized. LEARNER-5527

    THROTTLE_RATES = {
        'user': '20/minute',
        'staff': '40/minute',
    }

    def check_for_switches(self):  # pylint: disable=missing-function-docstring
        if USE_RATE_LIMIT_2_FOR_COURSE_LIST_API.is_enabled():
            self.THROTTLE_RATES = {
                'user': '2/minute',
                'staff': '10/minute',
            }
        elif USE_RATE_LIMIT_10_FOR_COURSE_LIST_API.is_enabled():
            self.THROTTLE_RATES = {
                'user': '10/minute',
                'staff': '20/minute',
            }

    def allow_request(self, request, view):
        self.check_for_switches()
        # Use a special scope for staff to allow for a separate throttle rate
        user = request.user
        if user.is_authenticated and (user.is_staff or user.is_superuser):
            self.scope = 'staff'
            self.rate = self.get_rate()
            self.num_requests, self.duration = self.parse_rate(self.rate)

        return super().allow_request(request, view)


class LazyPageNumberPagination(NamespacedPageNumberPagination):
    """
    NamespacedPageNumberPagination that works with a LazySequence queryset.

    The paginator cache uses ``@cached_property`` to cache the property values for
    count and num_pages.  It assumes these won't change, but in the case of a
    LazySequence, its count gets updated as we move through it.  This class clears
    the cached property values before reporting results so they will be recalculated.

    """

    @function_trace('get_paginated_response')
    def get_paginated_response(self, data):
        # Clear the cached property values to recalculate the estimated count from the LazySequence
        del self.page.paginator.__dict__['count']
        del self.page.paginator.__dict__['num_pages']

        # Paginate queryset function is using cached number of pages and sometime after
        # deleting from cache when we recalculate number of pages are different and it raises
        # EmptyPage error while accessing the previous page link. So we are catching that exception
        # and raising 404. For more detail checkout PROD-1222
        page_number = self.request.query_params.get(self.page_query_param, 1)
        try:
            self.page.paginator.validate_number(page_number)
        except InvalidPage as exc:
            msg = self.invalid_page_message.format(
                page_number=page_number, message=str(exc)
            )
            self.page.number = self.page.paginator.num_pages
            raise NotFound(msg)  # pylint: disable=raise-missing-from  # noqa: B904

        return super().get_paginated_response(data)

    @function_trace('pagination_paginate_queryset')
    def paginate_queryset(self, queryset, request, view=None):
        """
        Paginate a queryset if required, either returning a
        page object, or `None` if pagination is not configured for this view.

        This is copied verbatim from upstream with added function traces.
        https://github.com/encode/django-rest-framework/blob/c6e24521dab27a7af8e8637a32b868ffa03dec2f/rest_framework/pagination.py#L191
        """
        with function_trace('pagination_paginate_queryset_get_page_size'):
            page_size = self.get_page_size(request)
            if not page_size:
                return None

        with function_trace('pagination_paginate_queryset_construct_paginator_instance'):
            paginator = self.django_paginator_class(queryset, page_size)

        with function_trace('pagination_paginate_queryset_get_page_number'):
            page_number = request.query_params.get(self.page_query_param, 1)

        if page_number in self.last_page_strings:
            page_number = paginator.num_pages

        with function_trace('pagination_paginate_queryset_get_page'):
            try:
                self.page = paginator.page(page_number)  # pylint: disable=attribute-defined-outside-init
            except InvalidPage as exc:
                msg = self.invalid_page_message.format(
                    page_number=page_number, message=str(exc)
                )
                raise NotFound(msg)  # pylint: disable=raise-missing-from  # noqa: B904

        with function_trace('pagination_paginate_queryset_get_num_pages'):
            if paginator.num_pages > 1 and self.template is not None:
                # The browsable API should display pagination controls.
                self.display_page_controls = True

        self.request = request  # pylint: disable=attribute-defined-outside-init

        with function_trace('pagination_paginate_queryset_listify_page'):
            page_list = list(self.page)

        return page_list


@view_auth_classes(is_authenticated=False)
class CourseListView(DeveloperErrorViewMixin, ListAPIView):
    """
    **Use Cases**

        Request information on all courses visible to the specified user.

    **Example Requests**

        GET /api/courses/v1/courses/
        POST /api/courses/v1/courses/

    **Response Values**

        Body comprises a list of objects as returned by `CourseDetailView`.

    **Parameters**

        search_term (optional):
            Search term to filter courses (used by ElasticSearch).

        username (optional):
            The username of the specified user whose visible courses we
            want to see. The username is not required only if the API is
            requested by an Anonymous user.

        org (optional):
            If specified, visible `CourseOverview` objects are filtered
            such that only those belonging to the organization with the
            provided org code (e.g., "HarvardX") are returned.
            Case-insensitive.

        permissions (optional):
            If specified, it filters visible `CourseOverview` objects by
            checking if each permission specified is granted for the username.
            Notice that Staff users are always granted permission to list any
            course.

        active_only (optional):
            If this boolean is specified, only the courses that have not ended or do not have any end
            date are returned. This is different from search_term because this filtering is done on
            CourseOverview and not ElasticSearch.

        course_keys (optional):
            If specified, it fetches the `CourseOverview` objects for the
            the specified course keys

        mobile_search (bool):
            Optional parameter that limits the number of returned courses
            to MOBILE_SEARCH_COURSE_LIMIT.

    **Returns**

        * 200 on success, with a list of course discovery objects as returned
          by `CourseDetailView`.
        * 400 if an invalid parameter was sent or the username was not provided
          for an authenticated request.
        * 403 if a user who does not have permission to masquerade as
          another user specifies a username other than their own.
        * 404 if the specified user does not exist, or the requesting user does
          not have permission to view their courses.

        Example response:

            [
              {
                "blocks_url": "/api/courses/v1/blocks/?course_id=edX%2Fexample%2F2012_Fall",
                "media": {
                  "course_image": {
                    "uri": "/c4x/edX/example/asset/just_a_test.jpg",
                    "name": "Course Image"
                  }
                },
                "description": "An example course.",
                "end": "2015-09-19T18:00:00Z",
                "enrollment_end": "2015-07-15T00:00:00Z",
                "enrollment_start": "2015-06-15T00:00:00Z",
                "course_id": "edX/example/2012_Fall",
                "name": "Example Course",
                "number": "example",
                "org": "edX",
                "start": "2015-07-17T12:00:00Z",
                "start_display": "July 17, 2015",
                "start_type": "timestamp"
              }
            ]
    **Note**

        The POST /api/courses/v1/courses/ reads `request.body` for parameters, allowing for
        larger input than the query string.
    """
    class CourseListPageNumberPagination(LazyPageNumberPagination):
        max_page_size = 100

    pagination_class = CourseListPageNumberPagination
    serializer_class = CourseSerializer
    throttle_classes = (CourseListUserThrottle,)

    def get_queryset(self):
        """
        Yield courses visible to the user.
        """
        form_data = self.request.query_params
        if self.request.method == 'POST':
            form_data = self.request.data
        form = CourseListGetForm(
            data=form_data,
            initial={'requesting_user': self.request.user}
        )
        if not form.is_valid():
            raise ValidationError(form.errors)
        return list_courses(
            self.request,
            form.cleaned_data['username'],
            org=form.cleaned_data['org'],
            filter_=form.cleaned_data['filter_'],
            search_term=form.cleaned_data['search_term'],
            permissions=form.cleaned_data['permissions'],
            active_only=form.cleaned_data.get('active_only', False),
            course_keys=form.cleaned_data['course_keys'],
            mobile_search=form.cleaned_data.get('mobile_search', False),
        )

    def post(self, request, *args, **kwargs):
        """
        POST courses filter.
        """
        return self.list(request, *args, **kwargs)


class CourseIdListUserThrottle(UserRateThrottle):
    """Limit the number of requests users can make to the course list id API."""

    THROTTLE_RATES = {
        'user': '20/minute',
        'staff': '40/minute',
    }

    def allow_request(self, request, view):
        # Use a special scope for staff to allow for a separate throttle rate
        user = request.user
        if user.is_authenticated and (user.is_staff or user.is_superuser):
            self.scope = 'staff'
            self.rate = self.get_rate()
            self.num_requests, self.duration = self.parse_rate(self.rate)

        return super().allow_request(request, view)


@view_auth_classes()
class CourseIdListView(DeveloperErrorViewMixin, ListAPIView):
    """
    **Use Cases**

        Request a list of course IDs for all courses the specified user can
        access based on the provided parameters.

    **Example Requests**

        GET /api/courses/v1/courses_ids/

    **Response Values**

        Body comprises a list of course ids and pagination details.

    **Parameters**

        username (optional):
            The username of the specified user whose visible courses we
            want to see.

        role (required):
            Course ids are filtered such that only those for which the
            user has the specified role are returned. Role can be "staff"
            or "instructor".
            Case-insensitive.

    **Returns**

        * 200 on success, with a list of course ids and pagination details
        * 400 if an invalid parameter was sent or the username was not provided
          for an authenticated request.
        * 403 if a user who does not have permission to masquerade as
          another user who specifies a username other than their own.
        * 404 if the specified user does not exist, or the requesting user does
          not have permission to view their courses.

        Example response:

            {
                "results":
                    [
                        "course-v1:edX+DemoX+Demo_Course"
                    ],
                "pagination": {
                    "previous": null,
                    "num_pages": 1,
                    "next": null,
                    "count": 1
                }
            }

    """
    class CourseIdListPageNumberPagination(LazyPageNumberPagination):
        max_page_size = 1000

    pagination_class = CourseIdListPageNumberPagination
    serializer_class = CourseKeySerializer
    throttle_classes = (CourseIdListUserThrottle,)

    @function_trace('get_queryset')
    def get_queryset(self):
        """
        Returns CourseKeys for courses which the user has the provided role.
        """
        form = CourseIdListGetForm(self.request.query_params, initial={'requesting_user': self.request.user})
        if not form.is_valid():
            raise ValidationError(form.errors)

        return list_course_keys(
            self.request,
            form.cleaned_data['username'],
            role=form.cleaned_data['role'],
        )

    @function_trace('paginate_queryset')
    def paginate_queryset(self, *args, **kwargs):
        """
        No-op passthrough function purely for function-tracing (monitoring)
        purposes.

        This should be called once per GET request.
        """
        return super().paginate_queryset(*args, **kwargs)

    @function_trace('get_paginated_response')
    def get_paginated_response(self, *args, **kwargs):
        """
        No-op passthrough function purely for function-tracing (monitoring)
        purposes.

        This should be called only when the response is paginated. Two pages
        means two GET requests and one function call per request. Otherwise, if
        the whole response fits in one page, this function never gets called.
        """
        return super().get_paginated_response(*args, **kwargs)

    @function_trace('filter_queryset')
    def filter_queryset(self, *args, **kwargs):
        """
        No-op passthrough function purely for function-tracing (monitoring)
        purposes.

        This should be called once per GET request.
        """
        return super().filter_queryset(*args, **kwargs)

    @function_trace('get_serializer')
    def get_serializer(self, *args, **kwargs):
        """
        No-op passthrough function purely for function-tracing (monitoring)
        purposes.

        This should be called once per GET request.
        """
        return super().get_serializer(*args, **kwargs)
