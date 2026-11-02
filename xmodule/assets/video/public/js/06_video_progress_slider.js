/*
"This is as true in everyday life as it is in battle: we are given one life
and the decision is ours whether to wait for circumstances to make up our
mind, or whether to act, and in acting, to live."
— Omar N. Bradley
 */

// VideoProgressSlider module.
let template = [
    '<div class="slider" role="application" title="',
    gettext('Video position. Press space to toggle playback'),
    '"></div>'
].join('');

// VideoProgressSlider() function - what this module "exports".
export default function(state) {
    let dfd = $.Deferred();

    state.videoProgressSlider = {};
    _makeFunctionsPublic(state);
    _renderElements(state);

    dfd.resolve();
    return dfd.promise();
}

// ***************************************************************
// Private functions start here.
// ***************************************************************

// function _makeFunctionsPublic(state)
//
//     Functions which will be accessible via 'state' object. When called,
//     these functions will get the 'state' object as a context.

/* eslint-disable no-use-before-define */
function _makeFunctionsPublic(state) {
    let methodsDict = {
        destroy: destroy,
        buildSlider: buildSlider,
        getRangeParams: getRangeParams,
        onSlide: onSlide,
        onStop: onStop,
        updatePlayTime: updatePlayTime,
        updateStartEndTimeRegion: updateStartEndTimeRegion,
        notifyThroughHandleEnd: notifyThroughHandleEnd,
        getTimeDescription: getTimeDescription,
        focusSlider: focusSlider
    };

    state.bindTo(methodsDict, state.videoProgressSlider, state);
}

function destroy() {
    this.videoProgressSlider.el.removeAttr('tabindex').slider('destroy');
    this.el.off('destroy', this.videoProgressSlider.destroy);
    delete this.videoProgressSlider;
}

function bindHandlers(state) {
    state.videoProgressSlider.el.on('keypress', sliderToggle.bind(state));
    state.el.on('destroy', state.videoProgressSlider.destroy);
}
/* eslint-enable no-use-before-define */

// function _renderElements(state)
//
//     Create any necessary DOM elements, attach them, and set their
//     initial configuration. Also make the created DOM elements available
//     via the 'state' object. Much easier to work this way - you don't
//     have to do repeated jQuery element selects.
function _renderElements(state) {
    state.videoProgressSlider.el = $(template);

    state.el.find('.video-controls').prepend(state.videoProgressSlider.el);
    state.videoProgressSlider.buildSlider();
    _buildHandle(state);
    bindHandlers(state);
}

function _buildHandle(state) {
    state.videoProgressSlider.handle = state.videoProgressSlider.el
        .find('.ui-slider-handle');

    // ARIA
    // We just want the knob to be selectable with keyboard
    state.videoProgressSlider.el.attr({
        tabindex: -1
    });

    // Let screen readers know that this div, representing the slider
    // handle, behaves as a slider named 'video position'.
    state.videoProgressSlider.handle.attr({
        role: 'slider',
        'aria-disabled': false,
        'aria-valuetext': getTimeDescription(state.videoProgressSlider
            .slider.slider('option', 'value')),
        'aria-valuemax': state.videoPlayer.duration(),
        'aria-valuemin': '0',
        'aria-valuenow': state.videoPlayer.currentTime,
        tabindex: '0',
        'aria-label': gettext('Video position. Press space to toggle playback')
    });
}

// ***************************************************************
// Public functions start here.
// These are available via the 'state' object. Their context ('this'
// keyword) is the 'state' object. The magic private function that makes
// them available and sets up their context is makeFunctionsPublic().
// ***************************************************************

function buildSlider() {
    let sliderContents = edx.HtmlUtils.joinHtml(
        edx.HtmlUtils.HTML('<div class="ui-slider-handle progress-handle"></div>')
    );

    // xss-lint: disable=javascript-jquery-append
    this.videoProgressSlider.el.append(sliderContents.text);

    this.videoProgressSlider.slider = this.videoProgressSlider.el
        .slider({
            range: 'min',
            min: this.config.startTime,
            max: this.config.endTime,
            slide: this.videoProgressSlider.onSlide,
            stop: this.videoProgressSlider.onStop,
            step: 5
        });

    this.videoProgressSlider.sliderProgress = this.videoProgressSlider
        .slider
        .find('.ui-slider-range.ui-widget-header.ui-slider-range-min');
}

// Rebuild the slider start-end range (if it doesn't take up the
// whole slider). Remember that endTime === null means the end-time
// is set to the end of video by default.
function updateStartEndTimeRegion(params) {
    let start, end, duration, rangeParams;

    // We must have a duration in order to determine the area of range.
    // It also must be non-zero.
    if (!params.duration) {
        return;
    } else {
        duration = params.duration;
    }

    start = this.config.startTime;
    end = this.config.endTime;

    if (start > duration) {
        start = 0;
    } else if (this.isFlashMode()) {
        start /= Number(this.speed);
    }

    // If end is set to null, or it is greater than the duration of the
    // video, then we set it to the end of the video.
    if (end === null || end > duration) {
        end = duration;
    } else if (this.isFlashMode()) {
        end /= Number(this.speed);
    }

    // Don't build a range if it takes up the whole slider.
    if (start === 0 && end === duration) {
        return;
    }

    // Because JavaScript has weird rounding rules when a series of
    // mathematical operations are performed in a single statement, we will
    // split everything up into smaller statements.
    //
    // This will ensure that visually, the start-end range aligns nicely
    // with actual starting and ending point of the video.

    rangeParams = getRangeParams(start, end, duration);
}

