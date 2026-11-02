"""
An implementation of IdReader and IdGenerator that manages ids for the SplitMongo storage
mechanism.
"""


from opaque_keys.edx.locator import DefinitionLocator, LocalId

from xmodule.modulestore.split_mongo import BlockKey
from xmodule.x_module import AsideKeyGenerator, OpaqueKeyReader


# TODO: Migrate split_mongo to use this class for all key mapping/creation.
class SplitMongoIdManager(OpaqueKeyReader, AsideKeyGenerator):  # pylint: disable=abstract-method
    """
    An IdManager that knows how to retrieve the DefinitionLocator, given
    a usage_id and a :class:`.SplitModuleStoreRuntime`.
    """
    def __init__(self, runtime):
        self._runtime = runtime

    def get_definition_id(self, usage_id):
        if isinstance(usage_id.block_id, LocalId):
            # a LocalId indicates that this block hasn't been persisted yet, and is instead stored
            # in-memory in the local_modules dictionary.
            return self._runtime.local_modules[usage_id].scope_ids.def_id
        else:
            block_key = BlockKey.from_usage_key(usage_id)
            module_data = self._runtime.get_module_data(block_key, usage_id.course_key)

            if module_data.definition is not None:
                return DefinitionLocator(usage_id.block_type, module_data.definition)
            else:
                raise ValueError("All non-local blocks should have a definition specified")
