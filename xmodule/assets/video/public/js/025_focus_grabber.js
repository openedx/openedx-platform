/*
 * 025_focus_grabber.js
 *
 * Purpose: Provide a way to focus on autohidden Video controls.
 *
 *
 * Because in HTML player mode we have a feature of autohiding controls on
 * mouse inactivity, sometimes focus is lost from the currently selected
 * control. What's more, when all controls are autohidden, we can't get to any
 * of them because by default browser does not place hidden elements on the
 * focus chain.
 *
 * To get around this minor annoyance, this module will manage 2 placeholder
 * elements that will be invisible to the user's eye, but visible to the
 * browser. This will allow for a sneaky stealing of focus and placing it where
 * we need (on hidden controls).
 *
 * This code has been moved to a separate module because it provides a concrete
 * block of functionality that can be turned on (off).
 */

/*
 * "If you want to climb a mountain, begin at the top."
 *
 * ~ Zen saying
 */



// FocusGrabber module.
let FocusGrabber = function(state) {
    let dfd = $.Deferred();

    state.focusGrabber = {};

    _makeFunctionsPublic(state);
    _renderElements(state);
    _bindHandlers(state);

    dfd.resolve();
    return dfd.promise();
};

// Private functions.

function _makeFunctionsPublic(state) {
    let methodsDict = {
        disableFocusGrabber: disableFocusGrabber,
        enableFocusGrabber: enableFocusGrabber,
        onFocus: onFocus
    };

    state.bindTo(methodsDict, state.focusGrabber, state);
}

function _renderElements(state) {
    state.focusGrabber.elFirst = state.el.find('.focus_grabber.first');
    state.focusGrabber.elLast = state.el.find('.focus_grabber.last');

    // From the start, the Focus Grabber must be disabled so that
    // tabbing (switching focus) does not land the user on one of the
    // placeholder elements (elFirst, elLast).
    state.focusGrabber.disableFocusGrabber();
}

function _bindHandlers(state) {
    state.focusGrabber.elFirst.on('focus', state.focusGrabber.onFocus);
    state.focusGrabber.elLast.on('focus', state.focusGrabber.onFocus);

    // When the video container element receives programmatic focus, then
    // on un-focus ('blur' event) we should trigger a 'mousemove' event so
    // as to reveal autohidden controls.
    state.el.on('blur', function() {
        state.el.trigger('mousemove');
    });
}

// Public functions.

function enableFocusGrabber() {
    let tabIndex;

    // When the Focus Grabber is being enabled, there are two different
    // scenarios:
    //
    //     1.) Currently focused element was inside the video player.
    //     2.) Currently focused element was somewhere else on the page.
    //
    // In the first case we must make sure that the video player doesn't
    // loose focus, even though the controls are autohidden.
    if ($(document.activeElement).parents().hasClass('video')) {
        tabIndex = -1;
    } else {
        tabIndex = 0;
    }

    this.focusGrabber.elFirst.attr('tabindex', tabIndex);
    this.focusGrabber.elLast.attr('tabindex', tabIndex);

    // Don't loose focus. We are inside video player on some control, but
    // because we can't remain focused on a hidden element, we will shift
    // focus to the main video element.
    //
    // Once the main element will receive the un-focus ('blur') event, a
    // 'mousemove' event will be triggered, and the video controls will
    // receive focus once again.
    if (tabIndex === -1) {
        this.el.focus();

        this.focusGrabber.elFirst.attr('tabindex', 0);
        this.focusGrabber.elLast.attr('tabindex', 0);
    }
}

function disableFocusGrabber() {
    // Only programmatic focusing on these elements will be available.
    // We don't want the user to focus on them (for example with the 'Tab'
    // key).
    this.focusGrabber.elFirst.attr('tabindex', -1);
    this.focusGrabber.elLast.attr('tabindex', -1);
}

function onFocus(event, params) {
    // Once the Focus Grabber placeholder elements will gain focus, we will
    // trigger 'mousemove' event so that the autohidden controls will
    // become visible.
    this.el.trigger('mousemove');

    this.focusGrabber.disableFocusGrabber();
}

export default FocusGrabber;
