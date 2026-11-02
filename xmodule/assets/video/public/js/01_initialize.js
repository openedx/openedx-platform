/* eslint-disable no-console, no-param-reassign */
/**
 * @file Initialize module works with the JSON config, and sets up various
 * settings, parameters, variables. After all setup actions are performed, it
 * invokes the video player to play the specified video. This module must be
 * invoked first. It provides several functions which do not fit in with other
 * modules.
 *
 * @external VideoPlayer
 *
 * @module Initialize
 */

import VideoPlayer from './03_video_player.js';
import i18n from './00_i18n.js';
import _ from 'underscore';
import moment from 'moment';

/**
 * @function
 *
 * Initialize module exports this function.
 *
 * @param {object} state The object containg the state of the video player.
 *     All other modules, their parameters, public variables, etc. are
 *     available via this object.
 * @param {DOM element} element Container of the entire Video DOM element.
 */
let Initialize = function(state, element) {
    _makeFunctionsPublic(state);

    state.initialize(element)
        .done(function() {
            if (state.isYoutubeType()) {
                state.parseSpeed();
            }
            // On iPhones and iPods native controls are used.
            if (/iP(hone|od)/i.test(state.isTouch[0])) {
                _hideWaitPlaceholder(state);
                state.el.trigger('initialize', arguments);

                return false;
            }

            _initializeModules(state, i18n)
                .done(function() {
                    // On iPad ready state occurs just after start playing.
                    // We hide controls before video starts playing.
                    if (/iPad|Android/i.test(state.isTouch[0])) {
                        state.el.on('play', _.once(function() {
                            state.trigger('videoControl.show', null);
                        }));
                    } else {
                        // On PC show controls immediately.
                        state.trigger('videoControl.show', null);
                    }

                    _hideWaitPlaceholder(state);
                    state.el.trigger('initialize', arguments);
                });
        });
};

/* eslint-disable no-use-before-define */
let methodsDict = {
    bindTo: bindTo,
    fetchMetadata: fetchMetadata,
    getCurrentLanguage: getCurrentLanguage,
    getDuration: getDuration,
    getPlayerMode: getPlayerMode,
    getVideoMetadata: getVideoMetadata,
    initialize: initialize,
    isHtml5Mode: isHtml5Mode,
    isFlashMode: isFlashMode,
    isYoutubeType: isYoutubeType,
    parseSpeed: parseSpeed,
    parseYoutubeStreams: parseYoutubeStreams,
    setPlayerMode: setPlayerMode,
    setSpeed: setSpeed,
    setAutoAdvance: setAutoAdvance,
    speedToString: speedToString,
    trigger: trigger,
    youtubeId: youtubeId,
    loadHtmlPlayer: loadHtmlPlayer,
    loadYoutubePlayer: loadYoutubePlayer,
    loadYouTubeIFrameAPI: loadYouTubeIFrameAPI
};
/* eslint-enable no-use-before-define */

let _youtubeApiDeferred = null;
let _oldOnYouTubeIframeAPIReady;

Initialize.prototype = methodsDict;

export default Initialize;

// ***************************************************************
// Private functions start here. Private functions start with underscore.
// ***************************************************************

/**
 * @function _makeFunctionsPublic
 *
 * Functions which will be accessible via 'state' object. When called,
 * these functions will get the 'state'
 * object as a context.
 *
 * @param {object} state The object containg the state (properties,
 *     methods, modules) of the Video player.
 */
function _makeFunctionsPublic(state) {
    bindTo(methodsDict, state, state);
}

