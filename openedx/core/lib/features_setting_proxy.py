"""
Features Proxy Implementation
"""
import warnings
from collections.abc import Mapping, MutableMapping


class FeaturesProxy(MutableMapping):
    """
    A proxy for feature flags stored in the settings namespace.

    Features:
    - Flattens features from configuration (e.g., YAML or env) into the local settings namespace.
    - Automatically updates `django.conf.settings` when a feature is modified.
    - Acts like a dict (get, set, update, etc.).

    Example usage:
        fp = FeaturesProxy(LocalNamespace)
        fp["NEW_FEATURE"] = True
        val = fp.get("EXISTING_FLAG", False)
    """

    def __init__(self, namespace=None):
        """Store the namespace (as a dict)"""
        self.ns = namespace or {}

    def __getitem__(self, key):
        """Retrieve a feature flag by key"""
        return self.ns[key]

    def __setitem__(self, key, value):
        """Sets a key-value pair while emitting a deprecation warning about using FEATURES as a dict."""
        warnings.warn(
            f"Accessing FEATURES as a dict is deprecated. "
            f"Add '{key} = {value!r}' to your Django settings module instead of modifying FEATURES.",
            DeprecationWarning,
            stacklevel=2
        )
        self.ns[key] = value

    def __delitem__(self, key):
        """Remove a feature flag from the namespace."""
        del self.ns[key]

    def __iter__(self):
        return iter(self.ns)

    def __len__(self):
        return len(self.ns)

    def __contains__(self, key):
        return key in self.ns

    def clear(self):
        """Remove all feature flags from the namespace."""
        self.ns.clear()

    def get(self, key, default=None):
        """Standard dict-style get with default"""
        return self.ns.get(key, default)

    def update(self, other=(), /, **kwds):
        """
        Update multiple features at once, ensuring each goes through __setitem__
        to emit deprecation warnings.

        Mirrors dict.update() behavior:
        - If `other` is a mapping, uses its keys.
        - If `other` is iterable of pairs, updates from those.
        - Then applies any keyword arguments.

        Examples:
            proxy.update({'FEATURE_A': True})
                -> other = {'FEATURE_A': True}

            proxy.update([('FEATURE_A', True)])
                -> other = [('FEATURE_A', True)]

            proxy.update(FEATURE_B=False)
                -> kwds = {'FEATURE_B': False}

            proxy.update({'FEATURE_A': True}, FEATURE_B=False)
                -> other={'FEATURE_A': True}; kwds = {'FEATURE_B': False}
        """
        if isinstance(other, Mapping):
            # Handles objects that formally conform to the Mapping interface
            # Mapping-like types: defaultdict, OrderedDict, Counter
            for key in other:
                self[key] = other[key]
        elif hasattr(other, "keys"):
            # Fallback for objects that implement a .keys() method but
            # may not formally subclass Mapping
            for key in other.keys():
                self[key] = other[key]
        else:
            for key, value in other:
                self[key] = value
        for key, value in kwds.items():
            self[key] = value

    def copy(self):
        """
        Return a shallow copy of the underlying namespace wrapped in a new FeaturesProxy.
        """
        return FeaturesProxy(self.ns.copy())
