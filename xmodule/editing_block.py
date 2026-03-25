"""Descriptors for XBlocks/Xmodules, that provide editing of atrributes"""


import logging  # noqa: I001

from xblock.core import XBlock
from xblock.fields import Boolean, Scope, String

from xmodule.mako_block import MakoTemplateBlockBase

log = logging.getLogger(__name__)


class EditingFields:
    """Contains specific template information (the raw data body)"""
    data = String(scope=Scope.content, default='')


class EditingMixin(EditingFields, MakoTemplateBlockBase):
    """
    Module that provides a raw editing view of its data and children.  It does not
    perform any validation on its definition---just passes it along to the browser.

    This class is intended to be used as a mixin.
    """
    resources_dir = None

    mako_template = "widgets/raw-edit.html"

    @property
    def non_editable_metadata_fields(self):
        """
        `data` should not be editable in the Studio settings editor.
        """
        non_editable_fields = super().non_editable_metadata_fields
        non_editable_fields.append(self.fields['data'])
        return non_editable_fields

    # cdodge: a little refactoring here, since we're basically doing the same thing
    # here as with our parent class, let's call into it to get the basic fields
    # set and then add our additional fields. Trying to keep it DRY.
    def get_context(self):
        _context = MakoTemplateBlockBase.get_context(self)
        # Add our specific template information (the raw data body)
        _context.update({'data': self.data})
        return _context

    @XBlock.json_handler
    def studio_submit(self, submissions, suffix=''):  # pylint: disable=unused-argument
        """
        Change the settings for this XBlock given by the Studio user
        """
        for field_name in self.editable_metadata_fields:
            if field_name in submissions and field_name in self.fields:
                field = self.fields[field_name]
                if isinstance(field, Boolean):
                    setattr(self, field_name, submissions[field_name] == 'True')
                else:
                    setattr(self, field_name, submissions[field_name])
        return {
            'result': 'success',
        }