// function _renderElements(state)
//
//     Create any necessary DOM elements, attach them, and set their
//     initial configuration. Also make the created DOM elements available
//     via the 'state' object. Much easier to work this way - you don't
//     have to do repeated jQuery element selects.
function _renderElements(state) {
    // Launch embedding of actual video content, or set it up so that it
    // will be done as soon as the appropriate video player (YouTube or
    // stand-alone HTML5) is loaded, and can handle embedding.
    //
    // Note that the loading of stand alone HTML5 player API is handled by
    // Require JS. At the time when we reach this code, the stand alone
    // HTML5 player is already loaded, so no further testing in that case
    // is required.
    let video;
    let onYTApiReady;
    let setupOnYouTubeIframeAPIReady;

    if (state.videoType === 'youtube') {
        state.youtubeApiAvailable = false;

        onYTApiReady = function() {
            console.log('[Video info]: YouTube API is available and is loaded.');
            if (state.htmlPlayerLoaded) { return; }

            console.log('[Video info]: Starting YouTube player.');
            video = VideoPlayer(state);

            state.modules.push(video);
            state.__dfd__.resolve();
            state.youtubeApiAvailable = true;
        };

        if (window.YT) {
            // If we have a Deferred object responsible for calling OnYouTubeIframeAPIReady
            // callbacks, make sure that they have all been called by trying to resolve the
            // Deferred object. Upon resolving, all the OnYouTubeIframeAPIReady will be
            // called. If the object has been already resolved, the callbacks will not
            // be called a second time.
            if (_youtubeApiDeferred) {
                _youtubeApiDeferred.resolve();
            }

            window.YT.ready(onYTApiReady);
        } else {
            // There is only one global variable window.onYouTubeIframeAPIReady which
            // is supposed to be a function that will be called by the YouTube API
            // when it finished initializing. This function will update this global function
            // so that it resolves our Deferred object, which will call all of the
            // OnYouTubeIframeAPIReady callbacks.
            //
            // If this global function is already defined, we store it first, and make
            // sure that it gets executed when our Deferred object is resolved.
            setupOnYouTubeIframeAPIReady = function() {
                _oldOnYouTubeIframeAPIReady = window.onYouTubeIframeAPIReady || undefined;

                window.onYouTubeIframeAPIReady = function() {
                    _youtubeApiDeferred.resolve();
                };

                window.onYouTubeIframeAPIReady.done = _youtubeApiDeferred.done;

                if (_oldOnYouTubeIframeAPIReady) {
                    window.onYouTubeIframeAPIReady.done(_oldOnYouTubeIframeAPIReady);
                }
            };

            // If a Deferred object hasn't been created yet, create one now. It will
            // be responsible for calling OnYouTubeIframeAPIReady callbacks once the
            // YouTube API loads. After creating the Deferred object, load the YouTube
            // API.
            if (!_youtubeApiDeferred) {
                _youtubeApiDeferred = $.Deferred();
                setupOnYouTubeIframeAPIReady();
            } else if (!window.onYouTubeIframeAPIReady || !window.onYouTubeIframeAPIReady.done) {
                // The Deferred object could have been already defined in a previous
                // initialization of the video module. However, since then the global variable
                // window.onYouTubeIframeAPIReady could have been overwritten. If so,
                // we should set it up again.
                setupOnYouTubeIframeAPIReady();
            }

            // Attach a callback to our Deferred object to be called once the
            // YouTube API loads.
            window.onYouTubeIframeAPIReady.done(function() {
                window.YT.ready(onYTApiReady);
            });
        }
    } else {
        video = VideoPlayer(state);

        state.modules.push(video);
        state.__dfd__.resolve();
        state.htmlPlayerLoaded = true;
    }
}

function _waitForYoutubeApi(state) {
    console.log('[Video info]: Starting to wait for YouTube API to load.');
    window.setTimeout(function() {
        // If YouTube API will load OK, it will run `onYouTubeIframeAPIReady`
        // callback, which will set `state.youtubeApiAvailable` to `true`.
        // If something goes wrong at this stage, `state.youtubeApiAvailable` is
        // `false`.
        if (!state.youtubeApiAvailable) {
            console.log('[Video info]: YouTube API is not available.');
            if (!state.htmlPlayerLoaded) {
                state.loadHtmlPlayer();
            }
        }
        state.el.trigger('youtube_availability', [state.youtubeApiAvailable]);
    }, state.config.ytTestTimeout);
}

function loadYouTubeIFrameAPI(scriptTag) {
    let firstScriptTag = document.getElementsByTagName('script')[0];
    firstScriptTag.parentNode.insertBefore(scriptTag, firstScriptTag);
}

// function _parseYouTubeIDs(state)
//     The function parse YouTube stream ID's.
//     @return
//         false: We don't have YouTube video IDs to work with; most likely
//             we have HTML5 video sources.
//         true: Parsing of YouTube video IDs went OK, and we can proceed
//             onwards to play YouTube videos.
function _parseYouTubeIDs(state) {
    if (state.parseYoutubeStreams(state.config.streams)) {
        state.videoType = 'youtube';

        return true;
    }

    console.log(
        '[Video info]: Youtube Video IDs are incorrect or absent.'
    );

    return false;
}

