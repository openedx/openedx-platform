"""
Admin views for Staged Content and Clipboard
"""
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import StagedContent, StagedContentFile, UserClipboard


class StagedContentFileInline(admin.TabularInline):
    """ Inline admin UI for StagedContentFile """
    model = StagedContentFile
    readonly_fields = ('filename', 'md5_hash', 'source_key_str', 'data_file')


@admin.register(StagedContent)
class StagedContentAdmin(admin.ModelAdmin):
    """ Admin config for StagedContent """
    list_display = ('id', 'user', 'created', 'purpose', 'status', 'block_type', 'display_name', 'suggested_url_name')
    list_filter = ('purpose', 'status', 'block_type')
    search_fields = ('user__username', 'display_name', 'suggested_url_name')
    readonly_fields = ('id', 'user', 'created', 'purpose', 'status', 'block_type', 'olx', 'tags')
    inlines = (StagedContentFileInline, )


@admin.register(UserClipboard)
class UserClipboardAdmin(admin.ModelAdmin):
    """ Admin config for UserClipboard """
    list_display = ('user', 'content_link', 'source_usage_key', 'get_source_context_title')
    search_fields = ('user__username', 'source_usage_key', 'content__display_name')
    readonly_fields = ('source_context_key', 'get_source_context_title')

    @admin.display(description='Content')
    def content_link(self, obj):
        """ Display the StagedContent object as a link """
        url = reverse('admin:content_staging_stagedcontent_change', args=[obj.content.pk])
        return format_html('<a href="{}">{}</a>', url, obj.content)

    @admin.display(description='Source Context Title')
    def get_source_context_title(self, obj):
        return obj.get_source_context_title()
