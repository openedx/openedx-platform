"""
Provide names as exported by older mongo.py module
"""


from xmodule.modulestore.mongo.base import MongoKeyValueStore, MongoModuleStore  # noqa: F401

# Backwards compatibility for prod systems that refererence
# xmodule.modulestore.mongo.DraftMongoModuleStore
from xmodule.modulestore.mongo.draft import DraftModuleStore as DraftMongoModuleStore  # noqa: F401
