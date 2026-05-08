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
        "learning_package",
        "allow_public_learning",
        "allow_public_read",
    )
    list_display = ("slug", "org",)

    def get_readonly_fields(self, request, obj=None):
        """
        Ensure that 'slug' and 'uuid' cannot be edited after creation.
        """
        always_ro_fields = ["library_key", "learning_package"]
        if obj:
            return [*always_ro_fields, "org", "slug"]
        return always_ro_fields
