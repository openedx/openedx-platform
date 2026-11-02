# lint-amnesty, pylint: disable=missing-module-docstring

import importlib.resources as resources
import logging
import os
import time
import warnings
from collections import namedtuple
from functools import partial

import yaml

from django.conf import settings
from lxml import etree
from opaque_keys.edx.asides import AsideDefinitionKeyV2, AsideUsageKeyV2
from opaque_keys.edx.keys import UsageKey
from web_fragments.fragment import Fragment
from webob import Response
from webob.multidict import MultiDict
from xblock.core import XBlock
from xblock.fields import (
    Dict,
    Float,
    Integer,
    List,
    Scope,
    String,
    UserScope
)
from xblock.runtime import IdGenerator, IdReader, Runtime

from xmodule import block_metadata_utils
from xmodule.fields import RelativeTime
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.util.builtin_assets import add_webpack_js_to_fragment
from openedx.core.djangolib.markup import HTML

from common.djangoapps.xblock_django.constants import (
    ATTR_KEY_ANONYMOUS_USER_ID,
    ATTR_KEY_REQUEST_COUNTRY_CODE,
    ATTR_KEY_USER_ID,
    ATTR_KEY_USER_IS_BETA_TESTER,
    ATTR_KEY_USER_IS_GLOBAL_STAFF,
    ATTR_KEY_USER_IS_STAFF,
    ATTR_KEY_USER_ROLE,
)


log = logging.getLogger(__name__)

XMODULE_METRIC_NAME = 'edxapp.xmodule'
XMODULE_DURATION_METRIC_NAME = XMODULE_METRIC_NAME + '.duration'
XMODULE_METRIC_SAMPLE_RATE = 0.1

# xblock view names

# This is the view that will be rendered to display the XBlock in the LMS.
# It will also be used to render the block in "preview" mode in Studio, unless
# the XBlock also implements author_view.
STUDENT_VIEW = 'student_view'

# This is the view that will be rendered to display the XBlock in the LMS for unenrolled learners.
# Implementations of this view should assume that a user and user data are not available.
PUBLIC_VIEW = 'public_view'

# An optional view of the XBlock similar to student_view, but with possible inline
# editing capabilities. This view differs from studio_view in that it should be as similar to student_view
# as possible. When previewing XBlocks within Studio, Studio will prefer author_view to student_view.
AUTHOR_VIEW = 'author_view'

# The view used to render an editor in Studio. The editor rendering can be completely different
# from the LMS student_view, and it is only shown when the author selects "Edit".
STUDIO_VIEW = 'studio_view'

# Views that present a "preview" view of an xblock (as opposed to an editing view).
PREVIEW_VIEWS = [STUDENT_VIEW, PUBLIC_VIEW, AUTHOR_VIEW]

DEFAULT_PUBLIC_VIEW_MESSAGE = (
    'This content is only accessible to enrolled learners. '
    'Sign in or register, and enroll in this course to view it.'
)


# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
def _(text):
    return text


class OpaqueKeyReader(IdReader):
    """
    IdReader for :class:`DefinitionKey` and :class:`UsageKey`s.
    """
    def get_definition_id(self, usage_id):
        """Retrieve the definition that a usage is derived from.

        Args:
            usage_id: The id of the usage to query

        Returns:
            The `definition_id` the usage is derived from
        """
        raise NotImplementedError("Specific Modulestores must implement get_definition_id")

    def get_block_type(self, def_id):
        """Retrieve the block_type of a particular definition

        Args:
            def_id: The id of the definition to query

        Returns:
            The `block_type` of the definition
        """
        return def_id.block_type

    def get_usage_id_from_aside(self, aside_id):
        """
        Retrieve the XBlock `usage_id` associated with this aside usage id.

        Args:
            aside_id: The usage id of the XBlockAside.

        Returns:
            The `usage_id` of the usage the aside is commenting on.
        """
        return aside_id.usage_key

    def get_definition_id_from_aside(self, aside_id):
        """
        Retrieve the XBlock `definition_id` associated with this aside definition id.

        Args:
            aside_id: The usage id of the XBlockAside.

        Returns:
            The `definition_id` of the usage the aside is commenting on.
        """
        return aside_id.definition_key

    def get_aside_type_from_usage(self, aside_id):
        """
        Retrieve the XBlockAside `aside_type` associated with this aside
        usage id.

        Args:
            aside_id: The usage id of the XBlockAside.

        Returns:
            The `aside_type` of the aside.
        """
        return aside_id.aside_type

    def get_aside_type_from_definition(self, aside_id):
        """
        Retrieve the XBlockAside `aside_type` associated with this aside
        definition id.

        Args:
            aside_id: The definition id of the XBlockAside.

        Returns:
            The `aside_type` of the aside.
        """
        return aside_id.aside_type


class AsideKeyGenerator(IdGenerator):
    """
    An :class:`.IdGenerator` that only provides facilities for constructing new XBlockAsides.
    """
    def create_aside(self, definition_id, usage_id, aside_type):
        """
        Make a new aside definition and usage ids, indicating an :class:`.XBlockAside` of type `aside_type`
        commenting on an :class:`.XBlock` usage `usage_id`

        Returns:
            (aside_definition_id, aside_usage_id)
        """
        def_key = AsideDefinitionKeyV2(definition_id, aside_type)
        usage_key = AsideUsageKeyV2(usage_id, aside_type)
        return (def_key, usage_key)

    def create_usage(self, def_id):
        """Make a usage, storing its definition id.

        Returns the newly-created usage id.
        """
        raise NotImplementedError("Specific Modulestores must provide implementations of create_usage")

    def create_definition(self, block_type, slug=None):
        """Make a definition, storing its block type.

        If `slug` is provided, it is a suggestion that the definition id
        incorporate the slug somehow.

        Returns the newly-created definition id.

        """
        raise NotImplementedError("Specific Modulestores must provide implementations of create_definition")


