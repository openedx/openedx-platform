"""
Test cases for the modulestore migrator API.
"""

import pytest
from unittest.mock import patch
from opaque_keys.edx.locator import LibraryLocator, LibraryLocatorV2, CourseLocator
from openedx_content import api as content_api
from organizations.tests.factories import OrganizationFactory

from cms.djangoapps.modulestore_migrator import api
from cms.djangoapps.modulestore_migrator.data import CompositionLevel, RepeatHandlingStrategy
from cms.djangoapps.modulestore_migrator.models import ModulestoreMigration
from cms.djangoapps.modulestore_migrator.tests.factories import ModulestoreSourceFactory
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries import api as lib_api

from xmodule.modulestore.tests.utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, LibraryFactory


@pytest.mark.django_db
class TestModulestoreMigratorAPI(ModuleStoreTestCase):
    """
    Test cases for the modulestore migrator API.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory(password=self.user_password, is_staff=True)
        self.organization = OrganizationFactory(name="My Org", short_name="myorg")
        self.lib_key_v1 = LibraryLocator.from_string("library-v1:myorg+old")
        self.lib_key_v1_2 = LibraryLocator.from_string("library-v1:myorg+old2")
        self.lib_key_v1_3 = LibraryLocator.from_string("library-v1:myorg+old3")
        LibraryFactory.create(org="myorg", library="old", display_name="Old Library", modulestore=self.store)
        LibraryFactory.create(org="myorg", library="old2", display_name="Old Library 2", modulestore=self.store)
        LibraryFactory.create(org="myorg", library="old3", display_name="Old Library 3", modulestore=self.store)
        self.lib_key_v2_1 = LibraryLocatorV2.from_string("lib:myorg:1")
        self.lib_key_v2_2 = LibraryLocatorV2.from_string("lib:myorg:2")
        lib_api.create_library(org=self.organization, slug="1", title="Test Library 1")
        lib_api.create_library(org=self.organization, slug="2", title="Test Library 2")
        self.library_v2_1 = lib_api.ContentLibrary.objects.get(slug="1")
        self.library_v2_2 = lib_api.ContentLibrary.objects.get(slug="2")
        self.learning_package = self.library_v2_1.learning_package
        self.learning_package_2 = self.library_v2_2.learning_package
        self.source_unit_keys = [
            BlockFactory.create(
                display_name=f"Unit {c}",
                category="vertical",
                location=self.lib_key_v1.make_usage_key("vertical", c),
                parent_location=self.lib_key_v1.make_usage_key("library", "library"),
                user_id=self.user.id, publish_item=False,
            ).usage_key for c in ["X", "Y", "Z"]
        ]
        self.source_html_keys = [
            BlockFactory.create(
                display_name=f"HTML {c}",
                category="html",
                location=self.lib_key_v1.make_usage_key("html", c),
                parent_location=self.lib_key_v1.make_usage_key("vertical", c),
                user_id=self.user.id, publish_item=False,
            ).usage_key for c in ["X", "Y", "Z"]
        ]
        # We load this last so that it has an updated list of children.
        self.lib_v1 = self.store.get_library(self.lib_key_v1)
        self.course_key = CourseLocator.from_string('course-v1:TestOrg+TestCourse+TestRun')

        # Create containers and blocks for legacy libraries
        # Old Library 2
        for c in ["X", "Y", "Z"]:
            BlockFactory.create(
                display_name=f"Unit {c}",
                category="vertical",
                location=self.lib_key_v1_2.make_usage_key("vertical", c),
                parent_location=self.lib_key_v1_2.make_usage_key("library", "library"),
                user_id=self.user.id, publish_item=False,
            )
        for c in ["X", "Y"]:
            BlockFactory.create(
                display_name=f"Subsection {c}",
                category="sequential",
                location=self.lib_key_v1_2.make_usage_key("sequential", c),
                parent_location=self.lib_key_v1_2.make_usage_key("library", "library"),
                user_id=self.user.id, publish_item=False,
            )
        BlockFactory.create(
            display_name="Section X",
            category="chapter",
            location=self.lib_key_v1_2.make_usage_key("chapter", "X"),
            parent_location=self.lib_key_v1_2.make_usage_key("library", "library"),
            user_id=self.user.id, publish_item=False,
        )
        for c in ["X", "Y", "Z"]:
            BlockFactory.create(
                display_name=f"HTML {c}",
                category="html",
                location=self.lib_key_v1_2.make_usage_key("html", c),
                parent_location=self.lib_key_v1_2.make_usage_key("vertical", c),
                user_id=self.user.id, publish_item=False,
            )

        # Old Library 3
        for c in ["X", "Y", "Z"]:
            BlockFactory.create(
                display_name=f"Unit {c}",
                category="vertical",
                location=self.lib_key_v1_3.make_usage_key("vertical", c),
                parent_location=self.lib_key_v1_3.make_usage_key("library", "library"),
                user_id=self.user.id, publish_item=False,
            )
        for c in ["X", "Y"]:
            BlockFactory.create(
                display_name=f"Subsection {c}",
                category="sequential",
                location=self.lib_key_v1_3.make_usage_key("sequential", c),
                parent_location=self.lib_key_v1_3.make_usage_key("library", "library"),
                user_id=self.user.id, publish_item=False,
            )
        BlockFactory.create(
            display_name="Section X",
            category="chapter",
            location=self.lib_key_v1_3.make_usage_key("chapter", "X"),
            parent_location=self.lib_key_v1_3.make_usage_key("library", "library"),
            user_id=self.user.id, publish_item=False,
        )
        for c in ["X", "Y", "Z"]:
            BlockFactory.create(
                display_name=f"Html {c}",
                category="html",
                location=self.lib_key_v1_3.make_usage_key("html", c),
                parent_location=self.lib_key_v1_3.make_usage_key("vertical", c),
                user_id=self.user.id, publish_item=False,
            )
        for c in ["A"]:
            BlockFactory.create(
                display_name=f"Item Bank {c}",
                category="item_bank",
                location=self.lib_key_v1_3.make_usage_key("item_bank", c),
                parent_location=self.lib_key_v1_3.make_usage_key("vertical", "X"),
                user_id=self.user.id, publish_item=False,
            )
        for c in ["B"]:
            BlockFactory.create(
                display_name=f"Invalid {c}",
                category="invalid",
                location=self.lib_key_v1_3.make_usage_key("invalid", c),
                parent_location=self.lib_key_v1_3.make_usage_key("vertical", "X"),
                user_id=self.user.id, publish_item=False,
            )

    def test_start_migration_to_library(self):
        """
        Test that the API can start a migration to a library.
        """
        source = ModulestoreSourceFactory()
        user = UserFactory()

        api.start_migration_to_library(
            user=user,
            source_key=source.key,
            target_library_key=self.library_v2_1.library_key,
            target_collection_slug=None,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )

        modulestoremigration = ModulestoreMigration.objects.get()
        assert modulestoremigration.source.key == source.key
        assert (
            modulestoremigration.composition_level == CompositionLevel.Component.value
        )
        assert modulestoremigration.repeat_handling_strategy == RepeatHandlingStrategy.Skip.value
        assert modulestoremigration.preserve_url_slugs is True
        assert modulestoremigration.task_status is not None
        assert modulestoremigration.task_status.user == user

    def test_start_bulk_migration_to_library(self):
        """
        Test that the API can start a bulk migration to a library.
        """
        source = ModulestoreSourceFactory()
        source_2 = ModulestoreSourceFactory()
        user = UserFactory()

        api.start_bulk_migration_to_library(
            user=user,
            source_key_list=[source.key, source_2.key],
            target_library_key=self.library_v2_1.library_key,
            target_collection_slug_list=None,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )

        modulestoremigration = ModulestoreMigration.objects.get(source=source)
        assert modulestoremigration.source.key == source.key
        assert (
            modulestoremigration.composition_level == CompositionLevel.Component.value
        )
        assert modulestoremigration.repeat_handling_strategy == RepeatHandlingStrategy.Skip.value
        assert modulestoremigration.preserve_url_slugs is True
        assert modulestoremigration.task_status is not None
        assert modulestoremigration.task_status.user == user

        modulestoremigration_2 = ModulestoreMigration.objects.get(source=source_2)
        assert modulestoremigration_2.source.key == source_2.key
        assert (
            modulestoremigration_2.composition_level == CompositionLevel.Component.value
        )
        assert modulestoremigration_2.repeat_handling_strategy == RepeatHandlingStrategy.Skip.value
        assert modulestoremigration_2.preserve_url_slugs is True
        assert modulestoremigration_2.task_status is not None
        assert modulestoremigration_2.task_status.user == user

    def test_start_migration_to_library_with_collection(self):
        """
        Test that the API can start a migration to a library with a target collection.
        """

        source = ModulestoreSourceFactory()
        user = UserFactory()

        collection_key = "test-collection"
        content_api.create_collection(
            learning_package_id=self.learning_package.id,
            key=collection_key,
            title="Test Collection",
            created_by=user.id,
        )

        api.start_migration_to_library(
            user=user,
            source_key=source.key,
            target_library_key=self.library_v2_1.library_key,
            target_collection_slug=collection_key,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )

        modulestoremigration = ModulestoreMigration.objects.get()
        assert modulestoremigration.target_collection.key == collection_key

    def test_start_migration_to_library_with_strategy_skip(self):
        """
        Test that the API can start a migration to a library with a skip strategy.
        """
        library = LibraryFactory.create(modulestore=self.store)
        library_block = BlockFactory.create(
            parent=library,
            category="html",
            display_name="Original Block",
            publish_item=False,
        )
        source = ModulestoreSourceFactory(key=library.context_key)
        user = UserFactory()

        # Start a migration with the Skip strategy
        api.start_migration_to_library(
            user=user,
            source_key=source.key,
            target_library_key=self.library_v2_1.library_key,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )

        modulestoremigration = ModulestoreMigration.objects.get()
        assert modulestoremigration.repeat_handling_strategy == RepeatHandlingStrategy.Skip.value

        migrated_components = lib_api.get_library_components(self.library_v2_1.library_key)
        assert len(migrated_components) == 1

        # Update the block, changing its name
        library_block.display_name = "Updated Block"
        self.store.update_item(library_block, user.id)

        # Migrate again using the Skip strategy
        api.start_migration_to_library(
            user=user,
            source_key=source.key,
            target_library_key=self.library_v2_1.library_key,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )

        modulestoremigration = ModulestoreMigration.objects.last()
        assert modulestoremigration is not None
        assert modulestoremigration.repeat_handling_strategy == RepeatHandlingStrategy.Skip.value

        migrated_components_fork = lib_api.get_library_components(self.library_v2_1.library_key)
        assert len(migrated_components_fork) == 1

        component = lib_api.LibraryXBlockMetadata.from_component(
            self.library_v2_1.library_key, migrated_components_fork[0]
        )
        assert component.display_name == "Original Block"

    def test_start_migration_to_library_with_strategy_update(self):
        """
        Test that the API can start a migration to a library with a update strategy.
        """
        library = LibraryFactory.create(modulestore=self.store)
        library_block = BlockFactory.create(
            parent=library,
            category="html",
            display_name="Original Block",
            publish_item=False,
        )
        source = ModulestoreSourceFactory(key=library.context_key)
        user = UserFactory()

        # Start a migration with the Skip strategy
        api.start_migration_to_library(
            user=user,
            source_key=source.key,
            target_library_key=self.library_v2_1.library_key,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )

        modulestoremigration = ModulestoreMigration.objects.get()
        assert modulestoremigration.repeat_handling_strategy == RepeatHandlingStrategy.Skip.value

        migrated_components = lib_api.get_library_components(self.library_v2_1.library_key)
        assert len(migrated_components) == 1

        # Update the block, changing its name
        library_block.display_name = "Updated Block"
        self.store.update_item(library_block, user.id)

        # Migrate again using the Skip strategy
        api.start_migration_to_library(
            user=user,
            source_key=source.key,
            target_library_key=self.library_v2_1.library_key,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Update,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )

        modulestoremigration = ModulestoreMigration.objects.last()
        assert modulestoremigration is not None
        assert modulestoremigration.repeat_handling_strategy == RepeatHandlingStrategy.Update.value

        migrated_components_fork = lib_api.get_library_components(self.library_v2_1.library_key)
        assert len(migrated_components_fork) == 1

        component = lib_api.LibraryXBlockMetadata.from_component(
            self.library_v2_1.library_key, migrated_components_fork[0]
        )
        assert component.display_name == "Updated Block"

    def test_start_migration_to_library_with_strategy_forking(self):
        """
        Test that the API can start a migration to a library with a forking strategy.
        """
        library = LibraryFactory.create(modulestore=self.store)
        library_block = BlockFactory.create(
            parent=library,
            category="html",
            display_name="Original Block",
            publish_item=False,
        )
        source = ModulestoreSourceFactory(key=library.context_key)
        user = UserFactory()

        # Start a migration with the Skip strategy
        api.start_migration_to_library(
            user=user,
            source_key=source.key,
            target_library_key=self.library_v2_1.library_key,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )

        modulestoremigration = ModulestoreMigration.objects.get()
        assert modulestoremigration.repeat_handling_strategy == RepeatHandlingStrategy.Skip.value

        migrated_components = lib_api.get_library_components(self.library_v2_1.library_key)
        assert len(migrated_components) == 1

        # Update the block, changing its name
        library_block.display_name = "Updated Block"
        self.store.update_item(library_block, user.id)

        # Migrate again using the Fork strategy
        api.start_migration_to_library(
            user=user,
            source_key=source.key,
            target_library_key=self.library_v2_1.library_key,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Fork,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )

        modulestoremigration = ModulestoreMigration.objects.last()
        assert modulestoremigration is not None
        assert modulestoremigration.repeat_handling_strategy == RepeatHandlingStrategy.Fork.value

        migrated_components_fork = lib_api.get_library_components(self.library_v2_1.library_key)
        assert len(migrated_components_fork) == 2

        first_component = lib_api.LibraryXBlockMetadata.from_component(
            self.library_v2_1.library_key, migrated_components_fork[0]
        )
        assert first_component.display_name == "Original Block"

        second_component = lib_api.LibraryXBlockMetadata.from_component(
            self.library_v2_1.library_key, migrated_components_fork[1]
        )
        assert second_component.display_name == "Updated Block"

        # Update the block again, changing its name
        library_block.display_name = "Updated Block Again"
        self.store.update_item(library_block, user.id)

        # Migrate again using the Fork strategy
        api.start_migration_to_library(
            user=user,
            source_key=source.key,
            target_library_key=self.library_v2_1.library_key,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Fork,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )

        modulestoremigration = ModulestoreMigration.objects.last()
        assert modulestoremigration is not None
        assert modulestoremigration.repeat_handling_strategy == RepeatHandlingStrategy.Fork.value

        migrated_components_fork = lib_api.get_library_components(self.library_v2_1.library_key)
        assert len(migrated_components_fork) == 3

        first_component = lib_api.LibraryXBlockMetadata.from_component(
            self.library_v2_1.library_key, migrated_components_fork[0]
        )
        assert first_component.display_name == "Original Block"

        second_component = lib_api.LibraryXBlockMetadata.from_component(
            self.library_v2_1.library_key, migrated_components_fork[1]
        )
        assert second_component.display_name == "Updated Block"

        third_component = lib_api.LibraryXBlockMetadata.from_component(
            self.library_v2_1.library_key, migrated_components_fork[2]
        )
        assert third_component.display_name == "Updated Block Again"

    def test_migration_api_for_various_scenarios(self):
        """
        Test that get_migrations, get_block_migrations, forward_context, and forward_block
        behave as expected throughout a convoluted series of intertwined migrations.

        Also, ensure that each of the aforementioned api functions only performs 1 query each.
        """
        # pylint: disable=too-many-statements
        user = UserFactory()

        all_source_usage_keys = {*self.source_html_keys, *self.source_unit_keys}
        all_source_usage_key_strs = {str(sk) for sk in all_source_usage_keys}

        # In this test, we will be migrating self.lib_v1 a total of 6 times.
        # We will migrate it to each collection (A, B, and C) twice.

        # Lib 1 has Collection A and Collection B
        # Lib 2 has Collection C
        content_api.create_collection(
            learning_package_id=self.learning_package.id,
            key="test-collection-1a",
            title="Test Collection A in Lib 1",
            created_by=user.id,
        )
        content_api.create_collection(
            learning_package_id=self.learning_package.id,
            key="test-collection-1b",
            title="Test Collection B in Lib 1",
            created_by=user.id,
        )
        content_api.create_collection(
            learning_package_id=self.learning_package_2.id,
            key="test-collection-2c",
            title="Test Collection C in Lib 2",
            created_by=user.id,
        )

        # No migrations have happened.
        # Everything should return None / empty.
        assert not list(api.get_migrations(self.lib_key_v1))
        assert not api.get_forwarding(source_key=self.lib_key_v1)
        assert not api.get_forwarding_for_blocks(all_source_usage_keys)

        # FOUR MIGRATIONS!
        # * Migrate to Lib1.CollA
        # * Migrate to Lib1.CollB using FORK strategy
        # * Migrate to Lib1.CollA using UPDATE strategy
        # * Migrate to Lib2.CollC
        # Note: None of these are forwarding migrations!
        api.start_migration_to_library(
            user=user,
            source_key=self.lib_key_v1,
            target_library_key=self.lib_key_v2_1,
            target_collection_slug="test-collection-1a",
            composition_level=CompositionLevel.Unit,
            # repeat_handling_strategy here is arbitrary, as there will be no repeats.
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )
        api.start_migration_to_library(
            user=user,
            source_key=self.lib_key_v1,
            target_library_key=self.lib_key_v2_1,
            target_collection_slug="test-collection-1b",
            composition_level=CompositionLevel.Unit,
            # this will create a 2nd copy of every block
            repeat_handling_strategy=RepeatHandlingStrategy.Fork,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )
        api.start_migration_to_library(
            user=user,
            source_key=self.lib_key_v1,
            target_library_key=self.lib_key_v2_1,
            target_collection_slug="test-collection-1a",
            composition_level=CompositionLevel.Unit,
            # this will update the 2nd copies, but put them in the same collection as the first copies
            repeat_handling_strategy=RepeatHandlingStrategy.Update,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )
        api.start_migration_to_library(
            user=user,
            source_key=self.lib_key_v1,
            target_library_key=self.lib_key_v2_2,
            target_collection_slug="test-collection-2c",
            composition_level=CompositionLevel.Unit,
            # repeat_handling_strategy here is arbitrary, as there will be no repeats.
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )
        # get_migrations returns in reverse chronological order
        with self.assertNumQueries(1):
            migration_2c_i, migration_1a_ii, migration_1b_i, migration_1a_i = api.get_migrations(self.lib_key_v1)
        assert not migration_1a_i.is_failed
        assert not migration_1b_i.is_failed
        assert not migration_1a_ii.is_failed
        assert not migration_2c_i.is_failed
        # Confirm that the metadata came back correctly.
        assert migration_1a_i.target_key == self.lib_key_v2_1
        assert migration_1a_i.target_title == "Test Library 1"
        assert migration_1a_i.target_collection_slug == "test-collection-1a"
        assert migration_1a_i.target_collection_title == "Test Collection A in Lib 1"
        assert migration_2c_i.target_key == self.lib_key_v2_2
        assert migration_2c_i.target_title == "Test Library 2"
        assert migration_2c_i.target_collection_slug == "test-collection-2c"
        assert migration_2c_i.target_collection_title == "Test Collection C in Lib 2"
        # Call get_migration_blocks on each of the four migrations. Convert the mapping
        # from UsageKey->BlockMigrationResult into str->str just so we can assert things more easily.
        with self.assertNumQueries(1):
            mappings_1a_i = {
                str(sk): str(bm.target_key) for sk, bm in api.get_migration_blocks(migration_1a_i.pk).items()
            }
        mappings_1b_i = {
            str(sk): str(bm.target_key) for sk, bm in api.get_migration_blocks(migration_1b_i.pk).items()
        }
        mappings_1a_ii = {
            str(sk): str(bm.target_key) for sk, bm in api.get_migration_blocks(migration_1a_ii.pk).items()
        }
        mappings_2c_i = {
            str(sk): str(bm.target_key) for sk, bm in api.get_migration_blocks(migration_2c_i.pk).items()
        }
        # Each migration should have migrated every source block.
        assert set(mappings_1a_i.keys()) == all_source_usage_key_strs
        assert set(mappings_1b_i.keys()) == all_source_usage_key_strs
        assert set(mappings_1a_ii.keys()) == all_source_usage_key_strs
        assert set(mappings_2c_i.keys()) == all_source_usage_key_strs
        # Because the migration to Lib1.CollB used FORK, we expect that there is nothing in
        # common between it and the prior migration to Lib1.CollA.
        assert not (set(mappings_1a_i.values()) & set(mappings_1b_i.values()))
        # Because the second migration to Lib1.CollA used UPDATE, we expect that it
        # will have all the same mappings as the prior migration to Lib1.CollB.
        # This is a little countertuitive, since the migrations targeted different collections,
        # but the rule that the migrator follows is "UPDATE uses the block from the most recent migration".
        assert mappings_1b_i == mappings_1a_ii
        # Since forward_source_to_target=False, we have had no authoritative migration yet.
        assert api.get_forwarding(self.lib_key_v1) is None
        assert not api.get_forwarding_for_blocks(all_source_usage_keys)

        # ANOTHER MIGRATION!
        # * Migrate to Lib2.CollC using UPDATE strategy
        # Note: This *is* a forwarding migration
        api.start_migration_to_library(
            user=user,
            source_key=self.lib_key_v1,
            target_library_key=self.lib_key_v2_2,
            target_collection_slug="test-collection-2c",
            composition_level=CompositionLevel.Unit,
            repeat_handling_strategy=RepeatHandlingStrategy.Update,
            preserve_url_slugs=True,
            forward_source_to_target=True,
        )
        migration_2c_ii, _2c_i, _1a_ii, _1b_i, migration_1a_i_reloaded = api.get_migrations(self.lib_key_v1)
        assert migration_1a_i_reloaded.pk == migration_1a_i.pk
        assert not migration_2c_ii.is_failed
        # Our source lib should now forward to Lib2.
        with self.assertNumQueries(1):
            forwarded = api.get_forwarding(self.lib_key_v1)
        assert forwarded.target_key == self.lib_key_v2_2
        assert forwarded.target_collection_slug == "test-collection-2c"
        assert forwarded.pk == migration_2c_ii.pk
        # Our source lib's blocks should now forward to ones in Lib2.
        with self.assertNumQueries(1):
            forwarded_blocks = api.get_forwarding_for_blocks(all_source_usage_keys)
        assert forwarded_blocks[self.source_html_keys[1]].target_key.context_key == self.lib_key_v2_2
        assert forwarded_blocks[self.source_unit_keys[1]].target_key.context_key == self.lib_key_v2_2

        # FINAL MIGRATION!
        # * Migrate to Lib1.CollB using UPDATE strategy
        # Note: This *is* a forwarding migration, and should supplant the previous
        #       migration for forwarding purposes.
        api.start_migration_to_library(
            user=user,
            source_key=self.lib_key_v1,
            target_library_key=self.lib_key_v2_1,
            target_collection_slug="test-collection-1b",
            composition_level=CompositionLevel.Unit,
            repeat_handling_strategy=RepeatHandlingStrategy.Update,
            preserve_url_slugs=True,
            forward_source_to_target=True,
        )
        migration_1b_ii, _2c_ii, _2c_i, _1a_ii, _1b_i, _1a_i = api.get_migrations(self.lib_key_v1)
        assert not migration_1b_ii.is_failed
        # Our source lib should now forward to Lib1.
        forwarded = api.get_forwarding(self.lib_key_v1)
        assert forwarded.target_key == self.lib_key_v2_1
        assert forwarded.target_collection_slug == "test-collection-1b"
        assert forwarded.pk == migration_1b_ii.pk
        # Our source lib should now forward to Lib1.
        forwarded_blocks = api.get_forwarding_for_blocks(all_source_usage_keys)
        assert forwarded_blocks[self.source_html_keys[1]].target_key.context_key == self.lib_key_v2_1
        assert forwarded_blocks[self.source_unit_keys[1]].target_key.context_key == self.lib_key_v2_1

    def _get_summary_from_migration(self, migration, expected_state, blocks_limit):
        """
        Manually calculate the summary from the migration data
        """
        blocks = api.get_migration_blocks(migration.pk)

        summary = {
            "state": expected_state,
            "unsupported_blocks": 0,
            "unsupported_percentage": 0,
            "blocks_limit": blocks_limit,
            "total_blocks": 0,
            "total_components": 0,
            "sections": 0,
            "subsections": 0,
            "units": 0,
        }

        for key, block in blocks.items():
            block_type = key.block_type
            print(block_type)
            summary['total_blocks'] += 1
            if block_type not in ['vertical', 'sequential', 'chapter']:
                summary['total_components'] += 1
                if block.is_failed:
                    summary['unsupported_blocks'] += 1
            elif block_type == 'vertical':
                summary['units'] += 1
            elif block_type == 'sequential':
                summary['subsections'] += 1
            elif block_type == 'chapter':
                summary['sections'] += 1

        if summary['unsupported_blocks']:
            summary['unsupported_percentage'] = summary['unsupported_blocks'] * 100 / summary['total_blocks']

        return summary

    @patch('cms.djangoapps.modulestore_migrator.api.read_api.fetch_block_types')
    @patch('cms.djangoapps.modulestore_migrator.api.read_api.get_all_blocks_from_context')
    def test_preview_migration_success(self, mock_get_blocks, mock_fetch_block_types):
        """
        Test the preview migration summary in the success state

        This tests compare the summary generated by `preview_migration` with the
        data generated by a migration.
        """
        user = UserFactory()
        api.start_migration_to_library(
            user=user,
            source_key=self.lib_key_v1_2,
            target_library_key=self.lib_key_v2_1,
            target_collection_slug=None,
            composition_level=CompositionLevel.Section,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )
        migration = list(api.get_migrations(self.lib_key_v1_2))[0]
        summary = self._get_summary_from_migration(migration, "success", 1000)

        mock_get_blocks.return_value = [
            {
                'block_type': 'html',
                'block_id': '16',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@html+block@16',
            },
            {
                'block_type': 'html',
                'block_id': '17',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@html+block@17',
            },
            {
                'block_type': 'html',
                'block_id': '18',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@html+block@18',
            },
            {
                'block_type': 'chapter',
                'block_id': '19',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@chapter+block@19',
            },
            {
                'block_type': 'sequential',
                'block_id': '20',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@20',
            },
            {
                'block_type': 'sequential',
                'block_id': '21',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@21',
            },
            {
                'block_type': 'vertical',
                'block_id': '22',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@vertical+block@22',
            },
            {
                'block_type': 'vertical',
                'block_id': '23',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@vertical+block@23',
            },
            {
                'block_type': 'vertical',
                'block_id': '23',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@vertical+block@2',
            },
        ]
        mock_fetch_block_types.return_value = {
            "estimatedTotalHits": 0,
        }

        with self.settings(MAX_BLOCKS_PER_CONTENT_LIBRARY=1000):
            results = api.preview_migration(self.course_key, self.lib_key_v2_1)
            assert results == summary

    @patch('cms.djangoapps.modulestore_migrator.api.read_api.fetch_block_types')
    @patch('cms.djangoapps.modulestore_migrator.api.read_api.get_all_blocks_from_context')
    def test_preview_migration_partial(self, mock_get_blocks, mock_fetch_block_types):
        """
        Test the preview migration summary in the partial state

        This tests compare the summary generated by `preview_migration` with the
        data generated by a migration.
        """
        user = UserFactory()
        api.start_migration_to_library(
            user=user,
            source_key=self.lib_key_v1_3,
            target_library_key=self.lib_key_v2_1,
            target_collection_slug=None,
            composition_level=CompositionLevel.Section,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )
        migration = list(api.get_migrations(self.lib_key_v1_3))[0]
        summary = self._get_summary_from_migration(migration, "partial", 1000)

        mock_get_blocks.return_value = [
            {
                'block_type': 'html',
                'block_id': '16',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@html+block@16',
            },
            {
                'block_type': 'html',
                'block_id': '17',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@html+block@17',
            },
            {
                'block_type': 'html',
                'block_id': '18',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@html+block@18',
            },
            {
                'block_type': 'chapter',
                'block_id': '19',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@chapter+block@19',
            },
            {
                'block_type': 'sequential',
                'block_id': '20',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@20',
            },
            {
                'block_type': 'sequential',
                'block_id': '21',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@21',
            },
            {
                'block_type': 'vertical',
                'block_id': '22',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@vertical+block@22',
            },
            {
                'block_type': 'vertical',
                'block_id': '23',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@vertical+block@23',
            },
            {
                'block_type': 'vertical',
                'block_id': '23',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@vertical+block@2',
            },
            {
                'block_type': 'invalid',
                'block_id': '24',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@invalid+block@24',
            },  # Invalid
            {
                'block_type': 'item_bank',
                'block_id': '25',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@item_bank+block@25',
            },  # Invalid
            {
                'block_type': 'html',
                'block_id': '26',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@html+block@26',
            },  # Child of item bank
            {
                'block_type': 'html',
                'block_id': '27',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@html+block@27',
            },  # Child of item bank
        ]
        mock_fetch_block_types.return_value = {
            "estimatedTotalHits": 2,
        }

        # The unsupported children are not included in the summary
        with self.settings(MAX_BLOCKS_PER_CONTENT_LIBRARY=1000):
            results = api.preview_migration(self.course_key, self.lib_key_v2_1)
            assert results == summary

    @patch('cms.djangoapps.modulestore_migrator.api.read_api.fetch_block_types')
    @patch('cms.djangoapps.modulestore_migrator.api.read_api.get_all_blocks_from_context')
    def test_preview_migration_block_limit(self, mock_get_blocks, mock_fetch_block_types):
        """
        Test the preview migration summary in the block_limit_reached state

        This tests compare the summary generated by `preview_migration` with the
        data generated by a migration.
        """
        user = UserFactory()
        api.start_migration_to_library(
            user=user,
            source_key=self.lib_key_v1_2,
            target_library_key=self.lib_key_v2_1,
            target_collection_slug=None,
            composition_level=CompositionLevel.Section,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )
        migration = list(api.get_migrations(self.lib_key_v1_2))[0]
        summary = self._get_summary_from_migration(migration, "block_limit_reached", 10)

        mock_get_blocks.return_value = [
            {
                'block_type': 'html',
                'block_id': '16',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@html+block@16',
            },
            {
                'block_type': 'html',
                'block_id': '17',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@html+block@17',
            },
            {
                'block_type': 'html',
                'block_id': '18',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@html+block@18',
            },
            {
                'block_type': 'chapter',
                'block_id': '19',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@chapter+block@19',
            },
            {
                'block_type': 'sequential',
                'block_id': '20',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@20',
            },
            {
                'block_type': 'sequential',
                'block_id': '21',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@21',
            },
            {
                'block_type': 'vertical',
                'block_id': '22',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@vertical+block@22',
            },
            {
                'block_type': 'vertical',
                'block_id': '23',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@vertical+block@23',
            },
            {
                'block_type': 'vertical',
                'block_id': '23',
                'usage_key': 'block-v1:edX+DemoX+Demo_Course+type@vertical+block@2',
            },
        ]
        mock_fetch_block_types.return_value = {
            "estimatedTotalHits": 0,
        }

        with self.settings(MAX_BLOCKS_PER_CONTENT_LIBRARY=10):
            results = api.preview_migration(self.course_key, self.lib_key_v2_1)
            assert results == summary
