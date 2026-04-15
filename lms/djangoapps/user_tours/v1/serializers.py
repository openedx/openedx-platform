""" Serializer for UserTourView. """

from rest_framework import serializers

from lms.djangoapps.user_tours.models import UserDiscussionsTours, UserTour


class UserTourSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserTour
        fields = ['course_home_tour_status', 'show_courseware_tour']


class UserDiscussionsToursSerializer(serializers.ModelSerializer):
    """
    Serializer for UserDiscussionsTours model.
    """

    id = serializers.IntegerField(read_only=True)
    tour_name = serializers.CharField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = UserDiscussionsTours
        fields = ['id', 'tour_name', 'show_tour', 'user']

    def to_representation(self, instance):
        # Convert the status field to a boolean value
        instance.show_tour = bool(instance.show_tour)
        return super().to_representation(instance)
