""" API for User Tours. """
from django.conf import settings
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from lms.djangoapps.user_tours.models import UserDiscussionsTours, UserTour
from lms.djangoapps.user_tours.toggles import USER_TOURS_DISABLED
from lms.djangoapps.user_tours.v1.serializers import UserDiscussionsToursSerializer, UserTourSerializer


class UserTourView(RetrieveUpdateAPIView):
    """
    Supports retrieving and patching the UserTour model

    **Example Requests**

        GET /api/user_tours/v1/{username}
        PATCH /api/user_tours/v1/{username}
    """
    permission_classes = (IsAuthenticated,)
    serializer_class = UserTourSerializer

    def get(self, request, username):  # pylint: disable=arguments-differ
        """
        Retrieve the User Tour for the given username.

        Allows staff users to retrieve any user's User Tour.

        Returns
            200 with the following fields:
                course_home_tour_status (str): one of UserTour.CourseHomeChoices
                show_courseware_tour (bool): indicates if courseware tour should be shown.

            400 if there is a not allowed request (requesting a user you don't have access to)
            401 if unauthorized request
            403 if tours are disabled
            404 if the UserTour does not exist (shouldn't happen, but safety first)
        """
        if USER_TOURS_DISABLED.is_enabled():
            return Response(status=status.HTTP_403_FORBIDDEN)

        if request.user.username != username and not request.user.is_staff:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        try:
            user_tour = UserTour.objects.get(user__username=username)
        # Should never really happen, but better safe than sorry.
        except UserTour.DoesNotExist as e:  # noqa: F841
            return Response(status=status.HTTP_404_NOT_FOUND)

        return Response(self.get_serializer_class()(user_tour).data, status=status.HTTP_200_OK)

    def patch(self, request, username):  # pylint: disable=arguments-differ
        """
        Patch the User Tour for the request.user.

        Supports updating the `course_home_tour_status` and `show_courseware_tour` fields.

        Returns:
            200 response if update was successful

            400 if update was unsuccessful or there was nothing to update
            401 if unauthorized request
            403 if tours are disabled
        """
        if USER_TOURS_DISABLED.is_enabled():
            return Response(status=status.HTTP_403_FORBIDDEN)

        if request.user.username != username:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = UserTour.objects.filter(user__username=username).update(**serializer.validated_data)
        if updated:
            return Response(status=status.HTTP_200_OK)
        else:
            return Response(status=status.HTTP_400_BAD_REQUEST)

    def put(self, *_args, **_kwargs):
        """ Unsupported method. """
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)


class UserDiscussionsToursView(APIView):
    """
    Supports retrieving and patching the UserDiscussionsTours model
    returns a list of available tours and their status

    **Example Requests**
        GET /api/user_tours/v1/discussions/
        PUT /api/user_tours/v1/discussions/{tour_id}

    **Example Response**:
    [
        {
            "id": 1,
            "tour_name": "discussions",
            "show_tour": true,
            "user": 1
        }
    ]
    """

    permission_classes = (IsAuthenticated,)

    def get(self, request, tour_id=None):
        """
        Return a list of all tours in the database.

        Parameters:
            request (Request): The request object
            tour_id (int): The ID of the tour to be retrieved.

        Returns:
            200: A list of tours, serialized using the UserDiscussionsToursSerializer
            [
                {
                    "id": 1,
                    "tour_name": "discussions",
                    "show_tour": true,
                    "user": 1
                }
             ]
            403 if the tours are disabled

        """
        if USER_TOURS_DISABLED.is_enabled():
            return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            with transaction.atomic():
                tours = UserDiscussionsTours.objects.filter(user=request.user)

                tours_to_create = []
                for tour_name in settings.AVAILABLE_DISCUSSION_TOURS:
                    if tour_name not in [tour.tour_name for tour in tours]:
                        tours_to_create.append(UserDiscussionsTours(
                            tour_name=tour_name,
                            user=request.user,
                            show_tour=True
                        ))

                UserDiscussionsTours.objects.bulk_create(tours_to_create)
                tours = UserDiscussionsTours.objects.filter(user=request.user)
                serializer = UserDiscussionsToursSerializer(tours, many=True)
                return Response(serializer.data)
        except IntegrityError:
            return Response(status=status.HTTP_409_CONFLICT)

    def put(self, request, tour_id):
        """
        Update an existing tour with the data in the request body.

        Parameters:
            request (Request): The request object
            tour_id (int): The ID of the tour to be updated.

        Returns:
            200: The updated tour, serialized using the UserDiscussionsToursSerializer
            404: If the tour does not exist
            403: If the user does not have permission to update the tour or the tours are disabled
            400: Validation error
        """
        if USER_TOURS_DISABLED.is_enabled():
            return Response(status=status.HTTP_403_FORBIDDEN)
        tour = get_object_or_404(UserDiscussionsTours, pk=tour_id)
        if tour.user != request.user:
            return Response(status=status.HTTP_403_FORBIDDEN)

        serializer = UserDiscussionsToursSerializer(tour, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
