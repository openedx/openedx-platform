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

    _NOT_OVERRIDDEN = object()

    def _resolve(self, key):
        """Return key's value if it's been overridden via @override_settings, else self._NOT_OVERRIDDEN.

        We deliberately walk only the UserSettingsHolder chain (the layers that
        @override_settings pushes onto django.conf.settings._wrapped) rather
        than reading django.conf.settings.X directly. Django's bottom Settings
        layer is a *snapshot* taken at init time, so it doesn't reflect runtime
        mutations of the settings module's globals (which is what
        proxy.ns mutations and legacy patch.dict(settings.FEATURES, ...) do).
        Walking only the explicit override layers lets the new
        @override_settings(X=Y) path work while leaving the legacy patch.dict
        path untouched.
        """
        from django.conf import settings as django_settings
        wrapped = django_settings._wrapped  # pylint: disable=protected-access
        # UserSettingsHolder has a `default_settings` attribute and stores
        # explicit overrides in its own __dict__; the bottom Settings has no
        # default_settings, so the loop terminates there.
        while hasattr(wrapped, 'default_settings'):
            if key in wrapped.__dict__:
                return wrapped.__dict__[key]
            wrapped = wrapped.default_settings
        return self._NOT_OVERRIDDEN

    def __getitem__(self, key):
        """Retrieve a feature flag by key, preferring @override_settings overrides."""
        value = self._resolve(key)
        if value is not self._NOT_OVERRIDDEN:
            return value
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
        return self._resolve(key) is not self._NOT_OVERRIDDEN or key in self.ns

    def clear(self):
        """Remove all feature flags from the namespace."""
        self.ns.clear()

    def get(self, key, default=None):
        """Standard dict-style get with default; prefers @override_settings overrides."""
        value = self._resolve(key)
        if value is not self._NOT_OVERRIDDEN:
            return value
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