function getRangeParams(startTime, endTime, duration) {
    let step = 100 / duration;
    let left = startTime * step;
    let width = endTime * step - left;

    return {
        left: left + '%',
        width: width + '%'
    };
}

function onSlide(event, ui) {
    let time = ui.value;
    let endTime = this.videoPlayer.duration();

    if (this.config.endTime) {
        endTime = Math.min(this.config.endTime, endTime);
    }

    this.videoProgressSlider.frozen = true;

    // Remember the seek to value so that we don't repeat ourselves on the
    // 'stop' slider event.
    this.videoProgressSlider.lastSeekValue = time;

    this.trigger(
        'videoControl.updateVcrVidTime',
        {
            time: time,
            duration: endTime
        }
    );

    this.trigger(
        'videoPlayer.onSlideSeek',
        {type: 'onSlideSeek', time: time}
    );

    // ARIA
    this.videoProgressSlider.handle.attr(
        'aria-valuetext', getTimeDescription(this.videoPlayer.currentTime)
    );
}

function onStop(event, ui) {
    let _this = this;

    this.videoProgressSlider.frozen = true;

    // Only perform a seek if we haven't made a seek for the new slider value.
    // This is necessary so that if the user only clicks on the slider, without
    // dragging it, then only one seek is made, even when a 'slide' and a 'stop'
    // events are triggered on the slider.
    if (this.videoProgressSlider.lastSeekValue !== ui.value) {
        this.trigger(
            'videoPlayer.onSlideSeek',
            {type: 'onSlideSeek', time: ui.value}
        );
    }

    // ARIA
    this.videoProgressSlider.handle.attr(
        'aria-valuetext', getTimeDescription(this.videoPlayer.currentTime)
    );

    setTimeout(function() {
        _this.videoProgressSlider.frozen = false;
    }, 200);
}

function updatePlayTime(params) {
    let time = Math.floor(params.time);
    // params.duration could accidentally be construed as a floating
    // point double. Since we're displaying this number, round down
    // to nearest second
    let endTime = Math.floor(params.duration);

    if (this.config.endTime !== null) {
        endTime = Math.min(this.config.endTime, endTime);
    }

    if (
        this.videoProgressSlider.slider
        && !this.videoProgressSlider.frozen
    ) {
        this.videoProgressSlider.slider
            .slider('option', 'max', endTime)
            .slider('option', 'value', time);
    }

    // Update aria values.
    this.videoProgressSlider.handle.attr({
        'aria-valuemax': endTime,
        'aria-valuenow': time
    });
}

// When the video stops playing (either because the end was reached, or
// because endTime was reached), the screen reader must be notified that
// the video is no longer playing. We do this by a little trick. Setting
// the title attribute of the slider know to "video ended", and focusing
// on it. The screen reader will read the attr text.
//
// The user can then tab their way forward, landing on the next control
// element, the Play button.
//
// @param params  -  object with property `end`. If set to true, the
//                   function must set the title attribute to
//                   `video ended`;
//                   if set to false, the function must reset the attr to
//                   it's original state.
//
// This function will be triggered from VideoPlayer methods onEnded(),
// onPlay(), and update() (update method handles endTime).
function notifyThroughHandleEnd(params) {
    if (params.end) {
        this.videoProgressSlider.handle
            .attr('title', gettext('Video ended'))
            .focus();
    } else {
        this.videoProgressSlider.handle
            .attr('title', gettext('Video position'));
    }
}

// Returns a string describing the current time of video in
// `%d hours %d minutes %d seconds` format.
function getTimeDescription(time) {
    let seconds = Math.floor(time);
    let minutes = Math.floor(seconds / 60);
    let hours = Math.floor(minutes / 60);
    let i18n = function(value, word) {
        let msg;

        // eslint-disable-next-line default-case
        switch (word) {
        case 'hour':
            msg = ngettext('%(value)s hour', '%(value)s hours', value);
            break;
        case 'minute':
            msg = ngettext('%(value)s minute', '%(value)s minutes', value);
            break;
        case 'second':
            msg = ngettext('%(value)s second', '%(value)s seconds', value);
            break;
        }
        return interpolate(msg, {value: value}, true);
    };

    seconds %= 60;
    minutes %= 60;

    if (hours) {
        return i18n(hours, 'hour') + ' '
        + i18n(minutes, 'minute') + ' '
        + i18n(seconds, 'second');
    } else if (minutes) {
        return i18n(minutes, 'minute') + ' '
        + i18n(seconds, 'second');
    }

    return i18n(seconds, 'second');
}

// Shift focus to the progress slider container element.
function focusSlider() {
    this.videoProgressSlider.handle.attr(
        'aria-valuetext', getTimeDescription(this.videoPlayer.currentTime)
    );
    this.videoProgressSlider.el.trigger('focus');
}

// Toggle video playback when the spacebar is pushed.
function sliderToggle(e) {
    if (e.which === 32) {
        e.preventDefault();
        this.videoCommands.execute('togglePlayback');
    }
}
