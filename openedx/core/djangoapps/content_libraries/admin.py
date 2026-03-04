"""
Admin site for content libraries
"""
from django.contrib import admin

from .models import ContentLibrary


@admin.register(ContentLibrary)
class ContentLibraryAdmin(admin.ModelAdmin):
    """
    Definition of django admin UI for Content Libraries
    """

    fields = (
        "library_key",
        "org",
        "slug",
        "allow_public_learning",
        "allow_public_read",
        "authorized_lti_configs",
    )
    list_display = ("slug", "org",)

    def get_readonly_fields(self, request, obj=None):
        """
        Ensure that 'slug' and 'uuid' cannot be edited after creation.
        """
        if obj:
            return ["library_key", "org", "slug"]
        else:
            return ["library_key", ]