def shim_xmodule_js(fragment, js_module_name):
    """
    Set up the XBlock -> XModule shim on the supplied :class:`web_fragments.fragment.Fragment`
    """
    # Delay this import so that it is only used (and django settings are parsed) when
    # they are required (rather than at startup)
    import webpack_loader.utils  # lint-amnesty, pylint: disable=unused-import

    if not fragment.js_init_fn:
        fragment.initialize_js('XBlockToXModuleShim')
        fragment.json_init_args = {'xmodule-type': js_module_name}

        add_webpack_js_to_fragment(fragment, 'XModuleShim')


class XModuleFields:
    """
    Common fields for XModules.
    """
    display_name = String(
        display_name=_("Display Name"),
        help=_("The display name for this component."),
        scope=Scope.settings,
        # it'd be nice to have a useful default but it screws up other things; so,
        # use display_name_with_default for those
        default=None
    )


@XBlock.needs("i18n")
class XModuleMixin(XModuleFields, XBlock):
    """
    Fields and methods used by XModules internally.

    Adding this Mixin to an :class:`XBlock` allows it to cooperate with old-style :class:`XModules`
    """
    # Attributes for inspection of the block

    # This indicates whether the xmodule is a problem-type.
    # It should respond to max_score() and grade(). It can be graded or ungraded
    # (like a practice problem).
    has_score = False

    # Whether this module can be displayed in read-only mode.  It is safe to set this to True if
    # all user state is handled through the FieldData API.
    show_in_read_only_mode = False

    # Class level variable

    # True if this block always requires recalculation of grades, for
    # example if the score can change via an extrnal service, not just when the
    # student interacts with the module on the page.  A specific example is
    # FoldIt, which posts grade-changing updates through a separate API.
    always_recalculate_grades = False
    # The default implementation of get_icon_class returns the icon_class
    # attribute of the class
    #
    # This attribute can be overridden by subclasses, and
    # the function can also be overridden if the icon class depends on the data
    # in the module
    icon_class = 'other'

    def __init__(self, *args, **kwargs):
        self._asides = []
        # Initialization data used by SplitModuleStoreRuntime to defer FieldData initialization
        self._cds_init_args = kwargs.pop("cds_init_args", None)

        super().__init__(*args, **kwargs)

    def get_cds_init_args(self):
        """ Get initialization data used by SplitModuleStoreRuntime to defer FieldData initialization """
        if self._cds_init_args is None:
            raise KeyError("cds_init_args was not provided for this XBlock")
        if self._cds_init_args is False:
            raise RuntimeError("Tried to get SplitModuleStoreRuntime cds_init_args twice for the same XBlock.")
        args = self._cds_init_args
        # Free the memory and set this False to flag any double-access bugs. This only needs to be read once.
        self._cds_init_args = False
        return args

    @property
    def runtime(self):
        return self._runtime

    @runtime.setter
    def runtime(self, value):
        self._runtime = value

    @property
    def xmodule_runtime(self):
        """
        Shim to maintain backward compatibility.

        Deprecated in favor of the runtime property.
        """
        warnings.warn(
            'xmodule_runtime property is deprecated. Please use the runtime property instead.',
            DeprecationWarning, stacklevel=3,
        )
        return self.runtime

    @property
    def system(self):
        """
        Return the XBlock runtime (backwards compatibility alias provided for XModules).

        Deprecated in favor of the runtime property.
        """
        warnings.warn(
            'system property is deprecated. Please use the runtime property instead.',
            DeprecationWarning, stacklevel=3,
        )
        return self.runtime

    @property
    def course_id(self):
        return self.location.course_key

    @property
    def category(self):
        return self.scope_ids.block_type

    @property
    def location(self):
        return self.scope_ids.usage_id

    @location.setter
    def location(self, value):
        assert isinstance(value, UsageKey)
        self.scope_ids = self.scope_ids._replace(
            def_id=value,  # Note: assigning a UsageKey as def_id is OK in old mongo / import system but wrong in split
            usage_id=value,
        )

    @property
    def url_name(self):
        return block_metadata_utils.url_name_for_block(self)

    @property
    def display_name_with_default(self):
        """
        Return a display name for the module: use display_name if defined in
        metadata, otherwise convert the url name.
        """
        return block_metadata_utils.display_name_with_default(self)

    @property
    def display_name_with_default_escaped(self):
        """
        DEPRECATED: use display_name_with_default

        Return an html escaped display name for the module: use display_name if
        defined in metadata, otherwise convert the url name.

        Note: This newly introduced method should not be used.  It was only
        introduced to enable a quick search/replace and the ability to slowly
        migrate and test switching to display_name_with_default, which is no
        longer escaped.
        """
        # xss-lint: disable=python-deprecated-display-name
        return block_metadata_utils.display_name_with_default_escaped(self)

    @property
    def tooltip_title(self):
        """
        Return the title for the sequence item containing this xmodule as its top level item.
        """
        return self.display_name_with_default

    @property
    def xblock_kvs(self):
        """
        Retrieves the internal KeyValueStore for this XModule.

        Should only be used by the persistence layer. Use with caution.
        """
        # if caller wants kvs, caller's assuming it's up to date; so, decache it
        self.save()
        return self._field_data._kvs  # pylint: disable=protected-access

    def add_aside(self, aside):
        """
        save connected asides
        """
        self._asides.append(aside)

    def get_asides(self):
        """
        get the list of connected asides
        """
        return self._asides

    def get_explicitly_set_fields_by_scope(self, scope=Scope.content):
        """
        Get a dictionary of the fields for the given scope which are set explicitly on this xblock. (Including
        any set to None.)
        """
        result = {}
        for field in self.fields.values():  # lint-amnesty, pylint: disable=no-member
            if field.scope == scope and field.is_set_on(self):
                try:
                    result[field.name] = field.read_json(self)
                except TypeError as exception:
                    exception_message = "{message}, Block-location:{location}, Field-name:{field_name}".format(
                        message=str(exception),
                        location=str(self.location),
                        field_name=field.name
                    )
                    raise TypeError(exception_message)  # lint-amnesty, pylint: disable=raise-missing-from
        return result

    def has_children_at_depth(self, depth):
        r"""
        Returns true if self has children at the given depth. depth==0 returns
        false if self is a leaf, true otherwise.

                           SELF
                            |
                     [child at depth 0]
                     /           \
                 [depth 1]    [depth 1]
                 /       \
           [depth 2]   [depth 2]

        So the example above would return True for `has_children_at_depth(2)`, and False
        for depth > 2
        """
        if depth < 0:  # lint-amnesty, pylint: disable=no-else-raise
            raise ValueError("negative depth argument is invalid")
        elif depth == 0:
            return bool(self.get_children())
        else:
            return any(child.has_children_at_depth(depth - 1) for child in self.get_children())

    def get_content_titles(self):
        r"""
        Returns list of content titles for all of self's children.

                         SEQUENCE
                            |
                         VERTICAL
                        /        \
                 SPLIT_TEST      DISCUSSION
                /        \
           VIDEO A      VIDEO B

        Essentially, this function returns a list of display_names (e.g. content titles)
        for all of the leaf nodes.  In the diagram above, calling get_content_titles on
        SEQUENCE would return the display_names of `VIDEO A`, `VIDEO B`, and `DISCUSSION`.

        This is most obviously useful for sequence_modules, which need this list to display
        tooltips to users, though in theory this should work for any tree that needs
        the display_names of all its leaf nodes.
        """
        if self.has_children:
            return sum((child.get_content_titles() for child in self.get_children()), [])
        else:
            # xss-lint: disable=python-deprecated-display-name
            return [self.display_name_with_default_escaped]

    def get_children(self, usage_id_filter=None, usage_key_filter=None):  # pylint: disable=arguments-differ
        """Returns a list of XBlock instances for the children of
        this module"""

        # Be backwards compatible with callers using usage_key_filter
        if usage_id_filter is None and usage_key_filter is not None:
            usage_id_filter = usage_key_filter

        return [
            child
            for child
            in super().get_children(usage_id_filter)
            if child is not None
        ]

    def get_child(self, usage_id):
        """
        Return the child XBlock identified by ``usage_id``, or ``None`` if there
        is an error while retrieving the block.
        """
        try:
            child = super().get_child(usage_id)
        except ItemNotFoundError:
            log.warning('Unable to load item %s, skipping', usage_id)
            return None

        if child is None:
            return None

        child.runtime.export_fs = self.runtime.export_fs
        return child

    def get_required_block_descriptors(self):
        """
        Return a list of XBlock instances upon which this block depends but are
        not children of this block.

        TODO: Move this method directly to the ConditionalBlock.
        """
        return []

    def get_child_by(self, selector):
        """
        Return a child XBlock that matches the specified selector
        """
        for child in self.get_children():
            if selector(child):
                return child
        return None

    def get_icon_class(self):
        """
        Return a css class identifying this module in the context of an icon
        """
        return self.icon_class

    def has_dynamic_children(self):
        """
        Returns True if this block has dynamic children for a given
        student when the module is created.

        Returns False if the children of this block are the same
        children that the module will return for any student.
        """
        return False

    # Functions used in the LMS

    def get_score(self):
        """
        Score the student received on the problem, or None if there is no
        score.

        Returns:
          dictionary
             {'score': integer, from 0 to get_max_score(),
              'total': get_max_score()}

          NOTE (vshnayder): not sure if this was the intended return value, but
          that's what it's doing now.  I suspect that we really want it to just
          return a number.  Would need to change (at least) capa to match if we did that.
        """
        return None

    def max_score(self):
        """ Maximum score. Two notes:

            * This is generic; in abstract, a problem could be 3/5 points on one
              randomization, and 5/7 on another

            * In practice, this is a Very Bad Idea, and (a) will break some code
              in place (although that code should get fixed), and (b) break some
              analytics we plan to put in place.
        """
        return None

    def get_progress(self):
        """ Return a progress.Progress object that represents how far the
        student has gone in this module.  Must be implemented to get correct
        progress tracking behavior in nesting modules like sequence and
        vertical.

        If this module has no notion of progress, return None.
        """
        return None

    def bind_for_student(self, user_id, wrappers=None):
        """
        Set up this XBlock to act as an XModule instead of an XModuleDescriptor.

        Arguments:
            user_id: The user_id to set in scope_ids
            wrappers: These are a list functions that put a wrapper, such as
                      LmsFieldData or OverrideFieldData, around the field_data.
                      Note that the functions will be applied in the order in
                      which they're listed. So [f1, f2] -> f2(f1(field_data))
        """

        # Skip rebinding if we're already bound a user, and it's this user.
        if self.scope_ids.user_id is not None and user_id == self.scope_ids.user_id:
            if getattr(self.runtime, 'position', None):
                self.position = self.runtime.position   # update the position of the tab
            return

        # If we are switching users mid-request, save the data from the old user.
        self.save()

        # Update scope_ids to point to the new user.
        self.scope_ids = self.scope_ids._replace(user_id=user_id)

        # Clear out any cached instantiated children.
        self.clear_child_cache()

        # Clear out any cached field data scoped to the old user.
        for field in self.fields.values():  # lint-amnesty, pylint: disable=no-member
            if field.scope in (Scope.parent, Scope.children):
                continue

            if field.scope.user == UserScope.ONE:
                field._del_cached_value(self)  # pylint: disable=protected-access
                # not the most elegant way of doing this, but if we're removing
                # a field from the module's field_data_cache, we should also
                # remove it from its _dirty_fields
                if field in self._dirty_fields:
                    del self._dirty_fields[field]

        if wrappers:
            # Put user-specific wrappers around the field-data service for this block.
            # Note that these are different from modulestore.xblock_field_data_wrappers, which are not user-specific.
            wrapped_field_data = self.runtime.service(self, 'field-data-unbound')
            for wrapper in wrappers:
                wrapped_field_data = wrapper(wrapped_field_data)
            self._bound_field_data = wrapped_field_data
            if getattr(self.runtime, "uses_deprecated_field_data", False):
                # This approach is deprecated but OldModuleStoreRuntime still requires it.
                # For SplitModuleStoreRuntime, don't set ._field_data this way.
                self._field_data = wrapped_field_data

    @property
    def non_editable_metadata_fields(self):
        """
        Return the list of fields that should not be editable in Studio.

        When overriding, be sure to append to the superclasses' list.
        """
        # We are not allowing editing of xblock tag and name fields at this time (for any component).
        return [XBlock.tags, XBlock.name]

    @property
    def editable_metadata_fields(self):
        """
        Returns the metadata fields to be edited in Studio. These are fields with scope `Scope.settings`.

        Can be limited by extending `non_editable_metadata_fields`.
        """
        metadata_fields = {}

        # Only use the fields from this class, not mixins
        fields = getattr(self, 'unmixed_class', self.__class__).fields

        for field in fields.values():
            if field in self.non_editable_metadata_fields:
                continue
            if field.scope not in (Scope.settings, Scope.content):
                continue

            metadata_fields[field.name] = self._create_metadata_editor_info(field)

        return metadata_fields

    def _create_metadata_editor_info(self, field):
        """
        Creates the information needed by the metadata editor for a specific field.
        """
        def jsonify_value(field, json_choice):
            """
            Convert field value to JSON, if needed.
            """
            if isinstance(json_choice, dict):
                new_json_choice = dict(json_choice)  # make a copy so below doesn't change the original
                if 'display_name' in json_choice:
                    new_json_choice['display_name'] = get_text(json_choice['display_name'])
                if 'value' in json_choice:
                    new_json_choice['value'] = field.to_json(json_choice['value'])
            else:
                new_json_choice = field.to_json(json_choice)
            return new_json_choice

        def get_text(value):
            """Localize a text value that might be None."""
            if value is None:
                return None
            else:
                return self.runtime.service(self, "i18n").ugettext(value)

        # gets the 'default_value' and 'explicitly_set' attrs
        metadata_field_editor_info = self.runtime.get_field_provenance(self, field)
        metadata_field_editor_info['field_name'] = field.name
        metadata_field_editor_info['display_name'] = get_text(field.display_name)
        metadata_field_editor_info['help'] = get_text(field.help)
        metadata_field_editor_info['value'] = field.read_json(self)

        # We support the following editors:
        # 1. A select editor for fields with a list of possible values (includes Booleans).
        # 2. Number editors for integers and floats.
        # 3. A generic string editor for anything else (editing JSON representation of the value).
        editor_type = "Generic"
        values = field.values
        if "values_provider" in field.runtime_options:
            values = field.runtime_options['values_provider'](self)
        if isinstance(values, (tuple, list)) and len(values) > 0:
            editor_type = "Select"
            values = [jsonify_value(field, json_choice) for json_choice in values]
        elif isinstance(field, Integer):
            editor_type = "Integer"
        elif isinstance(field, Float):
            editor_type = "Float"
        elif isinstance(field, List):
            editor_type = "List"
        elif isinstance(field, Dict):
            editor_type = "Dict"
        elif isinstance(field, RelativeTime):
            editor_type = "RelativeTime"
        elif isinstance(field, String) and field.name == "license":
            editor_type = "License"
        metadata_field_editor_info['type'] = editor_type
        metadata_field_editor_info['options'] = [] if values is None else values

        return metadata_field_editor_info

    def public_view(self, _context):
        """
        Default message for blocks that don't implement public_view
        """
        alert_html = HTML(
            '<div class="page-banner"><div class="alert alert-warning">'
            '<span class="icon icon-alert fa fa fa-warning" aria-hidden="true"></span>'
            '<div class="message-content">{}</div></div></div>'
        )

        if self.display_name:
            display_text = _(
                '{display_name} is only accessible to enrolled learners. '
                'Sign in or register, and enroll in this course to view it.'
            ).format(
                display_name=self.display_name
            )
        else:
            display_text = _(DEFAULT_PUBLIC_VIEW_MESSAGE)  # lint-amnesty, pylint: disable=translation-of-non-string

        return Fragment(alert_html.format(display_text))


