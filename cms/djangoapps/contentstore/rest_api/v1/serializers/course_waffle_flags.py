"""
API Serializers for course waffle flags
"""

from rest_framework import serializers

from cms.djangoapps.contentstore import toggles


class CourseWaffleFlagsSerializer(serializers.Serializer):
    """
    Serializer for course waffle flags
    """

    use_new_home_page = serializers.SerializerMethodField()
    use_new_custom_pages = serializers.SerializerMethodField()
    use_new_schedule_details_page = serializers.SerializerMethodField()
    use_new_advanced_settings_page = serializers.SerializerMethodField()
    use_new_grading_page = serializers.SerializerMethodField()
    use_new_updates_page = serializers.SerializerMethodField()
    use_new_import_page = serializers.SerializerMethodField()
    use_new_export_page = serializers.SerializerMethodField()
    use_new_files_uploads_page = serializers.SerializerMethodField()
    use_new_video_uploads_page = serializers.SerializerMethodField()
    use_new_course_outline_page = serializers.SerializerMethodField()
    use_new_unit_page = serializers.SerializerMethodField()
    use_new_course_team_page = serializers.SerializerMethodField()
    use_new_certificates_page = serializers.SerializerMethodField()
    use_new_textbooks_page = serializers.SerializerMethodField()
    use_new_group_configurations_page = serializers.SerializerMethodField()
    enable_course_optimizer = serializers.SerializerMethodField()
    use_react_markdown_editor = serializers.SerializerMethodField()
    use_video_gallery_flow = serializers.SerializerMethodField()
    enable_course_optimizer_check_prev_run_links = serializers.SerializerMethodField()
    enable_course_optimizer_extended_checks = serializers.SerializerMethodField()
    enable_unit_expanded_view = serializers.SerializerMethodField()
    enable_outline_component_creation = serializers.SerializerMethodField()

    def get_course_key(self):
        """
        Retrieve the course_key from the context
        """
        return self.context.get("course_key")

    def get_use_new_home_page(self, obj):
        """
        Method to indicate whether we should use the new home page.

        This used to be based on a waffle flag but the flag is being removed so we
        default it to true for now until we can remove the need for it from the consumers
        of this serializer and the related APIs.

        See https://github.com/openedx/edx-platform/issues/37497
        """
        return True

    def get_use_new_custom_pages(self, obj):
        """
        Method to get the use_new_custom_pages switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_custom_pages(course_key)

    def get_use_new_schedule_details_page(self, obj):
        """
        Method to get the use_new_schedule_details_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_schedule_details_page(course_key)

    def get_use_new_advanced_settings_page(self, obj):
        """
        Method to get the use_new_advanced_settings_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_advanced_settings_page(course_key)

    def get_use_new_grading_page(self, obj):
        """
        Method to get the use_new_grading_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_grading_page(course_key)

    def get_use_new_updates_page(self, obj):
        """
        Method to get the use_new_updates_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_updates_page(course_key)

    def get_use_new_import_page(self, obj):
        """
        Method to get the use_new_import_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_import_page(course_key)

    def get_use_new_export_page(self, obj):
        """
        Method to get the use_new_export_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_export_page(course_key)

    def get_use_new_files_uploads_page(self, obj):
        """
        Method to get the use_new_files_uploads_page switch

        Always true, because the switch is being removed an the new experience
        should alawys be on.
        """
        return True

    def get_use_new_video_uploads_page(self, obj):
        """
        Method to get the use_new_video_uploads_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_video_uploads_page(course_key)

    def get_use_new_course_outline_page(self, obj):
        """
        Method to get the use_new_course_outline_page switch

        Always true, because the switch is being removed and the new experience
        should always be on. This function will be removed in
        https://github.com/openedx/edx-platform/issues/37497
        """
        return True

    def get_use_new_unit_page(self, obj):
        """
        Method to get the use_new_unit_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_unit_page(course_key)

    def get_use_new_course_team_page(self, obj):
        """
        Method to get the use_new_course_team_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_course_team_page(course_key)

    def get_use_new_certificates_page(self, obj):
        """
        Method to get the use_new_certificates_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_certificates_page(course_key)

    def get_use_new_textbooks_page(self, obj):
        """
        Method to get the use_new_textbooks_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_textbooks_page(course_key)

    def get_use_new_group_configurations_page(self, obj):
        """
        Method to get the use_new_group_configurations_page switch
        """
        course_key = self.get_course_key()
        return toggles.use_new_group_configurations_page(course_key)

    def get_enable_course_optimizer(self, obj):
        """
        Method to get the enable_course_optimizer waffle flag
        """
        course_key = self.get_course_key()
        return toggles.enable_course_optimizer(course_key)

    def get_use_react_markdown_editor(self, obj):
        """
        Method to get the use_react_markdown_editor waffle flag
        """
        course_key = self.get_course_key()
        return toggles.use_react_markdown_editor(course_key)

    def get_use_video_gallery_flow(self, obj):
        """
        Method to get the use_video_gallery_flow waffle flag
        """
        return toggles.use_video_gallery_flow()

    def get_enable_course_optimizer_check_prev_run_links(self, obj):
        """
        Method to get the enable_course_optimizer_check_prev_run_links waffle flag
        """
        course_key = self.get_course_key()
        return toggles.enable_course_optimizer_check_prev_run_links(course_key)

    def get_enable_course_optimizer_extended_checks(self, obj):
        """
        Method to get the enable_course_optimizer_extended_checks waffle flag
        """
        course_key = self.get_course_key()
        return toggles.enable_course_optimizer_extended_checks(course_key)

    def get_enable_unit_expanded_view(self, obj):
        """
        Method to get the enable_unit_expanded_view waffle flag
        """
        course_key = self.get_course_key()
        return toggles.enable_unit_expanded_view(course_key)

    def get_enable_outline_component_creation(self, obj):
        """
        Method to get the enable_outline_component_creation waffle flag
        """
        course_key = self.get_course_key()
        return toggles.enable_outline_component_creation(course_key)
