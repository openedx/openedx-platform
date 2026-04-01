"""
The block_structure django app provides an extensible framework for caching
data of block structures from the modulestore.

Dual-Phase. The framework is meant to be used in 2 phases.

  * Collect Phase (for expensive and full-tree traversals) - In the
    first phase, the "collect" phase, any and all data from the
    modulestore should be collected and cached for later access to
    the block structure.  Instantiating any and all xBlocks in the block
    structure is also done at this phase, since that is also (currently)
    a costly operation.

    Any full tree traversals should also be done during this phase. For
    example, if data for a block depends on its parents, the traversal
    should happen during the collection phase and any required data
    for the block should be percolated down the tree and stored as
    aggregate values on the descendants.  This allows for faster and
    direct access to blocks in the Transform phase.

  * Transform Phase (for fast access to blocks) - In the second
    phase, the "transform" phase, only the previously collected and
    cached data should be accessed. There should be no access to the
    modulestore or instantiation of xBlocks in this phase.


To make this framework extensible, the Transformer and
Extensibility design patterns are used. This django app only
provides the underlying framework for Block Structure Transformers
and a Transformer Registry.  Clients are expected to provide actual
implementations of Transformers or add them to the extensible Registry.

Transformers. As inspired by
http://www.ccs.neu.edu/home/riccardo/courses/csu370-fa07/lect18.pdf,
a Block Structure Transformer takes in a block structure (or tree) and
manipulates the structure and the data of its blocks according to its
own requirements. Its output can then be used for further
transformations by other transformers down the pipeline.

Note: For performance and space optimization, our implementation
differs from the paper in that our transformers mutate the block
structure in-place rather than returning a modified copy of it.

Block Structure. The BlockStructure and its family of classes
provided with this framework are the base data types for accessing
and manipulating block structures. BlockStructures are constructed
using the BlockStructureFactory and then used as the currency across
Transformers.

Registry. Transformers are registered using the platform's
PluginManager (e.g., Stevedore). This is currently done by updating
pyproject.toml.  Only registered transformers are called during the Collect
Phase.  And only registered transformers can be used during the
Transform phase.  Exceptions to this rule are any nested transformers
that are contained within higher-order transformers - as long as the
higher-order transformers are registered and appropriately call the
contained transformers within them.

Note: A partial subset (as an ordered list) of the registered
transformers can be requested during the Transform phase, allowing
the client to manipulate exactly which transformers to call.

Links to Other Block Structure Related Documentation:

  * https://openedx.atlassian.net/wiki/spaces/AC/pages/154861855/Block+Structure+Cache+Invalidation+Proposal
  * https://openedx.atlassian.net/wiki/spaces/AC/pages/34734111/Course+Block+Transformers
  * https://openedx.atlassian.net/wiki/spaces/AC/pages/41910826/Course+Blocks+API+Storage+Cache+Requirements
"""