/**
 * Extract HLS video URLs from available video URLs.
 *
 * @param {object} state The object contaning the state (properties, methods, modules) of the Video player.
 * @returns Array of available HLS video source urls.
 */
function extractHLSVideoSources(state) {
    return _.filter(state.config.sources, function(source) {
        return /\.m3u8(\?.*)?$/.test(source);
    });
}

// function _prepareHTML5Video(state)
// The function prepare HTML5 video, parse HTML5
// video sources etc.
function _prepareHTML5Video(state) {
    state.speeds = ['0.75', '1.0', '1.25', '1.50', '2.0'];
    // If none of the supported video formats can be played and there is no
    // short-hand video links, than hide the spinner and show error message.
    if (!state.config.sources.length) {
        _hideWaitPlaceholder(state);
        state.el
            .find('.video-player div')
            .addClass('hidden');
        state.el
            .find('.video-player .video-error')
            .removeClass('is-hidden');

        return false;
    }

    state.videoType = 'html5';

    if (!_.keys(state.config.transcriptLanguages).length) {
        state.config.showCaptions = false;
    }
    state.setSpeed(state.speed);

    return true;
}

function _hideWaitPlaceholder(state) {
    state.el
        .addClass('is-initialized')
        .find('.spinner')
        .attr({
            'aria-hidden': 'true',
            tabindex: -1
        });
}

function _setConfigurations(state) {
    state.setPlayerMode(state.config.mode);
    // Possible value are: 'visible', 'hiding', and 'invisible'.
    state.controlState = 'visible';
    state.controlHideTimeout = null;
    state.captionState = 'invisible';
    state.captionHideTimeout = null;
    state.HLSVideoSources = extractHLSVideoSources(state);
}

// eslint-disable-next-line no-shadow
function _initializeModules(state, i18n) {
    let dfd = $.Deferred(),
        modulesList = $.map(state.modules, function(module) {
            let options = state.options[module.moduleName] || {};
            if (_.isFunction(module)) {
                return module(state, i18n, options);
            } else if ($.isPlainObject(module)) {
                return module;
            }
        });

    $.when.apply(null, modulesList)
        .done(dfd.resolve);

    return dfd.promise();
}

function _getConfiguration(data, storage) {
    let isBoolean = function(value) {
            let regExp = /^true$/i;
            return regExp.test(value.toString());
        },
        // List of keys that will be extracted form the configuration.
        extractKeys = [],
        // Compatibility keys used to change names of some parameters in
        // the final configuration.
        compatKeys = {
            start: 'startTime',
            end: 'endTime'
        },
        // Conversions used to pre-process some configuration data.
        conversions = {
            showCaptions: isBoolean,
            autoplay: isBoolean,
            autohideHtml5: isBoolean,
            autoAdvance: function(value) {
                let shouldAutoAdvance = storage.getItem('auto_advance');
                if (_.isUndefined(shouldAutoAdvance)) {
                    return isBoolean(value) || false;
                } else {
                    return shouldAutoAdvance;
                }
            },
            savedVideoPosition: function(value) {
                return storage.getItem('savedVideoPosition', true)
                || Number(value)
                || 0;
            },
            speed: function(value) {
                return storage.getItem('speed', true) || value;
            },
            generalSpeed: function(value) {
                return storage.getItem('general_speed')
                || value
                || '1.0';
            },
            transcriptLanguage: function(value) {
                return storage.getItem('language')
                || value
                || 'en';
            },
            ytTestTimeout: function(value) {
                value = parseInt(value, 10);

                if (!isFinite(value)) {
                    value = 1500;
                }

                return value;
            },
            startTime: function(value) {
                value = parseInt(value, 10);
                if (!isFinite(value) || value < 0) {
                    return 0;
                }

                return value;
            },
            endTime: function(value) {
                value = parseInt(value, 10);

                if (!isFinite(value) || value === 0) {
                    return null;
                }

                return value;
            },
            audioDescriptionActive: function(value) {
                let stored = storage.getItem('audio_description_active');
                if (_.isUndefined(stored)) {
                    return value === true || value === 'true';
                }
                return stored;
            }
        },
        config = {};

    data = _.extend({
        startTime: 0,
        endTime: null,
        sub: '',
        streams: ''
    }, data);

    $.each(data, function(option, value) {
    // Extract option that is in `extractKeys`.
        if ($.inArray(option, extractKeys) !== -1) {
            return;
        }

        // Change option name to key that is in `compatKeys`.
        if (compatKeys[option]) {
            option = compatKeys[option];
        }

        // Pre-process data.
        if (conversions[option]) {
            if (_.isFunction(conversions[option])) {
                value = conversions[option].call(this, value);
            } else {
                throw new TypeError(option + ' is not a function.');
            }
        }
        config[option] = value;
    });

    return config;
}