class XModuleToXBlockMixin:
    """
    Common code needed by XModule and XBlocks converted from XModules.
    """
    @property
    def ajax_url(self):
        """
        Returns the URL for the ajax handler.
        """
        return self.runtime.handler_url(self, 'xmodule_handler', '', '').rstrip('/?')

    @XBlock.handler
    def xmodule_handler(self, request, suffix=None):
        """
        XBlock handler that wraps `handle_ajax`
        """
        class FileObjForWebobFiles:
            """
            Turn Webob cgi.FieldStorage uploaded files into pure file objects.

            Webob represents uploaded files as cgi.FieldStorage objects, which
            have a .file attribute.  We wrap the FieldStorage object, delegating
            attribute access to the .file attribute.  But the files have no
            name, so we carry the FieldStorage .filename attribute as the .name.

            """
            def __init__(self, webob_file):
                self.file = webob_file.file
                self.name = webob_file.filename

            def __getattr__(self, name):
                return getattr(self.file, name)

        # WebOb requests have multiple entries for uploaded files.  handle_ajax
        # expects a single entry as a list.
        request_post = MultiDict(request.POST)
        for key in set(request.POST.keys()):
            if hasattr(request.POST[key], "file"):
                request_post[key] = list(map(FileObjForWebobFiles, request.POST.getall(key)))

        response_data = self.handle_ajax(suffix, request_post)
        return Response(response_data, content_type='application/json', charset='UTF-8')


