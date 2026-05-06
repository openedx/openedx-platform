# pylint: disable=missing-module-docstring

import copy
from collections import namedtuple

from xblock.core import XBlockAside
from xblock.exceptions import InvalidScopeError
from xblock.fields import Scope

from xmodule.modulestore.inheritance import InheritanceKeyValueStore

from .definition_lazy_loader import DefinitionLazyLoader

# id is a BlockUsageLocator, def_id is the definition's guid
SplitMongoKVSid = namedtuple('SplitMongoKVSid', 'id, def_id')


class SplitMongoKVS(InheritanceKeyValueStore):
    """
    A KeyValueStore that maps keyed data access to one of the 3 data areas
    known to the MongoModuleStore (data, children, and metadata)
    """

    VALID_SCOPES = (Scope.parent, Scope.children, Scope.settings, Scope.content)

    def __init__(self, definition, initial_values, default_values, parent, aside_fields=None, field_decorator=None):
        """

        :param definition: either a lazyloader or definition id for the definition
        :param initial_values: a dictionary of the locally set values
        :param default_values: any Scope.settings field defaults that are set locally
            (copied from a template block with copy_from_template)
        """
        # deepcopy so that manipulations of fields does not pollute the source
        super().__init__(copy.deepcopy(initial_values))
        self._definition = definition  # either a DefinitionLazyLoader or the db id of the definition.
        # if the db id, then the definition is presumed to be loaded into _fields

        self._defaults = default_values
        # a decorator function for field values (to be called when a field is accessed)
        if field_decorator is None:
            self.field_decorator = lambda x: x
        else:
            self.field_decorator = field_decorator

        self.parent = parent
        self.aside_fields = aside_fields if aside_fields else {}

    def get(self, key):
        if key.block_family == XBlockAside.entry_point:  # pylint: disable=no-else-raise
            if key.scope not in self.VALID_SCOPES:
                raise InvalidScopeError(key, self.VALID_SCOPES)

            if key.block_scope_id.block_type not in self.aside_fields:
                # load the definition to see if it has the aside_fields
                self._load_definition()
                if key.block_scope_id.block_type not in self.aside_fields:
                    raise KeyError()
            aside_fields = self.aside_fields[key.block_scope_id.block_type]
            # load the field, if needed
            if key.field_name not in aside_fields:
                self._load_definition()

            if key.field_name in aside_fields:
                return self.field_decorator(aside_fields[key.field_name])

            raise KeyError()
        else:
            # load the field, if needed
            if key.field_name not in self._fields:
                if key.scope == Scope.parent:
                    return self.parent
                if key.scope == Scope.children:  # pylint: disable=no-else-raise
                    # didn't find children in _fields; so, see if there's a default
                    raise KeyError()
                elif key.scope == Scope.settings:
                    # get default which may be the inherited value
                    raise KeyError()
                elif key.scope == Scope.content:
                    if isinstance(self._definition, DefinitionLazyLoader):
                        self._load_definition()
                    else:
                        raise KeyError()
                else:
                    raise InvalidScopeError(key)

            if key.field_name in self._fields:
                field_value = self._fields[key.field_name]
                # return the "decorated" field value
                return self.field_decorator(field_value)

            return None

    def set(self, key, value):
        # handle any special cases
        if key.scope not in self.VALID_SCOPES:
            raise InvalidScopeError(key, self.VALID_SCOPES)
        if key.scope == Scope.content:
            self._load_definition()

        if key.block_family == XBlockAside.entry_point:
            if key.scope == Scope.children:
                raise InvalidScopeError(key)

            self.aside_fields.setdefault(key.block_scope_id.block_type, {})[key.field_name] = value
        else:
            # set the field
            self._fields[key.field_name] = value

            # This function is currently incomplete: it doesn't handle side effects.
            # To complete this function, here is some pseudocode for what should happen:
            #
            # if key.scope == Scope.children:
            #     remove inheritance from any exchildren
            #     add inheritance to any new children
            # if key.scope == Scope.settings:
            #     if inheritable, push down to children

    def delete(self, key):
        # handle any special cases
        if key.scope not in self.VALID_SCOPES:
            raise InvalidScopeError(key, self.VALID_SCOPES)
        if key.scope == Scope.content:
            self._load_definition()

        if key.block_family == XBlockAside.entry_point:
            if key.scope == Scope.children:
                raise InvalidScopeError(key)

            if key.block_scope_id.block_type in self.aside_fields \
                    and key.field_name in self.aside_fields[key.block_scope_id.block_type]:
                del self.aside_fields[key.block_scope_id.block_type][key.field_name]
        else:
            # delete the field value
            if key.field_name in self._fields:
                del self._fields[key.field_name]

    def has(self, key):
        """
        Is the given field explicitly set in this kvs (neither inherited nor default)
        """
        # handle any special cases
        if key.scope not in self.VALID_SCOPES:
            return False

        if key.scope == Scope.content:
            self._load_definition()
        elif key.scope == Scope.parent:
            return True

        if key.block_family == XBlockAside.entry_point:
            if key.scope == Scope.children:
                return False

            b_type = key.block_scope_id.block_type
            return b_type in self.aside_fields and key.field_name in self.aside_fields[b_type]
        else:
            # it's not clear whether inherited values should return True. Right now they don't
            # if someone changes it so that they do, then change any tests of field.name in xx._field_data
            return key.field_name in self._fields

    def has_default_value(self, field_name):
        """
        Is the given field has default value in this kvs
        """
        return field_name in self._defaults

    def default(self, key):
        """
        Check to see if the default should be from the template's defaults (if any)
        rather than the global default or inheritance.
        """
        if self._defaults and key.field_name in self._defaults:
            return self._defaults[key.field_name]
        # If not, try inheriting from a parent, then use the XBlock type's normal default value:
        return super().default(key)

    def _load_definition(self):
        """
        Update fields w/ the lazily loaded definitions
        """
        if isinstance(self._definition, DefinitionLazyLoader):
            persisted_definition = self._definition.fetch()
            if persisted_definition is not None:
                fields = self._definition.field_converter(persisted_definition.get('fields'))
                self._fields.update(fields)
                aside_fields_p = persisted_definition.get('aside_fields')
                if aside_fields_p:
                    aside_fields = self._definition.field_converter(aside_fields_p)
                    for aside_type, fields in aside_fields.items():
                        self.aside_fields.setdefault(aside_type, {}).update(fields)
                # do we want to cache any of the edit_info?
            self._definition = None  # already loaded