// ***************************************************************
// Public functions start here.
// These are available via the 'state' object. Their context ('this'
// keyword) is the 'state' object. The magic private function that makes
// them available and sets up their context is makeFunctionsPublic().
// ***************************************************************

// function bindTo(methodsDict, obj, context, rewrite)
// Creates a new function with specific context and assigns it to the provided
// object.
// eslint-disable-next-line no-shadow
function bindTo(methodsDict, obj, context, rewrite) {
    $.each(methodsDict, function(name, method) {
        if (_.isFunction(method)) {
            if (_.isUndefined(rewrite)) {
                rewrite = true;
            }

            if (_.isUndefined(obj[name]) || rewrite) {
                obj[name] = _.bind(method, context);
            }
        }
    });
}

function loadYoutubePlayer() {
    if (this.htmlPlayerLoaded) { return; }

    console.log(
        '[Video info]: Fetch metadata for YouTube video.'
    );

    this.fetchMetadata();
    this.parseSpeed();
}

function loadHtmlPlayer() {
    // When the youtube link doesn't work for any reason
    // (for example, firewall) any
    // alternate sources should automatically play.
    if (!_prepareHTML5Video(this)) {
        console.log(
            '[Video info]: Continue loading '
    + 'YouTube video.'
        );

        // Non-YouTube sources were not found either.

        this.el.find('.video-player div')
            .removeClass('hidden');
        this.el.find('.video-player .video-error')
            .addClass('is-hidden');

        // If in reality the timeout was to short, try to
        // continue loading the YouTube video anyways.
        this.loadYoutubePlayer();
    } else {
        console.log(
            '[Video info]: Start HTML5 player.'
        );

        // In-browser HTML5 player does not support quality
        // control.
        this.el.find('.quality_control').hide();
        _renderElements(this);
    }
}

// function initialize(element)
// The function set initial configuration and preparation.

function initialize(element) {
    let self = this,
        el = this.el,
        id = this.id,
        container = el.find('.video-wrapper'),
        __dfd__ = $.Deferred(),
        isTouch = onTouchBasedDevice() || '';

    if (isTouch) {
        el.addClass('is-touch');
    }

    $.extend(this, {
        __dfd__: __dfd__,
        container: container,
        isFullScreen: false,
        isTouch: isTouch
    });

    console.log('[Video info]: Initializing video with id "%s".', id);

    // We store all settings passed to us by the server in one place. These
    // are "read only", so don't modify them. All variable content lives in
    // 'state' object.
    // jQuery .data() return object with keys in lower camelCase format.
    this.config = $.extend({}, _getConfiguration(this.metadata, this.storage), {
        element: element,
        fadeOutTimeout: 1400,
        captionsFreezeTime: 10000,
        mode: $.cookie('edX_video_player_mode'),
        // Available HD qualities will only be accessible once the video has
        // been played once, via player.getAvailableQualityLevels.
        availableHDQualities: []
    });

    if (this.config.endTime < this.config.startTime) {
        this.config.endTime = null;
    }

    this.lang = this.config.transcriptLanguage;
    this.speed = this.speedToString(
        this.config.speed || this.config.generalSpeed
    );
    this.auto_advance = this.config.autoAdvance;
    this.htmlPlayerLoaded = false;
    this.duration = this.metadata.duration;

    _setConfigurations(this);

    // If `prioritizeHls` is set to true than `hls` is the primary playback
    if (this.config.prioritizeHls || !(_parseYouTubeIDs(this))) {
        // If we do not have YouTube ID's, try parsing HTML5 video sources.
        if (!_prepareHTML5Video(this)) {
            __dfd__.reject();
            // Non-YouTube sources were not found either.
            return __dfd__.promise();
        }

        console.log('[Video info]: Start player in HTML5 mode.');
        _renderElements(this);
    } else {
        _renderElements(this);

        _waitForYoutubeApi(this);

        let scriptTag = document.createElement('script');

        scriptTag.src = this.config.ytApiUrl;
        scriptTag.async = true;

        $(scriptTag).on('load', function() {
            self.loadYoutubePlayer();
        });
        $(scriptTag).on('error', function() {
            console.log(
                '[Video info]: YouTube returned an error for '
        + 'video with id "' + self.id + '".'
            );
            // If the video is already loaded in `_waitForYoutubeApi` by the
            // time we get here, then we shouldn't load it again.
            if (!self.htmlPlayerLoaded) {
                self.loadHtmlPlayer();
            }
        });

        window.Video.loadYouTubeIFrameAPI(scriptTag);
    }
    return __dfd__.promise();
}