def policy_key(location):
    """
    Get the key for a location in a policy file.  (Since the policy file is
    specific to a course, it doesn't need the full location url).
    """
    return f'{location.block_type}/{location.block_id}'


Template = namedtuple("Template", "metadata data children")


class ResourceTemplates:
    """
    Gets the yaml templates associated with a containing cls for display in the Studio.

    The cls must have a 'template_dir_name' attribute. It finds the templates as directly
    in this directory under 'templates'.

    Additional templates can be loaded by setting the
    CUSTOM_RESOURCE_TEMPLATES_DIRECTORY configuration setting.

    Note that a template must end with ".yaml" extension otherwise it will not be
    loaded.
    """
    template_packages = [__name__]

    @classmethod
    def _load_template(cls, template_path, template_id):
        """
        Reads an loads the yaml content provided in the template_path and
        return the content as a dictionary.
        """
        if not os.path.exists(template_path):
            return None

        with open(template_path) as file_object:
            template = yaml.safe_load(file_object)
            template['template_id'] = template_id
            return template

    @classmethod
    def _load_templates_in_dir(cls, dirpath):
        """
        Lists every resource template found in the provided dirpath.
        """
        templates = []
        for template_file in os.listdir(dirpath):
            if not template_file.endswith('.yaml'):
                log.warning("Skipping unknown template file %s", template_file)
                continue

            template = cls._load_template(os.path.join(dirpath, template_file), template_file)
            templates.append(template)
        return templates

    @classmethod
    def templates(cls):
        """
        Returns a list of dictionary field: value objects that describe possible templates that can be used
        to seed a module of this type.

        Expects a class attribute template_dir_name that defines the directory
        inside the 'templates' resource directory to pull templates from.
        """
        templates = {}

        for dirpath in cls.get_template_dirpaths():
            for template in cls._load_templates_in_dir(dirpath):
                templates[template['template_id']] = template

        return list(templates.values())

    @classmethod
    def get_template_dir(cls):  # lint-amnesty, pylint: disable=missing-function-docstring
        if getattr(cls, 'template_dir_name', None):
            dirname = os.path.join('templates', cls.template_dir_name)  # lint-amnesty, pylint: disable=no-member
            template_path = resources.files(__name__.rsplit('.', 1)[0]) / dirname

            if not template_path.is_dir():
                log.warning("No resource directory {dir} found when loading {cls_name} templates".format(
                    dir=dirname,
                    cls_name=cls.__name__,
                ))
                return
            return dirname
        return

    @classmethod
    def get_template_dirpaths(cls):
        """
        Returns of list of directories containing resource templates.
        """
        template_dirpaths = []
        template_dirname = cls.get_template_dir()
        if template_dirname:
            template_path = resources.files(__name__.rsplit('.', 1)[0]) / template_dirname
            if template_path.is_dir():
                with resources.as_file(template_path) as template_real_path:
                    template_dirpaths.append(str(template_real_path))

        custom_template_dir = cls.get_custom_template_dir()
        if custom_template_dir:
            template_dirpaths.append(custom_template_dir)
        return template_dirpaths

    @classmethod
    def get_custom_template_dir(cls):
        """
        If settings.CUSTOM_RESOURCE_TEMPLATES_DIRECTORY is defined, check if it has a
        subdirectory named as the class's template_dir_name and return the full path.
        """
        template_dir_name = getattr(cls, 'template_dir_name', None)

        if template_dir_name is None:
            return

        resource_dir = settings.CUSTOM_RESOURCE_TEMPLATES_DIRECTORY

        if not resource_dir:
            return None

        template_dir_path = os.path.join(resource_dir, template_dir_name)

        if os.path.exists(template_dir_path):
            return template_dir_path
        return None

    @classmethod
    def get_template(cls, template_id):
        """
        Get a single template by the given id (which is the file name identifying it w/in the class's
        template_dir_name)
        """
        for directory in sorted(cls.get_template_dirpaths(), reverse=True):
            abs_path = os.path.join(directory, template_id)
            if os.path.exists(abs_path):
                return cls._load_template(abs_path, template_id)


