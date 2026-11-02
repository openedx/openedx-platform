import HtmlUtils from 'edx-ui-toolkit/js/utils/html-utils';

let template = [
    '<button class="control add-fullscreen" aria-disabled="false" title="',
    gettext('Fill browser'),
    '" aria-label="',
    gettext('Fill browser'),
    '">',
    '<span class="icon fa fa-arrows-alt" aria-hidden="true"></span>',
    '</button>'
].join('');

// The following properties and functions enable cross-browser use of the
// the Fullscreen Web API.
//
//     function getVendorPrefixed(property)
//     function getFullscreenElement()
//     function exitFullscreen()
//     function requestFullscreen(element, options)
//
//     For more information about the Fullscreen Web API see MDN:
//     https://developer.mozilla.org/en-US/docs/Web/API/Fullscreen_API
let prefixedFullscreenProperties = (function() {
    if ('fullscreenEnabled' in document) {
        return {
            fullscreenElement: 'fullscreenElement',
            fullscreenEnabled: 'fullscreenEnabled',
            requestFullscreen: 'requestFullscreen',
            exitFullscreen: 'exitFullscreen',
            fullscreenchange: 'fullscreenchange',
            fullscreenerror: 'fullscreenerror'
        };
    }
    if ('webkitFullscreenEnabled' in document) {
        return {
            fullscreenElement: 'webkitFullscreenElement',
            fullscreenEnabled: 'webkitFullscreenEnabled',
            requestFullscreen: 'webkitRequestFullscreen',
            exitFullscreen: 'webkitExitFullscreen',
            fullscreenchange: 'webkitfullscreenchange',
            fullscreenerror: 'webkitfullscreenerror'
        };
    }
    if ('mozFullScreenEnabled' in document) {
        return {
            fullscreenElement: 'mozFullScreenElement',
            fullscreenEnabled: 'mozFullScreenEnabled',
            requestFullscreen: 'mozRequestFullScreen',
            exitFullscreen: 'mozCancelFullScreen',
            fullscreenchange: 'mozfullscreenchange',
            fullscreenerror: 'mozfullscreenerror'
        };
    }
    if ('msFullscreenEnabled' in document) {
        return {
            fullscreenElement: 'msFullscreenElement',
            fullscreenEnabled: 'msFullscreenEnabled',
            requestFullscreen: 'msRequestFullscreen',
            exitFullscreen: 'msExitFullscreen',
            fullscreenchange: 'MSFullscreenChange',
            fullscreenerror: 'MSFullscreenError'
        };
    }
    return {};
}());

function getVendorPrefixed(property) {
    return prefixedFullscreenProperties[property];
}

function getFullscreenElement() {
    return document[getVendorPrefixed('fullscreenElement')];
}

function exitFullscreen() {
    if (document[getVendorPrefixed('exitFullscreen')]) {
        return document[getVendorPrefixed('exitFullscreen')]();
    }
    return null;
}

function requestFullscreen(element, options) {
    if (element[getVendorPrefixed('requestFullscreen')]) {
        return element[getVendorPrefixed('requestFullscreen')](options);
    }
    return null;
}

// ***************************************************************
// Private functions start here.
// ***************************************************************

function destroy() {
    $(document).off('keyup', this.videoFullScreen.exitHandler);
    this.videoFullScreen.fullScreenEl.remove();
    this.el.off({
        destroy: this.videoFullScreen.destroy
    });
    document.removeEventListener(
        getVendorPrefixed('fullscreenchange'),
        this.videoFullScreen.handleFullscreenChange
    );
    if (this.isFullScreen) {
        this.videoFullScreen.exit();
    }
    delete this.videoFullScreen;
}

// function renderElements(state)
//
//     Create any necessary DOM elements, attach them, and set their initial configuration. Also
//     make the created DOM elements available via the 'state' object. Much easier to work this
//     way - you don't have to do repeated jQuery element selects.
function renderElements(state) {
    /* eslint-disable no-param-reassign */
    state.videoFullScreen.fullScreenEl = $(template);
    state.videoFullScreen.sliderEl = state.el.find('.slider');
    state.videoFullScreen.fullScreenState = false;
    HtmlUtils.append(state.el.find('.secondary-controls'), HtmlUtils.HTML(state.videoFullScreen.fullScreenEl));
    state.videoFullScreen.updateControlsHeight();
    /* eslint-enable no-param-reassign */
}

// function bindHandlers(state)
//
//     Bind any necessary function callbacks to DOM events (click, mousemove, etc.).
function bindHandlers(state) {
    state.videoFullScreen.fullScreenEl.on('click', state.videoFullScreen.toggleHandler);
    state.el.on({
        destroy: state.videoFullScreen.destroy
    });
    $(document).on('keyup', state.videoFullScreen.exitHandler);
    document.addEventListener(
        getVendorPrefixed('fullscreenchange'),
        state.videoFullScreen.handleFullscreenChange
    );
}

function getControlsHeight(controls, slider) {
    return controls.height() + 0.5 * slider.height();
}