// function parseYoutubeStreams(state, youtubeStreams)
//
//     Take a string in the form:
//         "iCawTYPtehk:0.75,KgpclqP-LBA:1.0,9-2670d5nvU:1.5"
//     parse it, and make it available via the 'state' object. If we are
//     not given a string, or it's length is zero, then we return false.
//
//     @return
//         false: We don't have YouTube video IDs to work with; most likely
//             we have HTML5 video sources.
//         true: Parsing of YouTube video IDs went OK, and we can proceed
//             onwards to play YouTube videos.
function parseYoutubeStreams(youtubeStreams) {
    if (_.isUndefined(youtubeStreams) || !youtubeStreams.length) {
        return false;
    }

    this.videos = {};

    _.each(youtubeStreams.split(/,/), function(video) {
        let speed;
        video = video.split(/:/);
        speed = this.speedToString(video[0]);
        this.videos[speed] = video[1];
    }, this);

    return _.isString(this.videos['1.0']);
}

// function fetchMetadata()
//
//     When dealing with YouTube videos, we must fetch meta data that has
//     certain key facts not available while the video is loading. For
//     example the length of the video can be determined from the meta
//     data.
function fetchMetadata() {
    let self = this,
        metadataXHRs = [];

    this.metadata = {};

    metadataXHRs = _.map(this.videos, function(url, speed) {
        return self.getVideoMetadata(url, function(data) {
            if (data.items.length > 0) {
                let metaDataItem = data.items[0];
                self.metadata[metaDataItem.id] = metaDataItem.contentDetails;
            }
        });
    });

    $.when.apply(this, metadataXHRs).done(function() {
        self.el.trigger('metadata_received');

        // Not only do we trigger the "metadata_received" event, we also
        // set a flag to notify that metadata has been received. This
        // allows for code that will miss the "metadata_received" event
        // to know that metadata has been received. This is important in
        // cases when some code will subscribe to the "metadata_received"
        // event after it has been triggered.
        self.youtubeMetadataReceived = true;
    });
}

// function parseSpeed()
//
//     Create a separate array of available speeds.
function parseSpeed() {
    this.speeds = _.keys(this.videos).sort();
}

function setSpeed(newSpeed) {
    // Possible speeds for each player type.
    // HTML5 =          [0.75, 1, 1.25, 1.5, 2]
    // Youtube Flash =  [0.75, 1, 1.25, 1.5]
    // Youtube HTML5 =  [0.25, 0.5, 1, 1.5, 2]
    let map = {
        0.25: '0.75', // Youtube HTML5 -> HTML5 or Youtube Flash
        '0.50': '0.75', // Youtube HTML5 -> HTML5 or Youtube Flash
        0.75: '0.50', // HTML5 or Youtube Flash -> Youtube HTML5
        1.25: '1.50', // HTML5 or Youtube Flash -> Youtube HTML5
        2.0: '1.50' // HTML5 or Youtube HTML5 -> Youtube Flash
    };

    if (_.contains(this.speeds, newSpeed)) {
        this.speed = newSpeed;
    } else {
        newSpeed = map[newSpeed];
        this.speed = _.contains(this.speeds, newSpeed) ? newSpeed : '1.0';
    }
    this.speed = parseFloat(this.speed);
}

