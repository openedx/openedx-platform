# pylint: disable=missing-module-docstring

import copy

from opaque_keys.edx.locator import DefinitionLocator


class DefinitionLazyLoader:
    """
    A placeholder to put into an xblock in place of its definition which
    when accessed knows how to get its content. Only useful if the containing
    object doesn't force access during init but waits until client wants the
    definition. Only works if the modulestore is a split mongo store.
    """
    def __init__(self, modulestore, course_key, block_type, definition_id, field_converter):
        """
        Simple placeholder for yet-to-be-fetched data
        :param modulestore: the pymongo db connection with the definitions
        :param definition_locator: the id of the record in the above to fetch
        """
        self.modulestore = modulestore
        self.course_key = course_key
        self.definition_locator = DefinitionLocator(block_type, definition_id)
        self.field_converter = field_converter

    def fetch(self):
        """
        Fetch the definition. Note, the caller should replace this lazy
        loader pointer with the result so as not to fetch more than once
        """
        # get_definition may return a cached value perhaps from another course or code path
        # so, we copy the result here so that updates don't cross-pollinate nor change the cached
        # value in such a way that we can't tell that the definition's been updated.
        definition = self.modulestore.get_definition(self.course_key, self.definition_locator.definition_id)
        return copy.deepcopy(definition)
