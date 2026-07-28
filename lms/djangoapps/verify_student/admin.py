"""
Admin site configurations for verify_student.
"""

from django.contrib import admin

from lms.djangoapps.verify_student.models import (
    ManualVerification,
    SoftwareSecurePhotoVerification,
    SSOVerification,
    SSPVerificationRetryConfig,
    VerificationAttempt,
)


@admin.register(SoftwareSecurePhotoVerification)
class SoftwareSecurePhotoVerificationAdmin(admin.ModelAdmin):
    """
    Admin for the SoftwareSecurePhotoVerification table.
    """
    list_display = ('id', 'user', 'status', 'receipt_id', 'submitted_at', 'updated_at',)
    raw_id_fields = ('user', 'reviewing_user', 'copy_id_photo_from',)
    search_fields = ('receipt_id', 'user__username',)


@admin.register(SSOVerification)
class SSOVerificationAdmin(admin.ModelAdmin):
    """
    Admin for the SSOVerification table.
    """
    list_display = ('id', 'user', 'status', 'identity_provider_slug', 'created_at', 'updated_at',)
    readonly_fields = ('user', 'identity_provider_slug', 'identity_provider_type',)
    raw_id_fields = ('user',)
    search_fields = ('user__username', 'identity_provider_slug',)


@admin.register(ManualVerification)
class ManualVerificationAdmin(admin.ModelAdmin):
    """
    Admin for the ManualVerification table.
    """
    list_display = ('id', 'user', 'status', 'reason', 'created_at', 'updated_at',)
    raw_id_fields = ('user',)
    search_fields = ('user__username', 'reason',)


@admin.register(SSPVerificationRetryConfig)
class SSPVerificationRetryAdmin(admin.ModelAdmin):
    """
    Admin for the SSPVerificationRetryConfig table.
    """
    pass  # pylint: disable=unnecessary-pass


@admin.register(VerificationAttempt)
class VerificationAttemptAdmin(admin.ModelAdmin):
    """
    Admin for the VerificationAttempt table.
    """
    list_display = ('id', 'user', 'name', 'status', 'expiration_datetime',)
    raw_id_fields = ('user',)
    search_fields = ('user__username', 'name',)
