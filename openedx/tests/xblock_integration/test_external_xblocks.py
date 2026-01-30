"""
This will run tests on all XBlocks in the `xblock.test.v0`
entrypoint. Did you notice something about that entry point? It ends
with a v0. That means this is not finished. At some point, we might
stop running v0 tests, replacing them with test case failures, and
run v1 tests only.

That be the dragon here.
"""


from importlib.metadata import entry_points


class DuplicateXBlockTest(Exception):
    '''
    This exception is shown if there are multiple entry points with the same
    class name for a test. In most cases, this means you have two versions
    of the same XBlock installed, or two XBlocks with namespace collisions. In
    either case, it'd be nice to resolve (likely by renaming tests as they
    come in, hopefully still being careful to catch collisions which might
    effect deployed XBlocks. See discussion at:
      https://github.com/openedx/edx-platform/pull/11032#discussion_r48097392).
    '''
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


class InvalidTestName(Exception):
    '''
    This means you have an entry point for a test that does not correspond
    to a properly named test class. For example, if you cut-and-paste entry
    points in your package configuration, and forgot to repoint the class
    (so it points to `DoneXBlock` instead of `TestDone`), or otherwise made
    an error, you will see this exception.
    '''
    pass  # lint-amnesty, pylint: disable=unnecessary-pass

xblock_loaded = False  # pylint: disable=invalid-name

for entrypoint in entry_points(group="xblock.test.v0"):
    plugin = entrypoint.load()
    classname = plugin.__name__
    if classname in globals():
        raise DuplicateXBlockTest(classname)
    if not classname.startswith("Test"):
        raise InvalidTestName("Test class should start with 'Test': " + classname)
    # This should never happen, but while we're testing for class name
    # validity, we figured it was okay to be a little overly defensive.
    # See discussion at:
    # https://github.com/openedx/edx-platform/pull/11032#discussion_r48097392
    if not classname.replace("_", "").isalnum():
        raise InvalidTestName("Python variables should be letters, numbers, and underscores: " + classname)
    globals()[classname] = plugin
    print("Loading XBlock test: " + classname)
    xblock_loaded = True