function setAutoAdvance(enabled) {
    this.auto_advance = enabled;
}

function getVideoMetadata(url, callback) {
    let youTubeEndpoint;
    if (!(_.isString(url))) {
        url = this.videos['1.0'] || '';
    }
    // Will hit the API URL to get the youtube video metadata.
    youTubeEndpoint = this.config.ytMetadataEndpoint; // The new runtime supports anonymous users
    // and uses an XBlock handler to get YouTube metadata
    if (!youTubeEndpoint) {
        // The old runtime has a full/separate LMS API for getting YouTube metadata, but it doesn't
        // support anonymous users nor videos that play in a sandboxed iframe.
        youTubeEndpoint = [this.config.lmsRootURL, '/courses/yt_video_metadata', '?id=', url].join('');
    }
    return $.ajax({
        url: youTubeEndpoint,
        success: _.isFunction(callback) ? callback : null,
        error: function() {
            console.warn(
                'Unable to get youtube video metadata. Some video metadata may be unavailable.'
            );
        },
        notifyOnError: false
    });
}

function youtubeId(speed) {
    let currentSpeed = this.isFlashMode() ? this.speed : '1.0';

    return this.videos[speed]
    || this.videos[currentSpeed]
    || this.videos['1.0'];
}

function getDuration() {
    try {
        let safeMoment = typeof moment !== 'undefined' ? moment : window.moment;
        return safeMoment.duration(this.metadata[this.youtubeId()].duration, safeMoment.ISO_8601).asSeconds();
    } catch (err) {
        return _.result(this.metadata[this.youtubeId('1.0')], 'duration') || 0;
    }
}

/**
 * Sets player mode.
 *
 * @param {string} mode Mode to set for the video player if it is supported.
 *                      Otherwise, `html5` is used by default.
 */
function setPlayerMode(mode) {
    let supportedModes = ['html5', 'flash'];

    mode = _.contains(supportedModes, mode) ? mode : 'html5';
    this.currentPlayerMode = mode;
}

/**
 * Returns current player mode.
 *
 * @return {string} Returns string that describes player mode
 */
function getPlayerMode() {
    return this.currentPlayerMode;
}

/**
 * Checks if current player mode is Flash.
 *
 * @return {boolean} Returns `true` if current mode is `flash`, otherwise
 *                   it returns `false`
 */
function isFlashMode() {
    return this.getPlayerMode() === 'flash';
}

/**
 * Checks if current player mode is Html5.
 *
 * @return {boolean} Returns `true` if current mode is `html5`, otherwise
 *                   it returns `false`
 */
function isHtml5Mode() {
    return this.getPlayerMode() === 'html5';
}

function isYoutubeType() {
    return this.videoType === 'youtube';
}

function speedToString(speed) {
    return parseFloat(speed).toFixed(2).replace(/\.00$/, '.0');
}

function getCurrentLanguage() {
    let keys = _.keys(this.config.transcriptLanguages);

    if (keys.length) {
        if (!_.contains(keys, this.lang)) {
            if (_.contains(keys, 'en')) {
                this.lang = 'en';
            } else {
                this.lang = keys.pop();
            }
        }
    } else {
        return null;
    }

    return this.lang;
}

/*
 * The trigger() function will assume that the @objChain is a complete
 * chain with a method (function) at the end. It will call this function.
 * So for example, when trigger() is called like so:
 *
 *     state.trigger('videoPlayer.pause', {'param1': 10});
 *
 * Then trigger() will execute:
 *
 *     state.videoPlayer.pause({'param1': 10});
 */
function trigger(objChain) {
    let extraParameters = Array.prototype.slice.call(arguments, 1),
        i, tmpObj, chain;

    // Remember that 'this' is the 'state' object.
    tmpObj = this;
    chain = objChain.split('.');

    // At the end of the loop the variable 'tmpObj' will either be the
    // correct object/function to trigger/invoke. If the 'chain' chain of
    // object is incorrect (one of the link is non-existent), then the loop
    // will immediately exit.
    while (chain.length) {
        i = chain.shift();

        if (tmpObj.hasOwnProperty(i)) {
            tmpObj = tmpObj[i];
        } else {
            // An incorrect object chain was specified.

            return false;
        }
    }

    tmpObj.apply(this, extraParameters);

    return true;
}
