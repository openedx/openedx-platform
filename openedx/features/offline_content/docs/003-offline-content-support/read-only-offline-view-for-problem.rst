Read-only offline_view for problem
==================================

A proposal for the read-only ``offline_view`` of the problem block in this scope ("Phase 2"), built so that the
next scope ("Phase 3", interactive offline problems) extends it and does not replace it. It supports `ADR-003`_
and assumes the runtime changes in `offline_view for xblock-poll`_. Names are placeholders.

Why read-only should not be a separate rendering
------------------------------------------------

The quick way to get a read-only problem is a second template with the inputs disabled and no buttons. That
leaves three renderings of a problem (online, read-only, and later interactive offline), and the read-only one
is discarded in Phase 3.

The alternative: ``offline_view`` uses the same templates and the same JavaScript as ``student_view``, rendered
for a learner with no state. Read-only is then something the JavaScript does when the runtime tells it handlers
cannot be called. Mechanically:

1. **Content.** The problem rendered from its XML and settings with empty state and a fixed seed. The markup is
   what a learner sees on first visit, including the Submit button.
2. **State.** None in Phase 2. The page never shows attempts, saved answers or correctness.
3. **Interactions.** None in Phase 2. The JavaScript asks the runtime whether handlers are available. If not,
   it disables the inputs and buttons and shows a note that answers are accepted online.

Phase 3 changes step 3 only: the JavaScript learns to queue a submission and show it as pending. The content
render and the archive format stay as they are.

Runtime changes
---------------

Beyond what the survey needs:

Backend
~~~~~~~

* **A user-less sandbox.** Python-scripted problems run their script when the problem is built, so the
  generating runtime must provide the sandbox service on the CMS workers. Everything else the render needs
  (i18n, URL replacement, the cache) is already user-independent.
* **Nothing else.** The rules from the survey proposal apply as written: reading a user-scoped field or calling
  ``handler_url`` during an ``offline_view`` render raises.

Frontend
~~~~~~~~

* **A way to ask what handlers can do, before the learner types anything.** For example
  ``runtime.handlerMode(element)``, returning ``"live"``, ``"queued"`` or ``"none"``. The LMS and Studio
  runtimes return ``"live"``. The archive runtime returns ``"queued"``. An anonymous runtime returns
  ``"none"``.
* **The LMS page globals the problem script uses.** The block's bundle contains its own loader, collapsible and
  input scripts, but expects ``jQuery``, ``$.postWithPrefix``, ``Logger``, ``edx.HtmlUtils``, ``gettext`` and
  ``MathJax`` from the page. The archive page has to supply them. This list should be written down and tested,
  because it will otherwise drift.

Implementing it in the problem block
------------------------------------

Python (``xblock_problem/block.py``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Add a user-less problem constructor.** Today the ``lcp`` property reads the user service and writes
   ``seed`` and ``score`` into user state while rendering. Add a separate method that builds a
   ``LoncapaProblem`` with ``state={}``, ``seed=1``, no anonymous id, no xqueue and no handler URL.
   ``max_score`` already builds one in a similar way.
2. **Parameterise the render.** ``get_html`` and ``get_problem_html`` read ``self.lcp`` and user fields
   (``attempts``, ``has_saved_answers``) directly. Change them to take the problem and a small state object as
   arguments. ``student_view`` passes the learner's; ``offline_view`` passes the user-less problem and an empty
   state. Both go through ``problem_ajax.html``, ``problem.html`` and the input templates unchanged.
3. **Add** ``offline_view``. It declines, before rendering, when:

   * ``rerandomize`` is anything but "never";
   * a response type is outside the supported set. The six already marked ``multi_device_support`` (choice,
     multiple choice, option, numerical, string, formula) are the starting list;
   * an input type needs the server to display or to respond to typing (MATLAB, JS input, chemical equation,
     code graded through xqueue). Formula input is a judgement call: it works without its live preview.

4. **Omit the handler URL.** ``problem_ajax.html`` writes it into ``data-url``. In ``offline_view`` the
   attribute is absent.

``student_view`` keeps its current behaviour exactly: same markup, same single request. Existing usages are
unaffected.

JavaScript (``assets/static/js/display.js``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Use the** ``runtime`` **argument.** The constructor currently ignores it. Read
   ``runtime.handlerMode(element)`` once, defaulting to ``"live"`` when the method is missing so the block
   still works on older platforms.
2. **Add a read-only mode.** When the mode is not ``"live"``, after the normal render and input setup: disable
   every input and action button, skip the bindings that call the server, and show the "answers are accepted
   online" note. The first paint already comes from the ``data-content`` attribute and makes no request.
3. **Leave the live path alone.** The existing ``$.postWithPrefix`` calls stay for Phase 2. Moving them to
   ``runtime.callHandler`` is Phase 3 work.

The Phase 2 script treats ``"queued"`` the same as ``"none"``, because it cannot yet show a pending submission.
Archives built in Phase 2 therefore stay read-only until they are regenerated with the Phase 3 script.

What this leaves for Phase 3
----------------------------

* **Pending submissions.** Restoring a queued answer into each supported input type on reload, and showing
  "submitted, not yet graded" in place of correctness.
* ``student_view`` **literally on top of** ``offline_view``. The ``problem_get`` handler already returns the
  state-rendered HTML and the script already knows how to swap it in. That is the route to serving the cached
  fragment online and hydrating it, at the cost of one request on load.
* **Buttons that depend on time or attempts.** Show Answer, Reset, and the Submit cutoff after the due date
  cannot be decided at publish time. The archive needs either state from the application or a rule that the
  server's answer on replay is final.
* **Moving server calls to the runtime.** Capa uses one handler with a dispatch suffix
  (``xmodule_handler/problem_check``), so ``callHandler`` needs to accept a suffix, or capa needs named
  handlers.

.. _ADR-003: ../003-offline-content-generation-architecture.rst
.. _offline_view for xblock-poll: offline-view-for-xblock-poll.rst
