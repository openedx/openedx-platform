"""
Structured Tagging based on XBlockAsides
"""
from django.conf import settings
from web_fragments.fragment import Fragment
from webob import Response
from xblock.core import XBlock, XBlockAside
from xblock.fields import Dict, Scope

from common.djangoapps.edxmako.shortcuts import render_to_string
from xmodule.capa_block import ProblemBlock  # pylint: disable=wrong-import-order
from xmodule.x_module import AUTHOR_VIEW  # pylint: disable=wrong-import-order

_ = lambda text: text


class StructuredTagsAside(XBlockAside):
    """
    Aside that allows tagging blocks
    """
    saved_tags = Dict(help=_("Dictionary with the available tags"),
                      scope=Scope.content,
                      default={},)

    def get_available_tags(self):
        """
        Return available tags
        """
        # Import is placed here to avoid model import at project startup.
        from .models import TagCategories
        return TagCategories.objects.all()

    def _get_studio_resource_url(self, relative_url):
        """
        Returns the Studio URL to a static resource.
        """
        return settings.STATIC_URL + relative_url

    @XBlockAside.aside_for(AUTHOR_VIEW)
    def student_view_aside(self, block, context):  # pylint: disable=unused-argument
        """
        Display the tag selector with specific categories and allowed values,
        depending on the context.
        """
        if isinstance(block, ProblemBlock):
            tags = []
            for tag in self.get_available_tags():
                tag_available_values = tag.get_values()
                tag_current_values = self.saved_tags.get(tag.name, [])

                if isinstance(tag_current_values, str):
                    tag_current_values = [tag_current_values]

                tag_values_not_exists = [cur_val for cur_val in tag_current_values
                                         if cur_val not in tag_available_values]

                tag_values_available_to_choose = tag_available_values + tag_values_not_exists
                tag_values_available_to_choose.sort()

                tags.append({
                    'key': tag.name,
                    'title': tag.title,
                    'values': tag_values_available_to_choose,
                    'current_values': tag_current_values,
                })
            fragment = Fragment(render_to_string('structured_tags_block.html', {'tags': tags,
                                                                                'tags_count': len(tags),
                                                                                'block_location': block.location}))
            fragment.add_javascript_url(self._get_studio_resource_url('js/xblock_asides/structured_tags.js'))
            fragment.initialize_js('StructuredTagsInit')
            return fragment
        else:
            return Fragment('')

    @XBlock.handler
    def save_tags(self, request=None, suffix=None):  # pylint: disable=unused-argument
        """
        Handler to save choosen tags with connected XBlock
        """
        try:
            posted_data = request.json
        except ValueError:
            return Response("Invalid request body", status=400)

        saved_tags = {}
        need_update = False

        for av_tag in self.get_available_tags():
            if av_tag.name in posted_data and posted_data[av_tag.name]:
                tag_available_values = av_tag.get_values()
                tag_current_values = self.saved_tags.get(av_tag.name, [])

                if isinstance(tag_current_values, str):
                    tag_current_values = [tag_current_values]

                for posted_tag_value in posted_data[av_tag.name]:
                    if posted_tag_value not in tag_available_values and posted_tag_value not in tag_current_values:
                        return Response("Invalid tag value was passed: %s" % posted_tag_value, status=400)  # noqa: UP031  # pylint: disable=line-too-long

                saved_tags[av_tag.name] = posted_data[av_tag.name]
                need_update = True
            if av_tag.name in posted_data:
                need_update = True

        if need_update:
            self.saved_tags = saved_tags
            return Response()
        else:
            return Response("Tags parameters were not passed", status=400)

    def get_event_context(self, event_type, event):  # pylint: disable=unused-argument
        """
        This method return data that should be associated with the "check_problem" event
        """
        if self.saved_tags and event_type == "problem_check":
            return {'saved_tags': self.saved_tags}
        else:
            return None