class _ConfigurableFragmentWrapper:
    """
    Runtime mixin that allows for composition of many `wrap_xblock` wrappers
    """
    def __init__(self, wrappers=None, wrappers_asides=None, **kwargs):
        """
        :param wrappers: A list of wrappers, where each wrapper is:

            def wrapper(block, view, frag, context):
                ...
                return wrapped_frag
        """
        super().__init__(**kwargs)
        if wrappers is not None:
            self.wrappers = wrappers
        else:
            self.wrappers = []
        if wrappers_asides is not None:
            self.wrappers_asides = wrappers_asides
        else:
            self.wrappers_asides = []

    def wrap_xblock(self, block, view, frag, context):
        """
        See :func:`Runtime.wrap_child`
        """
        for wrapper in self.wrappers:
            frag = wrapper(block, view, frag, context)

        return frag

    def wrap_aside(self, block, aside, view, frag, context):    # pylint: disable=unused-argument
        """
        See :func:`Runtime.wrap_child`
        """
        for wrapper in self.wrappers_asides:
            frag = wrapper(aside, view, frag, context)

        return frag


# This function exists to give applications (LMS/CMS) a place to monkey-patch until
# we can refactor modulestore to split out the FieldData half of its interface from
# the Runtime part of its interface. This function mostly matches the
# Runtime.handler_url interface.
#
# The monkey-patching happens in cms/djangoapps/xblock_config/apps.py and lms/djangoapps/lms_xblock/apps.py
def block_global_handler_url(block, handler_name, suffix='', query='', thirdparty=False):
    """
    See :meth:`xblock.runtime.Runtime.handler_url`.
    """
    raise NotImplementedError("Applications must monkey-patch this function before using handler_url for studio_view")


# This function exists to give applications (LMS/CMS) a place to monkey-patch until
# we can refactor modulestore to split out the FieldData half of its interface from
# the Runtime part of its interface. This function matches the Runtime.local_resource_url interface
#
# The monkey-patching happens in cms/djangoapps/xblock_config/apps.py and lms/djangoapps/lms_xblock/apps.py
def block_global_local_resource_url(block, uri):
    """
    See :meth:`xblock.runtime.Runtime.local_resource_url`.
    """
    raise NotImplementedError("Applications must monkey-patch this function before using local_resource_url for studio_view")  # lint-amnesty, pylint: disable=line-too-long