// ***************************************************************
// Public functions start here.
// These are available via the 'state' object. Their context ('this' keyword) is the 'state' object.
// The magic private function that makes them available and sets up their context is makeFunctionsPublic().
// ***************************************************************

function handleFullscreenChange() {
    if (getFullscreenElement() !== this.el[0] && this.isFullScreen) {
        // The video was fullscreen so this event must relate to this video
        this.videoFullScreen.handleExit();
    }
}

function updateControlsHeight() {
    let controls = this.el.find('.video-controls');
    let slider = this.videoFullScreen.sliderEl;
    this.videoFullScreen.height = getControlsHeight(controls, slider);
    return this.videoFullScreen.height;
}

function notifyParent(fullscreenOpen) {
    if (window !== window.parent) {
        // This is used by the Learning MFE to know about changing fullscreen mode.
        // The MFE is then able to respond appropriately and scroll window to the previous position.
        window.parent.postMessage({
            type: 'plugin.videoFullScreen',
            payload: {
                open: fullscreenOpen
            }
        }, document.referrer
        );
    }
}

/**
 * Event handler to toggle fullscreen mode.
 * @param {jquery Event} event
 */
function toggleHandler(event) {
    event.preventDefault();
    this.videoCommands.execute('toggleFullScreen');
}

function handleExit() {
    let fullScreenClassNameEl = this.el.add(document.documentElement);
    let closedCaptionsEl = this.el.find('.closed-captions');

    if (this.isFullScreen === false) {
        return;
    }

    // eslint-disable-next-line no-multi-assign
    this.videoFullScreen.fullScreenState = this.isFullScreen = false;
    fullScreenClassNameEl.removeClass('video-fullscreen');
    $(window).scrollTop(this.scrollPos);
    this.videoFullScreen.fullScreenEl
        .attr({title: gettext('Fill browser'), 'aria-label': gettext('Fill browser')})
        .find('.icon')
        .removeClass('fa-compress')
        .addClass('fa-arrows-alt');

    $(closedCaptionsEl).css({top: '70%', left: '5%'});
    if (this.resizer) {
        this.resizer.delta.reset().setMode('width');
    }
    this.el.trigger('fullscreen', [this.isFullScreen]);

    this.videoFullScreen.notifyParent(false);
}

function handleEnter() {
    let fullScreenClassNameEl = this.el.add(document.documentElement);
    let closedCaptionsEl = this.el.find('.closed-captions');

    if (this.isFullScreen === true) {
        return;
    }

    this.videoFullScreen.notifyParent(true);

    // eslint-disable-next-line no-multi-assign
    this.videoFullScreen.fullScreenState = this.isFullScreen = true;
    fullScreenClassNameEl.addClass('video-fullscreen');
    this.videoFullScreen.fullScreenEl
        .attr({title: gettext('Exit full browser'), 'aria-label': gettext('Exit full browser')})
        .find('.icon')
        .removeClass('fa-arrows-alt')
        .addClass('fa-compress');

    $(closedCaptionsEl).css({top: '70%', left: '5%'});
    if (this.resizer) {
        this.resizer.delta.substract(this.videoFullScreen.updateControlsHeight(), 'height').setMode('both');
    }
    this.el.trigger('fullscreen', [this.isFullScreen]);
}

function exit() {
    if (getFullscreenElement() === this.el[0]) {
        exitFullscreen();
    } else {
        // Else some other element is fullscreen or the fullscreen api does not exist.
        this.videoFullScreen.handleExit();
    }
}

function enter() {
    this.scrollPos = $(window).scrollTop();
    this.videoFullScreen.handleEnter();
    requestFullscreen(this.el[0]);
}

/** Toggle fullscreen mode. */
function toggle() {
    if (this.videoFullScreen.fullScreenState) {
        this.videoFullScreen.exit();
    } else {
        this.videoFullScreen.enter();
    }
}

/**
 * Event handler to exit from fullscreen mode.
 * @param {jquery Event} event
 */
function exitHandler(event) {
    if ((this.isFullScreen) && (event.keyCode === 27)) {
        event.preventDefault();
        this.videoCommands.execute('toggleFullScreen');
    }
}

// function makeFunctionsPublic(state)
//
//     Functions which will be accessible via 'state' object. When called, these functions will
//     get the 'state' object as a context.
function makeFunctionsPublic(state) {
    let methodsDict = {
        destroy: destroy,
        enter: enter,
        exit: exit,
        exitHandler: exitHandler,
        handleExit: handleExit,
        handleEnter: handleEnter,
        handleFullscreenChange: handleFullscreenChange,
        toggle: toggle,
        toggleHandler: toggleHandler,
        updateControlsHeight: updateControlsHeight,
        notifyParent: notifyParent
    };

    state.bindTo(methodsDict, state.videoFullScreen, state);
}

// VideoControl() function - what this module "exports".
export default function(state) {
    let dfd = $.Deferred();

    // eslint-disable-next-line no-param-reassign
    state.videoFullScreen = {};

    makeFunctionsPublic(state);
    renderElements(state);
    bindHandlers(state);

    dfd.resolve();
    return dfd.promise();
}
