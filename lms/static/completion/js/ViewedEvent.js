/** Ensure that a function is only run once every `wait` milliseconds */
// eslint-disable-next-line max-classes-per-file
function throttle(fn, wait) {
    let time = 0;
    function delay() {
    // Do not call the function until at least `wait` seconds after the
    // last time the function was called.
        const now = Date.now();
        if (time + wait < now) {
            time = now;
            fn();
        }
    }
    return delay;
}

export class ElementViewing {
    /**
   * A wrapper for an HTMLElement that tracks whether the element has been
   * viewed or not.
   */
    constructor(el, viewedAfterMs, callback) {
        this.el = el;
        this.viewedAfterMs = viewedAfterMs;
        this.callback = callback;

        this.topSeen = false;
        this.bottomSeen = false;
        this.tallerThanViewport = false;
        this.seenForMs = 0;
        this.becameVisibleAt = undefined;
        this.hasBeenViewed = false;
    }

    getBoundingRect() {
        return this.el.getBoundingClientRect();
    }

    /** This element has become visible on screen.
   *
   * (may be called even when already on screen though)
   */
    handleVisible() {
        if (!this.becameVisibleAt) {
            this.becameVisibleAt = Date.now();
            // We're now visible; after viewedAfterMs, if the top and bottom have been
            // seen, this block will count as viewed.
            setTimeout(
                () => {
                    this.checkIfViewed();
                },
                this.viewedAfterMs - this.seenForMs,
            );
        }
    }

    handleNotVisible() {
        if (this.becameVisibleAt) {
            this.seenForMs = Date.now() - this.becameVisibleAt;
        }
        this.becameVisibleAt = undefined;
    }

    markTopSeen() {
    // If this element has been seen for enough time, but the top wasn't visible, it may now be
    // considered viewed.
        this.topSeen = true;
        this.checkIfViewed();
    }

    markBottomSeen() {
        this.bottomSeen = true;
        this.checkIfViewed();
    }

    /** This element is taller than the viewport.
   *
   * Its top and bottom edges can never be on screen simultaneously, so
   * requiring both to have been seen would make it permanently incompletable.
   */
    markTallerThanViewport() {
        this.tallerThanViewport = true;
        this.checkIfViewed();
    }

    getTotalTimeSeen() {
        if (this.becameVisibleAt) {
            return this.seenForMs + (Date.now() - this.becameVisibleAt);
        }
        return this.seenForMs;
    }

    areViewedCriteriaMet() {
        if (this.getTotalTimeSeen() < this.viewedAfterMs) {
            return false;
        }
        if (this.topSeen && this.bottomSeen) {
            return true;
        }
        // A component taller than the viewport can never show both edges at
        // once. Requiring both makes such components permanently incompletable
        // for any learner who does not scroll their full height, which is the
        // common case for content meant to be clicked rather than read through.
        return this.tallerThanViewport;
    }

    checkIfViewed() {
    // User can provide a "now" value for testing purposes.
        if (this.hasBeenViewed) {
            return;
        }
        if (this.areViewedCriteriaMet()) {
            this.hasBeenViewed = true;
            // Report to the tracker that we have been viewed
            this.callback(this.el, { elementHasBeenViewed: this.hasBeenViewed });
        }
    }
}

export class ViewedEventTracker {
    /**
   * When the top or bottom of an element is first viewed, and the entire
   * element is viewed for a specified amount of time, the callback is called,
   * passing the element that was viewed, and an event object having the
   * following field:
   *
   * *   hasBeenViewed (bool): true if all the conditions for being
   *     considered "viewed" have been met.
   */
    constructor() {
        this.elementViewings = new Set();
        this.handlers = [];
        if (window === window.parent) {
          // Preview (legacy LMS frontend).
          this.registerDomHandlers();
        } else {
          // Learning MFE.
          window.addEventListener('message', this.handleVisibilityMessage.bind(this));
        }
    }

    /** Add an element to track.  */
    addElement(element, viewedAfterMs) {
        this.elementViewings.add(
            new ElementViewing(
                element,
                viewedAfterMs,
                (el, event) => this.callHandlers(el, event),
            ),
        );
        // Update visibility status immediately after adding the element (in case it's already visible).
        // We don't need this for the Learning MFE because it will send a message once the iframe is loaded.
        if (window === window.parent) {
          this.updateVisible();
        }
    }

    /** Register a new handler to be called when an element has been viewed.  */
    addHandler(handler) {
        this.handlers.push(handler);
    }

    /** Mark which elements are currently visible.
   *
   *  Also marks when an elements top or bottom has been seen.
   * */
    updateVisible() {
        this.elementViewings.forEach((elv) => {
            if (elv.hasBeenViewed) {
                return;
            }

            const now = Date.now(); // Use the same "now" for all calculations
            const rect = elv.getBoundingRect();
            let visible = false;

            if (rect.top > 0 && rect.top < window.innerHeight) {
                elv.markTopSeen(now);
                visible = true;
            }
            if (rect.bottom > 0 && rect.bottom < window.innerHeight) {
                elv.markBottomSeen(now);
                visible = true;
            }
            if (rect.top < 0 && rect.bottom > window.innerHeight) {
                visible = true;
            }
            if (rect.bottom - rect.top > window.innerHeight) {
                elv.markTallerThanViewport();
            }

            if (visible) {
                elv.handleVisible(now);
            } else {
                elv.handleNotVisible(now);
            }
        });
    }

    registerDomHandlers() {
        window.onscroll = throttle(() => this.updateVisible(), 100);
        window.onresize = throttle(() => this.updateVisible(), 100);
        this.updateVisible();
    }

    /** Call the handlers for all newly-viewed elements and pause tracking
   *  for recently disappeared elements.
   */
    callHandlers(el, event) {
        this.handlers.forEach((handler) => {
            handler(el, event);
        });
    }

    /** Handle a unit.visibilityStatus message from the Learning MFE. */
    handleVisibilityMessage(event) {
        if (event.data.type === 'unit.visibilityStatus') {
          const { topPosition, viewportHeight } = event.data.data;

          this.elementViewings.forEach((elv) => {
              const rect = elv.getBoundingRect();
              let visible = false;

              // Convert iframe-relative rect coordinates to be relative to the parent's viewport.
              const elTopPosition = rect.top + topPosition;
              const elBottomPosition = rect.bottom + topPosition;

              // Check if the element is visible in the parent's viewport.
              if (elTopPosition < viewportHeight && elTopPosition >= 0) {
                  elv.markTopSeen();
                  visible = true;
              }
              if (elBottomPosition <= viewportHeight && elBottomPosition > 0) {
                  elv.markBottomSeen();
                  visible = true;
              }
              // The element currently fills the viewport. Parity with the
              // legacy path, which already counts this as visible.
              if (elTopPosition < 0 && elBottomPosition > viewportHeight) {
                  visible = true;
              }
              if (elBottomPosition - elTopPosition > viewportHeight) {
                  elv.markTallerThanViewport();
              }

              if (visible) {
                  elv.handleVisible();
              } else {
                  elv.handleNotVisible();
              }
          });
      }
    }
}
