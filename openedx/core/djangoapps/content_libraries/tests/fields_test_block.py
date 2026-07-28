"""
Block for testing variously scoped XBlock fields.
"""
import json

from web_fragments.fragment import Fragment
from webob import Response
from xblock import fields
from xblock.core import Scope, XBlock


class FieldsTestBlock(XBlock):
    """
    Block for testing variously scoped XBlock fields and XBlock handlers.

    This has only authored fields. See also UserStateTestBlock which has user fields.
    """
    BLOCK_TYPE = "fields-test"
    has_score = False

    display_name = fields.String(scope=Scope.settings, name='User State Test Block')
    setting_field = fields.String(scope=Scope.settings, name='A setting')
    content_field = fields.String(scope=Scope.content, name='A setting')

    @XBlock.json_handler
    def update_fields(self, data, suffix=None):  # pylint: disable=unused-argument
        """
        Update the authored fields of this block
        """
        self.display_name = data["display_name"]
        self.setting_field = data["setting_field"]
        self.content_field = data["content_field"]
        return {}

    @XBlock.handler
    def get_fields(self, request, suffix=None):  # pylint: disable=unused-argument
        """
        Get the various fields of this XBlock.
        """
        return Response(
            json.dumps({
                "display_name": self.display_name,
                "setting_field": self.setting_field,
                "content_field": self.content_field,
            }),
            content_type='application/json',
            charset='UTF-8',
        )

    def student_view(self, _context):
        """
        Return the student view.
        """
        fragment = Fragment()
        fragment.add_content(f'<h1>{self.display_name}</h1>\n')
        fragment.add_content(f'<p>SF: {self.setting_field}</p>\n')
        fragment.add_content(f'<p>CF: {self.content_field}</p>\n')
        handler_url = self.runtime.handler_url(self, 'get_fields')
        fragment.add_content(f'<p>handler URL: {handler_url}</p>\n')
        return fragment
