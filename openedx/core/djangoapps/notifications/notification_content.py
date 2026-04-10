"""
Helper functions for overriding notification content for given notification type.
"""
from typing import Dict  # noqa: UP035


def get_notification_type_context_function(notification_type) -> callable:
    """
    Returns:
         callable : The function that returns the context for the given notification type.
    """
    try:
        return globals()[f"get_{notification_type}_notification_context"]
    except KeyError:
        return lambda context: context


def get_notification_context_with_author_pronoun(context: Dict) -> Dict:  # noqa: UP006
    """
    Returns the context for the given notification type with the author pronoun.

    """
    html_tags_context = {
        'strong': 'strong',
        'p': 'p',
    }
    context.update(html_tags_context)
    if 'author_pronoun' in context:
        context['author_name'] = context['author_pronoun']
    return context


# Returns notification content for the new_comment notification.
get_new_comment_notification_context = get_notification_context_with_author_pronoun


# Returns notification content for the comment_on_followed_post notification.
get_comment_on_followed_post_notification_context = get_notification_context_with_author_pronoun
