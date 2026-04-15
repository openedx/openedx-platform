# lint-amnesty, pylint: disable=missing-module-docstring

import logging
import random

from django.utils.functional import cached_property
from lxml import etree
from web_fragments.fragment import Fragment
from xblock.fields import Integer, Scope

from xmodule.mako_block import MakoTemplateBlockBase
from xmodule.seq_block import SequenceMixin
from xmodule.x_module import STUDENT_VIEW, ResourceTemplates, XModuleMixin, XModuleToXBlockMixin
from xmodule.xml_block import XmlMixin

log = logging.getLogger('edx.' + __name__)


class RandomizeBlock(
    SequenceMixin,
    MakoTemplateBlockBase,
    XmlMixin,
    XModuleToXBlockMixin,
    ResourceTemplates,
    XModuleMixin,
):
    """
    Chooses a random child xblock. Chooses the same one every time for each student.

     Example:
     <randomize>
     <problem url_name="problem1" />
     <problem url_name="problem2" />
     <problem url_name="problem3" />
     </randomize>

    User notes:

      - If you're randomizing amongst graded blocks, each of them MUST be worth the same
        number of points.  Otherwise, the earth will be overrun by monsters from the
        deeps.  You have been warned.

    Technical notes:
      - There is more dark magic in this code than I'd like.  The whole varying-children +
        grading interaction is a tangle between super and subclasses of descriptors and
        blocks.
"""
    choice = Integer(help="Which random child was chosen", scope=Scope.user_state)

    resources_dir = None

    filename_extension = "xml"

    show_in_read_only_mode = True

    @cached_property
    def child(self):
        """ Return XBlock instance of selected choice """
        num_choices = len(self.get_children())

        if self.choice is not None and self.choice > num_choices:
            # Oops.  Children changed. Reset.
            self.choice = None

        if self.choice is None:
            # choose one based on the system seed, or randomly if that's not available
            if num_choices > 0:
                if self.runtime.seed is not None:
                    self.choice = self.runtime.seed % num_choices
                else:
                    self.choice = random.randrange(0, num_choices)

        if self.choice is None:
            return None
        child = self.get_children()[self.choice]

        if self.choice is not None:
            log.debug("children of randomize block (should be only 1): %s", child)

        return child

    def get_child_blocks(self):
        """
        For grading--return just the chosen child.
        """
        if self.child is None:
            return []

        return [self.child]

    def student_view(self, context):
        """
        The student view.
        """
        if self.child is None:
            # raise error instead?  In fact, could complain on block load...
            return Fragment(content="<div>Nothing to randomize between</div>")

        return self.child.render(STUDENT_VIEW, context)

    def get_html(self):
        return self.studio_view(None).content

    def get_icon_class(self):
        return self.child.get_icon_class() if self.child else 'other'

    def definition_to_xml(self, resource_fs):

        xml_object = etree.Element('randomize')
        for child in self.get_children():
            self.runtime.add_block_as_child_node(child, xml_object)
        return xml_object

    def has_dynamic_children(self):
        """
        Grading needs to know that only one of the children is actually "real".  This
        makes it use block.get_child_blocks().
        """
        return True
