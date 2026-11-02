'use strict';

import HtmlUtils from 'edx-ui-toolkit/js/utils/html-utils';
import _ from 'underscore';


let template = HtmlUtils.interpolateHtml(
    HtmlUtils.HTML([
        '<button class="control quality-control is-hidden" aria-disabled="false" title="',
        '{highDefinition}',
        '">',
        '<span class="icon icon-hd" aria-hidden="true">HD</span>',
        '<span class="sr text-translation">',
        '{highDefinition}',
        '</span>&nbsp;',
        '<span class="sr control-text">',
        '{off}',
        '</span>',
        '</button>'
    ].join('')),
    {
        highDefinition: gettext('High Definition'),
        off: gettext('off')
    }
);

// VideoQualityControl() function - what this module "exports".
let VideoQualityControl = function(state) {
    let dfd = $.Deferred();

    // Changing quality for now only works for YouTube videos.
    if (state.videoType !== 'youtube') {
        return;
    }

    state.videoQualityControl = {};

    _makeFunctionsPublic(state);
    _renderElements(state);
    _bindHandlers(state);

    dfd.resolve();
    return dfd.promise();
};

// ***************************************************************
// Private functions start here.
// ***************************************************************

// function _makeFunctionsPublic(state)
//
//     Functions which will be accessible via 'state' object. When called, these functions will
//     get the 'state' object as a context.
function _makeFunctionsPublic(state) {
    let methodsDict = {
        destroy: destroy,
        fetchAvailableQualities: fetchAvailableQualities,
        onQualityChange: onQualityChange,
        showQualityControl: showQualityControl,
        toggleQuality: toggleQuality
    };

    state.bindTo(methodsDict, state.videoQualityControl, state);
}

function destroy() {
    this.videoQualityControl.el.off({
        click: this.videoQualityControl.toggleQuality,
        destroy: this.videoQualityControl.destroy
    });
    this.el.off('.quality');
    this.videoQualityControl.el.remove();
    delete this.videoQualityControl;
}

// function _renderElements(state)
//
//     Create any necessary DOM elements, attach them, and set their initial configuration. Also
//     make the created DOM elements available via the 'state' object. Much easier to work this
//     way - you don't have to do repeated jQuery element selects.
function _renderElements(state) {
    // eslint-disable-next-line no-multi-assign
    let element = state.videoQualityControl.el = $(template.toString());
    state.videoQualityControl.quality = 'large';
    HtmlUtils.append(state.el.find('.secondary-controls'), HtmlUtils.HTML(element));
}

// function _bindHandlers(state)
//
//     Bind any necessary function callbacks to DOM events (click, mousemove, etc.).
function _bindHandlers(state) {
    state.videoQualityControl.el.on('click',
        state.videoQualityControl.toggleQuality
    );
    state.el.on('play.quality', _.once(
        state.videoQualityControl.fetchAvailableQualities
    ));

    state.el.on('destroy.quality', state.videoQualityControl.destroy);
}

// ***************************************************************
// Public functions start here.
// These are available via the 'state' object. Their context ('this' keyword) is the 'state' object.
// The magic private function that makes them available and sets up their context is makeFunctionsPublic().
// ***************************************************************

/*
 * @desc Shows quality control. This function will only be called if HD
 *       qualities are available.
 *
 * @public
 */
function showQualityControl() {
    this.videoQualityControl.el.removeClass('is-hidden');
}

// This function can only be called once as _.once has been used.
/*
 * @desc Get the available qualities from YouTube API. Possible values are:
         ['highres', 'hd1080', 'hd720', 'large', 'medium', 'small'].
         HD are: ['highres', 'hd1080', 'hd720'].
 *
 * @public
 */
function fetchAvailableQualities() {
    let qualities = this.videoPlayer.player.getAvailableQualityLevels();

    this.config.availableHDQualities = _.intersection(
        qualities, ['highres', 'hd1080', 'hd720']
    );

    // HD qualities are available, show video quality control.
    if (this.config.availableHDQualities.length > 0) {
        this.trigger('videoQualityControl.showQualityControl');
        this.trigger('videoQualityControl.onQualityChange', this.videoQualityControl.quality);
    }
    // On initialization, force the video quality to be 'large' instead of
    // 'default'. Otherwise, the player will sometimes switch to HD
    // automatically, for example when the iframe resizes itself.
    this.trigger('videoPlayer.handlePlaybackQualityChange',
        this.videoQualityControl.quality
    );
}

function onQualityChange(value) {
    let controlStateStr;
    this.videoQualityControl.quality = value;
    if (_.contains(this.config.availableHDQualities, value)) {
        controlStateStr = gettext('on');
        this.videoQualityControl.el
            .addClass('active')
            .find('.control-text')
            .text(controlStateStr);
    } else {
        controlStateStr = gettext('off');
        this.videoQualityControl.el
            .removeClass('active')
            .find('.control-text')
            .text(controlStateStr);
    }
}

// This function toggles the quality of video only if HD qualities are
// available.
function toggleQuality(event) {
    let value = this.videoQualityControl.quality;
    let isHD = _.contains(this.config.availableHDQualities, value);
    let newQuality = isHD ? 'large' : 'highres';

    event.preventDefault();

    this.trigger('videoPlayer.handlePlaybackQualityChange', newQuality);
}

export default VideoQualityControl;
