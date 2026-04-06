"""
Allows the registration of Django/Python settings that are derived from other settings
via callable methods/lambdas. The derivation time can be controlled to happen after all
other settings have been set. The derived setting can also be overridden by setting the
derived setting to an actual value.
"""
from __future__ import annotations

import re
import sys
import types
import typing as t

Settings: t.TypeAlias = types.ModuleType


T = t.TypeVar('T')


class Derived(t.Generic[T]):
    """
    A temporary Django setting value, defined with a function which generates the setting's eventual value.

    Said function (`calculate_value`) should accept a Django settings module, and return a calculated value.

    To ensure that application code does not encounter an instance of this class in your settings, be sure to call
    `derive_settings` somewhere in your terminal settings file.
    """
    def __init__(self, calculate_value: t.Callable[[Settings], T]):
        self.calculate_value = calculate_value


def derive_settings(module_name: str) -> None:
    """
    In the Django settings module at `module_name`, replace `Derived` values with their cacluated values.

    The replacement happens recursively for any values or containers defined by a Django setting name (which is: an
    uppercase top-level variable name which is not prefixed by an underscore). Within containers,
    """
    module = sys.modules[module_name]
    _derive_dict(module, vars(module), key_filter=_key_is_a_setting_name)


_SETTING_NAME_REGEX = re.compile(r'^[A-Z][A-Z0-9_]*$')


def _key_is_a_setting_name(key: str) -> bool:
    return bool(_SETTING_NAME_REGEX.match(key))


def _match_every_key(_key: str) -> bool:
    return True


def _derive_recursively(settings: Settings, value: t.Any) -> t.Any:
    """
    Recursively evaluate `Derived` objects` in `value` and any child containers. Return evaluated version of `value`.

    * If `value` is a `Derived` object, then use `settings` to calculate and return its value.
    * If `value` is a mutable container, then recursively evaluate it in-place.
    * If `value` is an immutable container, then recursively evalute a shallow copy of it.
      Keep in mind that immutable containers (particularly: tuples) can contain mutable containers. In such a case, the
      original and shallow-copied mutable containers will both reference the same child mutable container object.
    """
    if isinstance(value, Derived):
        return value.calculate_value(settings)
    elif isinstance(value, dict):
        return _derive_dict(settings, value)
    elif isinstance(value, list):
        return _derive_list(settings, value)
    elif isinstance(value, tuple):
        return _derive_tuple(settings, value)
    elif isinstance(value, frozenset):
        return _derive_frozenset(settings, value)
    else:
        return value


def _derive_dict(settings: Settings, the_dict: dict, key_filter: t.Callable[[str], bool] = _match_every_key) -> dict:
    """
    Recursively evaluate `Derived` objects in `the_dict` and any child containers. Modifies `the_dict` in place.

    Optionally takes a `key_filter`. Items that do not match the provided `key_filter` will be left alone.
    """
    for key, value in the_dict.items():
        if key_filter(key):
            the_dict[key] = _derive_recursively(settings, value)
    return the_dict


def _derive_list(settings: Settings, the_list: list) -> list:
    """
    Recursively evaluate `Derived` objects in `the_list` and any child containers. Modifies `the_list` in place.
    """
    for ix in range(len(the_list)):
        the_list[ix] = _derive_recursively(settings, the_list[ix])
    return the_list


def _derive_tuple(settings: Settings, tup: tuple) -> tuple:
    """
    Recursively evaluate `Derived` objects in `tup` and any child containers. Returns a shallow copy of `tup`.
    """
    return tuple(_derive_recursively(settings, item) for item in tup)


def _derive_set(settings: Settings, the_set: set) -> set:
    """
    Recursively evaluate `Derived` objects in `the_set` and any child containers. Modifies `the_set` in-place.
    """
    for original in the_set:
        derived = _derive_recursively(settings, original)
        if derived != original:
            the_set.remove(original)
            the_set.add(derived)
    return the_set


def _derive_frozenset(settings: Settings, the_set: frozenset) -> frozenset:
    """
    Recursively evaluate `Derived` objects in `the_set` and any child containers. Returns a shallow copy of `the_set`.
    """
    return frozenset(_derive_recursively(settings, item) for item in the_set)
