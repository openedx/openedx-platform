"""Django integration utilities for loading and accessing the content store."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:
    from .content import ContentStore

_CONTENTSTORE: dict[str, ContentStore] = {}


def load_function(path):
    """
    Load a function by name.

    path is a string of the form "path.to.module.function"
    returns the imported python object `function` from `path.to.module`
    """
    module_path, _, name = path.rpartition(".")
    return getattr(import_module(module_path), name)


def contentstore(name: str = "default") -> ContentStore:
    """
    Return a contentstore instance by name, creating and caching it if not already initialized.

    The "default" contentstore routes each request to the appropriate backend for the course. Other named stores
    (i.e. the deprecated "trashcan") are always legacy stores.
    """
    if name not in _CONTENTSTORE:
        config: dict[str, Any] = settings.CONTENTSTORE
        class_ = load_function(config["ENGINE"])
        options: dict[str, Any] = {}
        options.update(config["DOC_STORE_CONFIG"])
        if "ADDITIONAL_OPTIONS" in config:
            if name in config["ADDITIONAL_OPTIONS"]:
                options.update(config["ADDITIONAL_OPTIONS"][name])
        store: ContentStore = class_(**options)
        if name == "default":
            # Imported here to avoid a circular import (content.py imports this module).
            from .routing import RoutingContentStore  # pylint: disable=import-outside-toplevel
            store = RoutingContentStore(legacy_store=store)
        _CONTENTSTORE[name] = store

    return _CONTENTSTORE[name]