class _MetricsMixin:
    """
    Mixin for adding metric logging for render and handle methods in the ModuleStoreRuntime.
    """

    def render(self, block, view_name, context=None):  # lint-amnesty, pylint: disable=missing-function-docstring
        context = context or {}
        start_time = time.time()
        try:
            return super().render(block, view_name, context=context)
        finally:
            end_time = time.time()
            duration = end_time - start_time
            log.debug(
                "%.3fs - render %s.%s (%s)",
                duration,
                block.__class__.__name__,
                view_name,
                getattr(block, 'location', ''),
            )

    def handle(self, block, handler_name, request, suffix=''):  # lint-amnesty, pylint: disable=missing-function-docstring
        start_time = time.time()
        try:
            return super().handle(block, handler_name, request, suffix=suffix)
        finally:
            end_time = time.time()
            duration = end_time - start_time
            log.debug(
                "%.3fs - handle %s.%s (%s)",
                duration,
                block.__class__.__name__,
                handler_name,
                getattr(block, 'location', ''),
            )


class _ModuleSystemShim:
    """
    This shim provides the properties formerly available from ModuleSystem which are now being provided by services.

    This shim will be removed, so all properties raise a deprecation warning.
    """

    @property
    def anonymous_student_id(self):
        """
        Returns the anonymous user ID for the current user and course.

        Deprecated in favor of the user service.

        NOTE: This method returns a course-specific anonymous user ID. If you are looking for the student-specific one,
              use `ATTR_KEY_DEPRECATED_ANONYMOUS_USER_ID` from the user service.
        """
        warnings.warn(
            'runtime.anonymous_student_id is deprecated. Please use the user service instead.',
            DeprecationWarning, stacklevel=3,
        )
        user_service = self._services.get('user')
        if user_service:
            return user_service.get_current_user().opt_attrs.get(ATTR_KEY_ANONYMOUS_USER_ID)
        return None

    @property
    def seed(self):
        """
        Returns the numeric current user id, for use as a random seed.
        Returns 0 if there is no current user.

        Deprecated in favor of the user service.
        """
        warnings.warn(
            'runtime.seed is deprecated. Please use the user service `user_id` instead.',
            DeprecationWarning, stacklevel=2,
        )
        return self.user_id or 0

    @property
    def user_id(self):
        """
        Returns the current user id, or None if there is no current user.

        Deprecated in favor of the user service.
        """
        warnings.warn(
            'runtime.user_id is deprecated. Use block.scope_ids.user_id or the user service instead.',
            DeprecationWarning, stacklevel=2,
        )
        user_service = self._services.get('user')
        if user_service:
            return user_service.get_current_user().opt_attrs.get(ATTR_KEY_USER_ID)
        return None

    @property
    def user_is_staff(self):
        """
        Returns whether the current user has staff access to the course.

        Deprecated in favor of the user service.
        """
        warnings.warn(
            'runtime.user_is_staff is deprecated. Please use the user service instead.',
            DeprecationWarning, stacklevel=2,
        )
        user_service = self._services.get('user')
        if user_service:
            return user_service.get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_STAFF)
        return None

    @property
    def user_location(self):
        """
        Returns the "country code" associated with the current user's request IP address.

        Deprecated in favor of the user service.
        """
        warnings.warn(
            'runtime.user_location is deprecated. Please use the user service instead.',
            DeprecationWarning, stacklevel=2,
        )
        user_service = self._services.get('user')
        if user_service:
            return user_service.get_current_user().opt_attrs.get(ATTR_KEY_REQUEST_COUNTRY_CODE)
        return None

    @property
    def get_real_user(self):
        """
        Returns a function that takes `anonymous_student_id` and returns the Django User object
        associated with `anonymous_student_id`.

        If no `anonymous_student_id` is provided as an argument to this function, then the user service's anonymous user
        ID is used instead.

        Deprecated in favor of the user service.
        """
        warnings.warn(
            'runtime.get_real_user is deprecated. Please use the user service instead.',
            DeprecationWarning, stacklevel=2,
        )
        user_service = self._services.get('user')
        if user_service:
            return user_service.get_user_by_anonymous_id
        return None

    @property
    def get_user_role(self):
        """
        Returns a function that returns the user's role in the course.

        Implementation is different for LMS and Studio.

        Deprecated in favor of the user service.
        """
        warnings.warn(
            'runtime.get_user_role is deprecated. Please use the user service instead.',
            DeprecationWarning, stacklevel=2,
        )
        user_service = self._services.get('user')
        if user_service:
            return partial(user_service.get_current_user().opt_attrs.get, ATTR_KEY_USER_ROLE)

    @property
    def user_is_beta_tester(self):
        """
        Returns whether the current user is enrolled in the course as a beta tester.

        Deprecated in favor of the user service.
        """
        warnings.warn(
            'runtime.user_is_beta_tester is deprecated. Please use the user service instead.',
            DeprecationWarning, stacklevel=2,
        )
        user_service = self._services.get('user')
        if user_service:
            return user_service.get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_BETA_TESTER)

    @property
    def user_is_admin(self):
        """
        Returns whether the current user has global staff permissions.

        Deprecated in favor of the user service.
        """
        warnings.warn(
            'runtime.user_is_admin is deprecated. Please use the user service instead.',
            DeprecationWarning, stacklevel=2,
        )
        user_service = self._services.get('user')
        if user_service:
            return user_service.get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_GLOBAL_STAFF)

    @property
    def render_template(self):
        """
        Returns a function that takes (template_file, context), and returns rendered html.

        Deprecated in favor of the mako service.
        """
        warnings.warn(
            'Use of runtime.render_template is deprecated. '
            'Use MakoService.render_template or a JavaScript-based template instead.',
            DeprecationWarning, stacklevel=2,
        )
        if hasattr(self, '_deprecated_render_template'):
            return self._deprecated_render_template
        render_service = self._services.get('mako')
        if render_service:
            return render_service.render_template
        return None

    @render_template.setter
    def render_template(self, render_template):
        """
        Set render_template for backwards compatibility.

        Using this is deprecated in favor of the mako service.
        """
        self._deprecated_render_template = render_template

    @property
    def can_execute_unsafe_code(self):
        """
        Returns a function which returns a boolean, indicating whether or not to allow the execution of unsafe,
        unsandboxed code.

        Deprecated in favor of the sandbox service.
        """
        warnings.warn(
            'runtime.can_execute_unsafe_code is deprecated. Please use the sandbox service instead.',
            DeprecationWarning, stacklevel=2,
        )
        sandbox_service = self._services.get('sandbox')
        if sandbox_service:
            return sandbox_service.can_execute_unsafe_code
        # Default to saying "no unsafe code".
        return lambda: False

    @property
    def get_python_lib_zip(self):
        """
        Returns a function returning a bytestring or None.

        The bytestring is the contents of a zip file that should be importable by other Python code running in the
        module.

        Deprecated in favor of the sandbox service.
        """
        warnings.warn(
            'runtime.get_python_lib_zip is deprecated. Please use the sandbox service instead.',
            DeprecationWarning, stacklevel=2,
        )
        sandbox_service = self._services.get('sandbox')
        if sandbox_service:
            return sandbox_service.get_python_lib_zip
        # Default to saying "no lib data"
        return lambda: None

    @property
    def cache(self):
        """
        Returns a cache object with two methods:
        * .get(key) returns an object from the cache or None.
        * .set(key, value, timeout_secs=None) stores a value in the cache with a timeout.

        Deprecated in favor of the cache service.
        """
        warnings.warn(
            'runtime.cache is deprecated. Please use the cache service instead.',
            DeprecationWarning, stacklevel=2,
        )
        return self._services.get('cache') or DoNothingCache()

    @property
    def filestore(self):
        """
        A filestore ojbect. Defaults to an instance of OSFS based at settings.DATA_DIR.

        Deprecated in favor of runtime.resources_fs property.
        """
        warnings.warn(
            'runtime.filestore is deprecated. Please use the runtime.resources_fs service instead.',
            DeprecationWarning, stacklevel=2,
        )
        return self.resources_fs

    @property
    def node_path(self):
        """
        Path to node_modules. Doesn't seem to be used by any ModuleSystem dependent core XBlock anymore.

        Deprecated.
        """
        warnings.warn(
            'node_path is deprecated. Please use other methods of finding the node_modules location.',
            DeprecationWarning, stacklevel=2,
        )

    @property
    def hostname(self):
        """
        Hostname of the site as set in the Django settings `LMS_BASE`
        Deprecated in favour of direct import of `django.conf.settings`
        """
        warnings.warn(
            'runtime.hostname is deprecated. Please use `LMS_BASE` from `django.conf.settings`.',
            DeprecationWarning, stacklevel=2,
        )
        return settings.LMS_BASE

    @property
    def rebind_noauth_module_to_user(self):
        """
        A function that was used to bind modules initialized by AnonymousUsers to real users. Mainly used
        by the LTI Block to connect the right users with the requests from LTI tools.

        Deprecated in favour of the "rebind_user" service.
        """
        warnings.warn(
            "rebind_noauth_module_to_user is deprecated. Please use the 'rebind_user' service instead.",
            DeprecationWarning, stacklevel=2,
        )
        rebind_user_service = self._services.get('rebind_user')
        if rebind_user_service:
            return partial(rebind_user_service.rebind_noauth_module_to_user)

    # noinspection PyPep8Naming
    @property
    def STATIC_URL(self):  # pylint: disable=invalid-name
        """
        Returns the base URL for static assets.
        Deprecated in favor of the settings.STATIC_URL configuration.
        """
        warnings.warn(
            'runtime.STATIC_URL is deprecated. Please use settings.STATIC_URL instead.',
            DeprecationWarning, stacklevel=2,
        )
        return settings.STATIC_URL

    @property
    def course_id(self):
        """
        Old API to get the course ID.

        Deprecated in favor of `block.scope_ids.usage_id.context_key`.
        """
        warnings.warn(
            "`runtime.course_id` is deprecated. Use `context_key` instead: `block.scope_ids.usage_id.context_key`.",
            DeprecationWarning, stacklevel=2,
        )
        if hasattr(self, '_deprecated_course_id'):
            return self._deprecated_course_id.for_branch(None)

    @course_id.setter
    def course_id(self, course_id):
        """
        Set course_id, for backwards compatibility. Reading from this is deprecated.
        """
        self._deprecated_course_id = course_id


