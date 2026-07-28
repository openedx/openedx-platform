# pylint: disable=missing-module-docstring

import datetime
import os
import random
import unittest
from unittest import mock

import pytest
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.inheritance import InheritanceMixin
from xmodule.modulestore.mongo import DraftMongoModuleStore
from xmodule.modulestore.split_mongo.split import SplitMongoModuleStore
from xmodule.modulestore.tests.mongo_connection import MONGO_HOST, MONGO_PORT_NUM
from xmodule.modulestore.tests.utils import MemoryCache
from xmodule.x_module import XModuleMixin


@pytest.mark.mongo
@pytest.mark.django_db
class SplitWMongoCourseBootstrapper(unittest.TestCase):
    """
    Helper for tests which need to construct split mongo & old mongo based courses to get interesting internal structure.  # pylint: disable=line-too-long
    Override _create_course and after invoking the super() _create_course, have it call _create_item for
    each xblock you want in the course.
    This class ensures the db gets created, opened, and cleaned up in addition to creating the course

    Defines the following attrs on self:
    * user_id: a random non-registered mock user id
    * split_mongo: a pointer to the split mongo instance
    * draft_mongo: a pointer to the old draft instance
    * split_course_key (CourseLocator): of the new course
    * old_course_key: the SlashSpecifiedCourseKey for the course
    """
    # Snippet of what would be in the django settings envs file
    db_config = {
        'host': MONGO_HOST,
        'port': MONGO_PORT_NUM,
        'db': f'test_xmodule_{os.getpid()}',
        'collection': 'modulestore'
    }

    modulestore_options = {
        'default_class': 'xmodule.hidden_block.HiddenBlock',
        'fs_root': '',
        'render_template': mock.Mock(return_value=""),
        'xblock_mixins': (InheritanceMixin, XModuleMixin)
    }

    split_course_key = CourseLocator('test_org', 'test_course', 'runid', branch=ModuleStoreEnum.BranchName.draft)

    def setUp(self):
        self.user_id = random.getrandbits(32)
        super().setUp()
        self.split_mongo = SplitMongoModuleStore(
            None,
            self.db_config,
            **self.modulestore_options
        )
        self.addCleanup(self.split_mongo._drop_database)  # pylint: disable=protected-access
        self.draft_mongo = DraftMongoModuleStore(
            None, self.db_config, branch_setting_func=lambda: ModuleStoreEnum.Branch.draft_preferred,
            metadata_inheritance_cache_subsystem=MemoryCache(),
            **self.modulestore_options
        )
        self.addCleanup(self.draft_mongo._drop_database)  # pylint: disable=protected-access
        self.old_course_key = None
        self.runtime = None
        self._create_course()

    def _create_item(self, category, name, data, metadata, parent_category, parent_name, draft=True, split=True):
        """
        Create the item of the given category and block id in split and old mongo, add it to the optional
        parent. The parent category is only needed because old mongo requires it for the id.

        Note: if draft = False, it will create the draft and then publish it; so, it will overwrite any
        existing draft for both the new item and the parent
        """
        location = self.old_course_key.make_usage_key(category, name)
        self.draft_mongo.create_item(
            self.user_id,
            location.course_key,
            location.block_type,
            block_id=location.block_id,
            definition_data=data,
            metadata=metadata,
            runtime=self.runtime
        )
        if not draft:
            self.draft_mongo.publish(location, self.user_id)
        if isinstance(data, str):
            fields = {'data': data}
        else:
            fields = data.copy()
        fields.update(metadata)
        if parent_name:
            # add child to parent in mongo
            parent_location = self.old_course_key.make_usage_key(parent_category, parent_name)
            parent = self.draft_mongo.get_item(parent_location)
            parent.children.append(location)
            self.draft_mongo.update_item(parent, self.user_id)
            if not draft:
                self.draft_mongo.publish(parent_location, self.user_id)
            # create child for split
            if split:
                self.split_mongo.create_child(
                    self.user_id,
                    BlockUsageLocator(
                        course_key=self.split_course_key,
                        block_type=parent_category,
                        block_id=parent_name
                    ),
                    category,
                    block_id=name,
                    fields=fields
                )
        else:
            if split:
                self.split_mongo.create_item(
                    self.user_id,
                    self.split_course_key,
                    category,
                    block_id=name,
                    fields=fields
                )

    def _create_course(self, split=True):
        """
        * some detached items
        * some attached children
        * some orphans
        """
        metadata = {
            'start': datetime.datetime(2000, 3, 13, 4),
            'display_name': 'Migration test course',
        }
        data = {
            'wiki_slug': 'test_course_slug'
        }
        fields = metadata.copy()
        fields.update(data)
        if split:
            # split requires the course to be created separately from creating items
            self.split_mongo.create_course(
                self.split_course_key.org, self.split_course_key.course, self.split_course_key.run, self.user_id, fields=fields, root_block_id='runid'  # pylint: disable=line-too-long
            )
        old_course = self.draft_mongo.create_course(self.split_course_key.org, 'test_course', 'runid', self.user_id, fields=fields)  # pylint: disable=line-too-long
        self.old_course_key = old_course.id
        self.runtime = old_course.runtime
