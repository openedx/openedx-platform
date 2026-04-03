"""
Tests for testing the modulestore settings migration code.
"""


import copy
from unittest import TestCase

import ddt
import pytest

from openedx.core.lib.tempdir import mkdtemp_clean
from xmodule.modulestore.modulestore_settings import (
    convert_module_store_setting_if_needed,
    get_mixed_stores,
    update_module_store_settings,
)


@ddt.ddt
class ModuleStoreSettingsMigration(TestCase):
    """
    Tests for the migration code for the module store settings
    """

    OLD_CONFIG = {
        "default": {
            "ENGINE": "xmodule.modulestore.xml.XMLModuleStore",
            "OPTIONS": {
                "data_dir": "directory",
                "default_class": "xmodule.hidden_block.HiddenBlock",
            },
            "DOC_STORE_CONFIG": {},
        }
    }

    OLD_CONFIG_WITH_DIRECT_MONGO = {
        "default": {
            "ENGINE": "xmodule.modulestore.mongo.MongoModuleStore",
            "OPTIONS": {
                "collection": "modulestore",
                "db": "edxapp",
                "default_class": "xmodule.hidden_block.HiddenBlock",
                "fs_root": mkdtemp_clean(),
                "host": "localhost",
                "password": "password",
                "port": 27017,
                "render_template": "common.djangoapps.edxmako.shortcuts.render_to_string",
                "user": "edxapp"
            },
            "DOC_STORE_CONFIG": {},
        }
    }

    OLD_MIXED_CONFIG_WITH_DICT = {
        "default": {
            "ENGINE": "xmodule.modulestore.mixed.MixedModuleStore",
            "OPTIONS": {
                "mappings": {},
                "stores": {
                    "an_old_mongo_store": {
                        "DOC_STORE_CONFIG": {},
                        "ENGINE": "xmodule.modulestore.mongo.MongoModuleStore",
                        "OPTIONS": {
                            "collection": "modulestore",
                            "db": "test",
                            "default_class": "xmodule.hidden_block.HiddenBlock",
                        }
                    },
                    "default": {
                        "ENGINE": "the_default_store",
                        "OPTIONS": {
                            "option1": "value1",
                            "option2": "value2"
                        },
                        "DOC_STORE_CONFIG": {}
                    },
                    "xml": {
                        "ENGINE": "xmodule.modulestore.xml.XMLModuleStore",
                        "OPTIONS": {
                            "data_dir": "directory",
                            "default_class": "xmodule.hidden_block.HiddenBlock"
                        },
                        "DOC_STORE_CONFIG": {}
                    }
                }
            }
        }
    }

    ALREADY_UPDATED_MIXED_CONFIG = {
        'default': {
            'ENGINE': 'xmodule.modulestore.mixed.MixedModuleStore',
            'OPTIONS': {
                'mappings': {},
                'stores': [
                    {
                        'NAME': 'split',
                        'ENGINE': 'xmodule.modulestore.split_mongo.split_draft.DraftVersioningModuleStore',
                        'DOC_STORE_CONFIG': {},
                        'OPTIONS': {
                            'default_class': 'xmodule.hidden_block.HiddenBlock',
                            'fs_root': "fs_root",
                            'render_template': 'common.djangoapps.edxmako.shortcuts.render_to_string',
                        }
                    },
                    {
                        'NAME': 'draft',
                        'ENGINE': 'xmodule.modulestore.mongo.draft.DraftModuleStore',
                        'DOC_STORE_CONFIG': {},
                        'OPTIONS': {
                            'default_class': 'xmodule.hidden_block.HiddenBlock',
                            'fs_root': "fs_root",
                            'render_template': 'common.djangoapps.edxmako.shortcuts.render_to_string',
                        }
                    },
                ]
            }
        }
    }

    def assertStoreValuesEqual(self, store_setting1, store_setting2):
        """
        Tests whether the fields in the given store_settings are equal.
        """
        store_fields = ["OPTIONS", "DOC_STORE_CONFIG"]
        for field in store_fields:
            assert store_setting1[field] == store_setting2[field]

    def assertMigrated(self, old_setting):
        """
        Migrates the given setting and checks whether it correctly converted
        to an ordered list of stores within Mixed.
        """
        # pass a copy of the old setting since the migration modifies the given setting
        new_mixed_setting = convert_module_store_setting_if_needed(copy.deepcopy(old_setting))

        # check whether the configuration is encapsulated within Mixed.
        assert new_mixed_setting['default']['ENGINE'] == 'xmodule.modulestore.mixed.MixedModuleStore'

        # check whether the stores are in an ordered list
        new_stores = get_mixed_stores(new_mixed_setting)
        assert isinstance(new_stores, list)

        return new_mixed_setting, new_stores[0]

    def is_split_configured(self, mixed_setting):
        """
        Tests whether the split module store is configured in the given setting.
        """
        stores = get_mixed_stores(mixed_setting)
        split_settings = [store for store in stores if store['ENGINE'].endswith('.DraftVersioningModuleStore')]
        if len(split_settings):  # lint-amnesty, pylint: disable=len-as-condition
            # there should only be one setting for split
            assert len(split_settings) == 1
            # verify name
            assert split_settings[0]['NAME'] == 'split'
            # verify split config settings equal those of mongo
            self.assertStoreValuesEqual(
                split_settings[0],
                next((store for store in stores if 'DraftModuleStore' in store['ENGINE']), None)
            )
        return len(split_settings) > 0

    def test_convert_into_mixed(self):
        old_setting = self.OLD_CONFIG
        with pytest.warns(DeprecationWarning, match="Direct access to a modulestore is deprecated"):
            new_mixed_setting, new_default_store_setting = self.assertMigrated(old_setting)
        self.assertStoreValuesEqual(new_default_store_setting, old_setting["default"])
        assert new_default_store_setting['ENGINE'] == old_setting['default']['ENGINE']
        assert not self.is_split_configured(new_mixed_setting)

    def test_convert_from_old_mongo_to_draft_store(self):
        old_setting = self.OLD_CONFIG_WITH_DIRECT_MONGO
        with pytest.warns(DeprecationWarning, match="MongoModuleStore is deprecated"):
            new_mixed_setting, new_default_store_setting = self.assertMigrated(old_setting)
        self.assertStoreValuesEqual(new_default_store_setting, old_setting["default"])
        assert new_default_store_setting['ENGINE'] == 'xmodule.modulestore.mongo.draft.DraftModuleStore'
        assert self.is_split_configured(new_mixed_setting)

    def test_convert_from_dict_to_list(self):
        old_mixed_setting = self.OLD_MIXED_CONFIG_WITH_DICT
        with pytest.warns(DeprecationWarning,
                          match="Using a dict for the Stores option in the MixedModuleStore is deprecated"):
            new_mixed_setting, new_default_store_setting = self.assertMigrated(old_mixed_setting)
        assert new_default_store_setting['ENGINE'] == 'the_default_store'
        assert self.is_split_configured(new_mixed_setting)

        # exclude split when comparing old and new, since split was added as part of the migration
        new_stores = [store for store in get_mixed_stores(new_mixed_setting) if store['NAME'] != 'split']
        old_stores = get_mixed_stores(self.OLD_MIXED_CONFIG_WITH_DICT)

        # compare each store configured in mixed
        assert len(new_stores) == len(old_stores)
        for new_store in new_stores:
            self.assertStoreValuesEqual(new_store, old_stores[new_store['NAME']])

    def test_no_conversion(self):
        # make sure there is no migration done on an already updated config
        old_mixed_setting = self.ALREADY_UPDATED_MIXED_CONFIG
        new_mixed_setting, new_default_store_setting = self.assertMigrated(old_mixed_setting)  # lint-amnesty, pylint: disable=unused-variable
        assert self.is_split_configured(new_mixed_setting)
        assert old_mixed_setting == new_mixed_setting

    @ddt.data('draft', 'split')
    def test_update_settings(self, default_store):
        mixed_setting = self.ALREADY_UPDATED_MIXED_CONFIG
        update_module_store_settings(mixed_setting, default_store=default_store)
        assert get_mixed_stores(mixed_setting)[0]['NAME'] == default_store

    def test_update_settings_error(self):
        mixed_setting = self.ALREADY_UPDATED_MIXED_CONFIG
        with pytest.raises(Exception):
            update_module_store_settings(mixed_setting, default_store='non-existent store')
