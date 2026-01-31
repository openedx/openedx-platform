"""
Django admin page for the Agreements app
"""

from django.contrib import admin
from openedx.core.djangoapps.agreements.models import IntegritySignature, UserAgreement
from openedx.core.djangoapps.agreements.models import LTIPIITool
from openedx.core.djangoapps.agreements.models import LTIPIISignature
from openedx.core.djangoapps.agreements.models import ProctoringPIISignature


class IntegritySignatureAdmin(admin.ModelAdmin):
    """
    Admin for the IntegritySignature Model
    """
    list_display = ('user', 'course_key', 'created', 'modified')
    readonly_fields = ('user', 'course_key', 'created', 'modified')
    search_fields = ('user__username', 'course_key',)
    ordering = ['-modified']

    class Meta:
        model = IntegritySignature


admin.site.register(IntegritySignature, IntegritySignatureAdmin)


class LTIPIIToolAdmin(admin.ModelAdmin):
    """
    Admin for the LTIPIITool Model
    """
    readonly_fields = ('course_key', 'lti_tools', 'lti_tools_hash')

    class Meta:
        model = LTIPIITool


admin.site.register(LTIPIITool, LTIPIIToolAdmin)


class LTIPIISignatureAdmin(admin.ModelAdmin):
    """
    Admin for the LTIPIISignature Model
    """
    readonly_fields = ('user', 'course_key', 'lti_tools', 'lti_tools_hash')

    class Meta:
        model = LTIPIISignature


admin.site.register(LTIPIISignature, LTIPIISignatureAdmin)


class ProctoringPIISignatureAdmin(admin.ModelAdmin):
    """
    Admin for the ProctoringPIISignature Model
    """
    readonly_fields = ('user', 'course_key', 'proctoring_provider')

    class Meta:
        model = ProctoringPIISignature


admin.site.register(ProctoringPIISignature, ProctoringPIISignatureAdmin)


class UserAgreementAdmin(admin.ModelAdmin):
    """
    Admin for the UserAgreement Model
    """

    list_display = ('type', 'name', 'url', 'created', 'updated')

    class Meta:
        model = UserAgreement


admin.site.register(UserAgreement, UserAgreementAdmin)