class ModuleStoreRuntime(_MetricsMixin, _ConfigurableFragmentWrapper, _ModuleSystemShim, Runtime):
    """
    Base class for :class:`Runtime`s to be used with :class:`XBlock`s loaded from ModuleStore.
    """
    def __init__(
        self,
        load_item,
        resources_fs,
        error_tracker,
        get_policy=None,
        render_template=None,
        disabled_xblock_types=lambda: [],
        **kwargs
    ):
        """
        load_item: Takes a Location and returns an XModuleDescriptor

        resources_fs: A Filesystem object that contains all of the
            resources needed for the course

        error_tracker: A hook for tracking errors in loading the descriptor.
            Used for example to get a list of all non-fatal problems on course
            load, and display them to the user.

            See errortracker.py for more documentation

        get_policy: a function that takes a usage id and returns a dict of
            policy to apply.

        local_resource_url: an implementation of :meth:`xblock.runtime.Runtime.local_resource_url`

        """
        kwargs.setdefault('id_reader', OpaqueKeyReader())
        kwargs.setdefault('id_generator', AsideKeyGenerator())
        super().__init__(**kwargs)

        # This is used by XModules to write out separate files during xml export
        self.export_fs = None

        self.load_item = load_item
        self.resources_fs = resources_fs
        self.error_tracker = error_tracker
        if get_policy:
            self.get_policy = get_policy
        else:
            self.get_policy = lambda u: {}
        if render_template:
            self.render_template = render_template
            # Add the MakoService to the runtime services. If it already exists, do not attempt to reinitialize it;
            # otherwise, this could override the `namespace_prefix` of the `MakoService`, breaking template rendering in
            # Studio.
            #
            # This is not needed by most XBlocks, because the MakoService is added to their runtimes. However, there are
            # a few cases where the MakoService is not added to the XBlock's runtime. Specifically: * in the Instructor
            # Dashboard bulk emails tab, when rendering the HtmlBlock for its WYSIWYG editor. * during testing, when
            # fetching factory-created blocks.
            if 'mako' not in self._services:
                from common.djangoapps.edxmako.services import MakoService
                self._services['mako'] = MakoService()

        self.disabled_xblock_types = disabled_xblock_types

    def get(self, attr):
        """	provide uniform access to attributes (like etree)."""
        return self.__dict__.get(attr)

    def set(self, attr, val):
        """provide uniform access to attributes (like etree)"""
        self.__dict__[attr] = val

    def get_block(self, usage_id, for_parent=None):
        """See documentation for `xblock.runtime:Runtime.get_block`"""
        block = self.load_item(usage_id, for_parent=for_parent)
        # get_block_for_descriptor property is used to bind additional data such as user data
        # to the XBlock and to check if the user has access to the block as may be required for
        # the LMS or Preview.
        if getattr(self, 'get_block_for_descriptor', None):
            return self.get_block_for_descriptor(block)
        return block

    def load_block_type(self, block_type):
        """
        Returns a subclass of :class:`.XBlock` that corresponds to the specified `block_type`.
        """
        if block_type in self.disabled_xblock_types():
            return self.default_class
        return super().load_block_type(block_type)

    def get_field_provenance(self, xblock, field):
        """
        For the given xblock, return a dict for the field's current state:
        {
            'default_value': what json'd value will take effect if field is unset: either the field default or
            inherited value,
            'explicitly_set': boolean for whether the current value is set v default/inherited,
        }
        :param xblock:
        :param field:
        """
        # pylint: disable=protected-access
        # in runtime b/c runtime contains app-specific xblock behavior. Studio's the only app
        # which needs this level of introspection right now. runtime also is 'allowed' to know
        # about the kvs, dbmodel, etc.

        result = {}
        result['explicitly_set'] = xblock._field_data.has(xblock, field.name)
        try:
            result['default_value'] = xblock._field_data.default(xblock, field.name)
        except KeyError:
            result['default_value'] = field.to_json(field.default)
        return result

    def handler_url(self, block, handler_name, suffix='', query='', thirdparty=False):
        # When the Modulestore instantiates ModuleStoreRuntime, we will reference a
        # global function that the application can override, unless a specific function is
        # defined for LMS/CMS through the handler_url_override property.
        if getattr(self, 'handler_url_override', None):
            return self.handler_url_override(block, handler_name, suffix, query, thirdparty)
        return block_global_handler_url(block, handler_name, suffix, query, thirdparty)

    def local_resource_url(self, block, uri):
        """
        See :meth:`xblock.runtime.Runtime:local_resource_url` for documentation.
        """
        # Currently, Modulestore is responsible for instantiating ModuleStoreRuntime
        # This means that LMS/CMS don't have a way to define a subclass of ModuleStoreRuntime
        # that implements the correct local_resource_url. So, for now, instead, we will reference a
        # global function that the application can override.
        return block_global_local_resource_url(block, uri)

    def applicable_aside_types(self, block):
        """
        See :meth:`xblock.runtime.Runtime:applicable_aside_types` for documentation.
        """
        # applicable_aside_types_override property can be used by LMS/CMS to define specific filters
        # and conditions as may be applicable.
        if getattr(self, 'applicable_aside_types_override', None):
            return self.applicable_aside_types_override(block, applicable_aside_types=super().applicable_aside_types)

        potential_set = set(super().applicable_aside_types(block))
        return list(potential_set)

    def resource_url(self, resource):
        """
        See :meth:`xblock.runtime.Runtime:resource_url` for documentation.
        """
        raise NotImplementedError("edX Platform doesn't currently implement XBlock resource urls")

    def add_block_as_child_node(self, block, node):
        child = etree.SubElement(node, block.category)
        child.set('url_name', block.url_name)
        block.add_xml_to_node(child)

    def publish(self, block, event_type, event):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Publish events through the `EventPublishingService`.
        This ensures that the correct track method is used for Instructor tasks.
        """
        if publish_service := self._services.get('publish'):
            publish_service.publish(block, event_type, event)

    def service(self, block, service_name):
        """
        Runtime-specific override for the XBlock service manager.  If a service is not currently
        instantiated and is declared as a critical requirement, an attempt is made to load the
        module.

        Arguments:
            block (an XBlock): this block's class will be examined for service
                decorators.
            service_name (string): the name of the service requested.

        Returns:
            An object implementing the requested service, or None.
        """
        # Getting the service from parent module. making sure of block service declarations.
        service = super().service(block=block, service_name=service_name)
        # Passing the block to service if it is callable e.g. XBlockI18nService. It is the responsibility of calling
        # service to handle the passing argument.
        if callable(service):
            return service(block)
        return service

    def wrap_aside(self, block, aside, view, frag, context):
        # LMS/CMS can define custom wrap aside using wrap_asides_override as required.
        if getattr(self, 'wrap_asides_override', None):
            return self.wrap_asides_override(block, aside, view, frag, context, request_token=self.request_token)
        return super().wrap_aside(block, aside, view, frag, context)

    def layout_asides(self, block, context, frag, view_name, aside_frag_fns):
        # LMS/CMS can define custom layout aside using layout_asides_override as required.
        if getattr(self, 'layout_asides_override', None):
            return self.layout_asides_override(block, context, frag, view_name, aside_frag_fns)
        return super().layout_asides(block, context, frag, view_name, aside_frag_fns)


class DoNothingCache:
    """A duck-compatible object to use in ModuleSystemShim when there's no cache."""
    def get(self, _key):
        return None

    def set(self, key, value, timeout=None):
        pass
