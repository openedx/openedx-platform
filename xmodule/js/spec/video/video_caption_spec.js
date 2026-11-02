/* global _, WAIT_TIMEOUT */

(function() {
    'use strict';

    describe('VideoCaption', function() {
        var state, oldOTBD;
        var parseIntAttribute = function(element, attrName) {
            return parseInt(element.attr(attrName), 10);
        };

        beforeEach(function() {
            oldOTBD = window.onTouchBasedDevice;
            window.onTouchBasedDevice = jasmine.createSpy('onTouchBasedDevice')
                .and.returnValue(null);

            $.fn.scrollTo.calls.reset();
        });

        afterEach(function() {
            // `source` tags should be removed to avoid memory leak bug that we
            // had before. Removing of `source` tag, not `video` tag, stops
            // loading video source and clears the memory.
            $('source').remove();
            $.fn.scrollTo.calls.reset();
            state.storage.clear();
            state.videoPlayer.destroy();

            window.onTouchBasedDevice = oldOTBD;
        });

        describe('constructor', function() {
            describe('always', function() {
                beforeEach(function() {
                    spyOn($, 'ajaxWithPrefix').and.callThrough();
                });

                it('create the transcript element', function() {
                    state = jasmine.initializePlayer();
                    expect($('.video')).toContainElement('.subtitles');
                });

                it('has appropriate lang attributes', function() {
                    state = jasmine.initializePlayer();

                    $('.video .toggle-captions').trigger('click');

                    expect($('.video .subtitles-menu')).toHaveAttrs({
                        lang: 'en'
                    });
                    expect($('.video .closed-captions')).toHaveAttrs({
                        lang: 'en'
                    });
                });

                it('add transcript control to video player', function() {
                    state = jasmine.initializePlayer();
                    expect($('.video')).toContainElement('.toggle-transcript');
                });

                it('add ARIA attributes to transcript control', function() {
                    var $captionControl;
                    state = jasmine.initializePlayer();
                    $captionControl = $('.toggle-transcript');
                    expect($captionControl).toHaveAttrs({
                        'aria-disabled': 'false',
                        'aria-label': 'Turn off transcripts'
                    });
                    $captionControl.click();
                    expect($captionControl).toHaveAttrs({
                        'aria-disabled': 'false',
                        'aria-label': 'Turn on transcripts'
                    });
                });

                it('adds the captioning control to the video player', function() {
                    state = jasmine.initializePlayer();
                    expect($('.video')).toContainElement('.toggle-captions');
                    expect($('.video')).toContainElement('.closed-captions');
                });

                it('add ARIA attributes to caption control', function() {
                    var $toggleCaption;
                    state = jasmine.initializePlayer();
                    $toggleCaption = $('.toggle-captions');
                    expect($toggleCaption).toHaveAttrs({
                        'aria-disabled': 'false',
                        'aria-label': 'Turn on closed captioning'
                    });
                    $toggleCaption.click();
                    expect($toggleCaption).toHaveAttrs({
                        'aria-disabled': 'false',
                        'aria-label': 'Hide closed captions'
                    });
                });

                it('adds "closed" class to the main element if transcript setting is off', function() {
                    // No cookie, showCaptions setting is off: hide transcripts panel.
                    $.cookie.and.returnValue(null);
                    state = jasmine.initializePlayer('video_all.html', {showCaptions: false});
                    expect(state.el).toHaveClass('closed');

                    // No cookie, showCaptions setting is on: show transcripts panel.
                    $.cookie.and.returnValue(null);
                    state = jasmine.initializePlayer('video_all.html', {showCaptions: true});
                    expect(state.el).not.toHaveClass('closed');

                    // Cookie preference is on, showCaptions setting is off: hide transcripts panel.
                    $.cookie.and.returnValue('true');
                    state = jasmine.initializePlayer('video_all.html', {showCaptions: false});
                    expect(state.el).toHaveClass('closed');

                    // Cookie preference is on, showCaptions setting is on: show transcripts panel.
                    $.cookie.and.returnValue('true');
                    state = jasmine.initializePlayer('video_all.html', {showCaptions: true});
                    expect(state.el).not.toHaveClass('closed');

                    // Cookie preference is off, showCaptions setting is off: hide transcripts panel.
                    $.cookie.and.returnValue('false');
                    state = jasmine.initializePlayer('video_all.html', {showCaptions: false});
                    expect(state.el).toHaveClass('closed');

                    // Cookie preference is off, showCaptions setting is on: hide transcripts panel.
                    $.cookie.and.returnValue('false');
                    state = jasmine.initializePlayer('video_all.html', {showCaptions: true});
                    expect(state.el).toHaveClass('closed');
                });

                it('fetch the transcript in HTML5 mode', function(done) {
                    var transcriptURL = '/transcript/translation/en',
                        transcriptCall;
                    state = jasmine.initializePlayer();

                    jasmine.waitUntil(function() {
                        return state.videoCaption.loaded;
                    }).then(function() {
                        expect($.ajaxWithPrefix).toHaveBeenCalledWith({
                            url: transcriptURL,
                            notifyOnError: false,
                            // eslint-disable-next-line no-void
                            data: void 0,
                            success: jasmine.any(Function),
                            error: jasmine.any(Function)
                        });
                        transcriptCall = $.ajaxWithPrefix.calls.all().find(function(call) {
                            return call.args[0].url === transcriptURL;
                        });
                        expect(transcriptCall.args[0].data).toBeUndefined();
                    }).always(done);
                });

                it('fetch the transcript in Flash mode', function(done) {
                    state = jasmine.initializePlayerYouTube();
                    spyOn(state, 'isFlashMode').and.returnValue(true);
                    state.videoCaption.fetchCaption();

                    jasmine.waitUntil(function() {
                        return state.videoCaption.loaded;
                    }).then(function() {
                        expect($.ajaxWithPrefix).toHaveBeenCalledWith({
                            url: '/transcript/translation/en',
                            notifyOnError: false,
                            data: jasmine.any(Object),
                            success: jasmine.any(Function),
                            error: jasmine.any(Function)
                        });
                        expect($.ajaxWithPrefix.calls.mostRecent().args[0].data)
                            .toEqual({
                                videoId: 'cogebirgzzM'
                            });
                    }).always(done);
                });

                it('fetch the transcript in Youtube mode', function(done) {
                    state = jasmine.initializePlayerYouTube();

                    jasmine.waitUntil(function() {
                        return state.videoCaption.loaded;
                    }).then(function() {
                        expect($.ajaxWithPrefix).toHaveBeenCalledWith({
                            url: '/transcript/translation/en',
                            notifyOnError: false,
                            data: jasmine.any(Object),
                            success: jasmine.any(Function),
                            error: jasmine.any(Function)
                        });
                        expect($.ajaxWithPrefix.calls.mostRecent().args[0].data)
                            .toEqual({
                                videoId: 'cogebirgzzM'
                            });
                    }).always(done);
                });

                it('bind the mouse movement', function() {
                    state = jasmine.initializePlayer();
                    expect($('.subtitles-menu')).toHandle('mouseover');
                    expect($('.subtitles-menu')).toHandle('mouseout');
                    expect($('.subtitles-menu')).toHandle('mousemove');
                    expect($('.subtitles-menu')).toHandle('mousewheel');
                    expect($('.subtitles-menu')).toHandle('DOMMouseScroll');
                });

                it('bind the scroll', function() {
                    state = jasmine.initializePlayer();
                    expect($('.subtitles-menu'))
                        .toHandleWith('scroll', state.videoControl.showControls);
                });
            });

            it('can destroy itself', function() {
                var plugin;
                spyOn($, 'ajaxWithPrefix');
                state = jasmine.initializePlayer();
                plugin = state.videoCaption;

                spyOn($.fn, 'off').and.callThrough();
                state.videoCaption.destroy();

                expect(state.videoCaption).toBeUndefined();
                expect($.fn.off).toHaveBeenCalledWith({
                    'caption:fetch': plugin.fetchCaption,
                    'caption:resize': plugin.onResize,
                    'caption:update': plugin.onCaptionUpdate,
                    ended: plugin.pause,
                    fullscreen: plugin.onResize,
                    pause: plugin.pause,
                    play: plugin.play,
                    destroy: plugin.destroy
                });
            });

            describe('renderCaptions', function() {
                describe('is rendered', function() {
                    var KEY = $.ui.keyCode,

                        keyPressEvent = function(key) {
                            return $.Event('keydown', {keyCode: key});
                        };

                    it('toggles the captions on control click', function() {
                        state = jasmine.initializePlayer();

                        $('.toggle-captions').click();
                        expect($('.toggle-captions')).toHaveClass('is-active');
                        expect($('.closed-captions')).toHaveClass('is-visible');

                        $('.toggle-captions').click();
                        expect($('.toggle-captions')).not.toHaveClass('is-active');
                        expect($('.closed-captions')).not.toHaveClass('is-visible');
                    });

                    it('toggles the captions on keypress ENTER', function() {
                        state = jasmine.initializePlayer();

                        $('.toggle-captions').focus().trigger(keyPressEvent(KEY.ENTER));
                        expect($('.toggle-captions')).toHaveClass('is-active');
                        expect($('.closed-captions')).toHaveClass('is-visible');

                        $('.toggle-captions').focus().trigger(keyPressEvent(KEY.ENTER));
                        expect($('.toggle-captions')).not.toHaveClass('is-active');
                        expect($('.closed-captions')).not.toHaveClass('is-visible');
                    });

                    it('toggles the captions on keypress SPACE', function() {
                        state = jasmine.initializePlayer();

                        $('.toggle-captions').focus().trigger(keyPressEvent(KEY.SPACE));
                        expect($('.toggle-captions')).toHaveClass('is-active');
                        expect($('.closed-captions')).toHaveClass('is-visible');

                        $('.toggle-captions').focus().trigger(keyPressEvent(KEY.SPACE));
                        expect($('.toggle-captions')).not.toHaveClass('is-active');
                        expect($('.closed-captions')).not.toHaveClass('is-visible');
                    });
                });
            });

            describe('renderLanguageMenu', function() {
                describe('is rendered', function() {
                    var KEY = $.ui.keyCode,

                        keyPressEvent = function(key) {
                            return $.Event('keydown', {keyCode: key});
                        };

                    it('if languages more than 1', function() {
                        var transcripts, langCodes, langLabels;
                        state = jasmine.initializePlayer();
                        transcripts = state.config.transcriptLanguages;
                        langCodes = _.keys(transcripts);
                        langLabels = _.values(transcripts);

                        expect($('.langs-list')).toExist();
                        expect($('.langs-list')).toHandle('click');

                        $('.langs-list li').each(function() {
                            var code = $(this).data('lang-code'),
                                link = $(this).find('.control'),
                                label = link.text();

                            expect(code).toBeInArray(langCodes);
                            expect(label).toBeInArray(langLabels);
                        });
                    });

                    it('when clicking on link with new language', function() {
                        var Caption, $link;
                        state = jasmine.initializePlayer();
                        Caption = state.videoCaption;
                        $link = $('.langs-list li[data-lang-code="de"] .control-lang');

                        spyOn(Caption, 'fetchCaption');
                        spyOn(state.storage, 'setItem');

                        state.lang = 'en';
                        $link.trigger('click');

                        expect(Caption.fetchCaption).toHaveBeenCalled();
                        expect(state.lang).toBe('de');
                        expect(state.storage.setItem)
                            .toHaveBeenCalledWith('language', 'de');
                        expect($('.langs-list li.is-active').length).toBe(1);
                        expect($('.subtitles .subtitles-menu')).toHaveAttrs({
                            lang: 'de'
                        });
                        expect($('.closed-captions')).toHaveAttrs({
                            lang: 'de'
                        });
                        expect($link).toHaveAttr('aria-pressed', 'true');
                    });

                    it('when clicking on link with current language', function() {
                        var Caption, $link;

                        state = jasmine.initializePlayer();
                        Caption = state.videoCaption;
                        $link = $('.langs-list li[data-lang-code="en"] .control-lang');

                        spyOn(Caption, 'fetchCaption');
                        spyOn(state.storage, 'setItem');

                        state.lang = 'en';
                        $link.trigger('click');

                        expect(Caption.fetchCaption).not.toHaveBeenCalled();
                        expect(state.lang).toBe('en');
                        expect(state.storage.setItem)
                            .not.toHaveBeenCalledWith('language', 'en');
                        expect($('.langs-list li.is-active').length).toBe(1);
                        expect($link).toHaveAttr('aria-pressed', 'true');
                    });

                    it('open the language toggle on hover', function() {
                        state = jasmine.initializePlayer();
                        $('.lang').mouseenter();
                        expect($('.lang')).toHaveClass('is-opened');
                        $('.lang').mouseleave();
                        expect($('.lang')).not.toHaveClass('is-opened');
                    });

                    it('opens the language menu on arrow up', function() {
                        state = jasmine.initializePlayer();
                        $('.language-menu').focus();
                        $('.language-menu').trigger(keyPressEvent(KEY.UP));
                        expect($('.lang')).toHaveClass('is-opened');
                        expect($('.langs-list')
                            .find('li')
                            .last()
                            .find('.control-lang'))
                            .toBeFocused();
                    });

                    it('closes the language menu on ESC', function() {
                        state = jasmine.initializePlayer();
                        $('.language-menu').trigger(keyPressEvent(KEY.UP));
                        expect($('.lang')).toHaveClass('is-opened');
                        $('.language-menu').trigger(keyPressEvent(KEY.ESCAPE));
                        expect($('.lang')).not.toHaveClass('is-opened');
                        expect($('.language-menu')).toBeFocused();
                    });
                });

                describe('is not rendered', function() {
                    it('if just 1 language', function() {
                        state = jasmine.initializePlayer(null, {
                            transcriptLanguages: {en: 'English'}
                        });

                        expect($('.langs-list')).not.toExist();
                        expect($('.lang')).not.toHandle('mouseenter');
                        expect($('.lang')).not.toHandle('mouseleave');
                    });
                });
            });

            describe('when on a non touch-based device', function() {
                beforeEach(function(done) {
                    state = jasmine.initializePlayer();

                    jasmine.waitUntil(function() {
                        return state.videoCaption.rendered;
                    }).then(done);
                });

                it('render the transcript', function() {
                    var captionsData = jasmine.stubbedCaption,
                        $items = $('.subtitles li span[data-index]');

                    _.each(captionsData.text, function(text, index) {
                        var item = $items.eq(index);

                        expect(parseIntAttribute(item, 'data-index')).toEqual(index);
                        expect(parseIntAttribute(item, 'data-start')).toEqual(captionsData.start[index]);
                        expect(item.attr('tabindex')).toEqual('0');
                        expect(item.text().trim()).toEqual(captionsData.text[index]);
                    });
                });

                it('add a padding element to transcript', function() {
                    expect($('.subtitles li:first').hasClass('spacing'))
                        .toBe(true);
                    expect($('.subtitles li:last').hasClass('spacing'))
                        .toBe(true);
                });

                it('bind all the transcript link', function() {
                    var handlerList = ['captionMouseOverOut', 'captionClick',
                        'captionMouseDown', 'captionFocus', 'captionBlur',
                        'captionKeyDown'
                    ];

                    $.each(handlerList, function(index, handler) {
                        spyOn(state.videoCaption, handler);
                    });
                    $('.subtitles li span[data-index]').each(
                        function(index, link) {
                            $(link).trigger('mouseover');
                            expect(state.videoCaption.captionMouseOverOut).toHaveBeenCalled();

                            state.videoCaption.captionMouseOverOut.calls.reset();
                            $(link).trigger('mouseout');
                            expect(state.videoCaption.captionMouseOverOut).toHaveBeenCalled();

                            $(this).click();
                            expect(state.videoCaption.captionClick).toHaveBeenCalled();

                            $(this).trigger('mousedown');
                            expect(state.videoCaption.captionMouseDown).toHaveBeenCalled();

                            $(this).trigger('focusin');
                            expect(state.videoCaption.captionFocus).toHaveBeenCalled();

                            $(this).trigger('focusout');
                            expect(state.videoCaption.captionBlur).toHaveBeenCalled();

                            $(this).trigger('keydown');
                            expect(state.videoCaption.captionKeyDown).toHaveBeenCalled();
                        });
                });

                it('set rendered to true', function(done) {
                    state = jasmine.initializePlayer();

                    jasmine.waitUntil(function() {
                        return state.videoCaption.rendered;
                    }).then(function() {
                        expect(state.videoCaption.rendered).toBeTruthy();
                    }).always(done);
                });
            });

            describe('when on a touch-based device', function() {
                beforeEach(function() {
                    window.onTouchBasedDevice.and.returnValue(['iPad']);

                    state = jasmine.initializePlayer();
                    $.fn.scrollTo.calls.reset();
                });

                it('show explanation message', function() {
                    expect($('.subtitles .subtitles-menu li')).toHaveText(
                        'Transcript will be displayed when you start playing the video.'
                    );
                });

                it('show transcript on play', function(done) {
                    state.el.trigger('play');

                    jasmine.waitUntil(function() {
                        return state.videoCaption.rendered;
                    }).then(function() {
                        var captionsData = jasmine.stubbedCaption,
                            $items = $('.subtitles li span[data-index]');

                        _.each(captionsData.text, function(text, index) {
                            var item = $items.eq(index);

                            expect(parseIntAttribute(item, 'data-index')).toEqual(index);
                            expect(parseIntAttribute(item, 'data-start')).toEqual(captionsData.start[index]);
                            expect(item.attr('tabindex')).toEqual('0');
                            expect(item.text().trim()).toEqual(text);
                        });
                    }).always(done);
                });

                it('does not set rendered to true', function() {
                    expect(state.videoCaption.rendered).toBeFalsy();
                });
            });

            describe('when no transcripts file was specified', function() {
                beforeEach(function() {
                    state = jasmine.initializePlayer('video_all.html', {
                        sub: '',
                        transcriptLanguages: {}
                    });
                });

                it('transcript panel is not shown', function() {
                    expect(state.videoCaption.languageChooserEl).toBeHidden();
                });
            });
        });

        describe('mouse movement', function() {
            var originalClearTimeout;

            beforeEach(function(done) {
                jasmine.clock().install();
                state = jasmine.initializePlayer();
                jasmine.clock().tick(50);
                jasmine.waitUntil(function() {
                    return state.videoCaption.rendered;
                }).then(done);

                // Why we can't use spyOn(): https://github.com/jasmine/jasmine/issues/826
                originalClearTimeout = window.clearTimeout;
                window.clearTimeout = jasmine.createSpy().and.callFake(originalClearTimeout);
            });

            afterEach(function() {
                window.clearTimeout = originalClearTimeout;
                jasmine.clock().uninstall();
            });

            describe('when cursor is outside of the transcript box', function() {
                it('does not set freezing timeout', function() {
                    expect(state.videoCaption.frozen).toBeFalsy();
                });
            });

            describe('when cursor is in the transcript box', function() {
                beforeEach(function() {
                    spyOn(state.videoCaption, 'onMouseLeave');
                    $(window).trigger(jQuery.Event('mousemove'));
                    jasmine.clock().tick(state.config.captionsFreezeTime);
                    $('.subtitles-menu').trigger(jQuery.Event('mouseenter'));
                    jasmine.clock().tick(state.config.captionsFreezeTime);
                });

                it('set the freezing timeout', function() {
                    expect(state.videoCaption.frozen).not.toBeFalsy();
                    expect(state.videoCaption.onMouseLeave).toHaveBeenCalled();
                });

                describe('when the cursor is moving', function() {
                    it('reset the freezing timeout', function() {
                        $('.subtitles-menu').trigger(jQuery.Event('mousemove'));
                        expect(window.clearTimeout).toHaveBeenCalled();
                    });
                });

                describe('when the mouse is scrolling', function() {
                    it('reset the freezing timeout', function() {
                        $('.subtitles-menu').trigger(jQuery.Event('mousewheel'));
                        expect(window.clearTimeout).toHaveBeenCalled();
                    });
                });
            });

            describe(
                'when cursor is moving out of the transcript box',
                function() {
                    beforeEach(function() {
                        state.videoCaption.frozen = 100;
                        $.fn.scrollTo.calls.reset();
                    });

                    describe('always', function() {
                        beforeEach(function() {
                            $('.subtitles-menu').trigger(jQuery.Event('mouseout'));
                        });

                        it('reset the freezing timeout', function() {
                            expect(window.clearTimeout).toHaveBeenCalledWith(100);
                        });

                        it('unfreeze the transcript', function() {
                            expect(state.videoCaption.frozen).toBeNull();
                        });
                    });

                    describe('when the player is playing', function() {
                        beforeEach(function() {
                            state.videoCaption.playing = true;
                            $('.subtitles-menu span[data-index]:first')
                                .parent()
                                .addClass('current');
                            $('.subtitles-menu').trigger(jQuery.Event('mouseout'));
                        });

                        it('scroll the transcript', function() {
                            expect($.fn.scrollTo).toHaveBeenCalled();
                        });
                    });

                    describe('when the player is not playing', function() {
                        beforeEach(function() {
                            state.videoCaption.playing = false;
                            $('.subtitles-menu').trigger(jQuery.Event('mouseout'));
                        });

                        it('does not scroll the transcript', function() {
                            expect($.fn.scrollTo).not.toHaveBeenCalled();
                        });
                    });
                });
        });

        describe('fetchCaption', function() {
            var Caption, msg;

            beforeEach(function() {
                state = jasmine.initializePlayer();
                Caption = state.videoCaption;
                spyOn($, 'ajaxWithPrefix').and.callThrough();
                spyOn(Caption, 'renderCaption');
                spyOn(Caption, 'bindHandlers');
                spyOn(Caption, 'updatePlayTime');
                spyOn(Caption, 'updateGoogleDisclaimer');
                spyOn(Caption, 'hideCaptions');
                spyOn(state, 'youtubeId').and.returnValue('Z5KLxerq05Y');
            });

            it('show transcript on language change', function() {
                Caption.loaded = true;
                Caption.fetchCaption();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(Caption.hideCaptions).toHaveBeenCalledWith(false);
            });

            msg = 'use cookie to show/hide transcripts if they have not been '
                    + 'loaded yet';
            it(msg, function() {
                Caption.loaded = false;
                Caption.hideCaptionsOnLoad = false;
                Caption.fetchCaption();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(Caption.hideCaptions).toHaveBeenCalledWith(false);

                Caption.loaded = false;
                Caption.hideCaptions.calls.reset();
                Caption.hideCaptionsOnLoad = true;
                Caption.fetchCaption();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(Caption.hideCaptions).toHaveBeenCalledWith(true);
            });

            it('on success: on touch devices', function() {
                state.isTouch = true;
                Caption.loaded = false;
                Caption.fetchCaption();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(Caption.bindHandlers).toHaveBeenCalled();
                expect(Caption.renderCaption).not.toHaveBeenCalled();
                expect(Caption.updatePlayTime).not.toHaveBeenCalled();
                expect(Caption.loaded).toBeTruthy();
            });

            msg = 'on success: change language on touch devices when '
                 + 'transcripts have not been rendered yet';
            it(msg, function() {
                state.isTouch = true;
                Caption.loaded = true;
                Caption.rendered = false;
                Caption.fetchCaption();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(Caption.bindHandlers).not.toHaveBeenCalled();
                expect(Caption.renderCaption).not.toHaveBeenCalled();
                expect(Caption.updatePlayTime).not.toHaveBeenCalled();
                expect(Caption.loaded).toBeTruthy();
            });

            it('on success: re-render on touch devices', function() {
                state.isTouch = true;
                Caption.loaded = true;
                Caption.rendered = true;
                Caption.fetchCaption();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(Caption.bindHandlers).not.toHaveBeenCalled();
                expect(Caption.renderCaption).toHaveBeenCalled();
                expect(Caption.updatePlayTime).toHaveBeenCalled();
                expect(Caption.loaded).toBeTruthy();
            });

            it('on success: rendered correct', function() {
                Caption.loaded = false;
                Caption.fetchCaption();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(Caption.bindHandlers).toHaveBeenCalled();
                expect(Caption.renderCaption).toHaveBeenCalled();
                expect(Caption.updateGoogleDisclaimer).toHaveBeenCalled()
                expect(Caption.updatePlayTime).not.toHaveBeenCalled();
                expect(Caption.loaded).toBeTruthy();
            });

            it('on success: re-rendered correct', function() {
                Caption.loaded = true;
                Caption.rendered = true;
                Caption.fetchCaption();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(Caption.bindHandlers).not.toHaveBeenCalled();
                expect(Caption.renderCaption).toHaveBeenCalled();
                expect(Caption.updatePlayTime).toHaveBeenCalled();
                expect(Caption.loaded).toBeTruthy();
            });

            msg = 'on error: transcripts are hidden if there are no transcripts';
            it(msg, function() {
                spyOn(Caption, 'fetchAvailableTranslations');
                $.ajax.and.callFake(function(settings) {
                    _.result(settings, 'error');
                });

                state.config.transcriptLanguages = {};

                Caption.fetchCaption();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(Caption.fetchAvailableTranslations).not.toHaveBeenCalled();
                expect(Caption.hideCaptions.calls.mostRecent().args[0]).toEqual(true);
            });

            msg = 'on error: for Html5 player an attempt to fetch transcript '
                    + 'with youtubeId if there are no additional transcripts';
            it(msg, function() {
                spyOn(Caption, 'fetchAvailableTranslations');
                spyOn(Caption, 'fetchCaption').and.callThrough();
                spyOn(Caption, 'hideClosedCaptions').and.callThrough();
                $.ajax.and.callFake(function(settings) {
                    _.result(settings, 'error');
                });

                state.config.transcriptLanguages = {};
                state.videoType = 'html5';

                Caption.fetchCaption();

                expect(Caption.fetchAvailableTranslations).not.toHaveBeenCalled();
                expect(Caption.hideCaptions.calls.mostRecent().args[0]).toEqual(true);
                expect(Caption.fetchCaption.calls.mostRecent().args[0]).toEqual(true);
                expect(Caption.fetchCaption.calls.count()).toEqual(2);
                expect(Caption.hideClosedCaptions.calls.count()).toEqual(1);
            });

            msg = 'on success: when fetchCaption called with fetch_with_youtubeId to '
                    + 'get transcript with youtubeId for html5';
            it(msg, function() {
                spyOn(Caption, 'fetchAvailableTranslations');
                spyOn(Caption, 'fetchCaption').and.callThrough();

                Caption.loaded = true;
                state.config.transcriptLanguages = {};
                state.videoType = 'html5';

                Caption.fetchCaption(true);

                expect(Caption.fetchAvailableTranslations).not.toHaveBeenCalled();
                expect($.ajaxWithPrefix.calls.mostRecent().args[0].data)
                    .toEqual({videoId: 'Z5KLxerq05Y'});
                expect(Caption.hideCaptions).toHaveBeenCalledWith(false);
                expect(Caption.fetchCaption.calls.mostRecent().args[0]).toEqual(true);
                expect(Caption.fetchCaption.calls.count()).toEqual(1);
            });

            msg = 'on error: fetch available translations if there are '
                    + 'additional transcripts';
            it(msg, function() {
                $.ajax
                    .and.callFake(function(settings) {
                        _.result(settings, 'error');
                    });

                state.config.transcriptLanguages = {
                    en: 'English',
                    uk: 'Ukrainian'
                };

                spyOn(Caption, 'fetchAvailableTranslations');
                Caption.fetchCaption();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(Caption.fetchAvailableTranslations).toHaveBeenCalled();
            });


        });

        describe('fetchAvailableTranslations', function() {
            var Caption, msg;

            beforeEach(function() {
                state = jasmine.initializePlayer();
                Caption = state.videoCaption;
                spyOn($, 'ajaxWithPrefix').and.callThrough();
                spyOn(Caption, 'hideCaptions');
                spyOn(Caption, 'fetchCaption');
                spyOn(Caption, 'renderLanguageMenu');
            });

            it('request created with correct parameters', function() {
                Caption.fetchAvailableTranslations();

                expect($.ajaxWithPrefix).toHaveBeenCalledWith({
                    url: '/transcript/available_translations',
                    notifyOnError: false,
                    success: jasmine.any(Function),
                    error: jasmine.any(Function)
                });
            });

            msg = 'on success: language menu is rendered if translations available';
            it(msg, function() {
                state.config.transcriptLanguages = {
                    en: 'English',
                    uk: 'Ukrainian',
                    de: 'German'
                };
                Caption.fetchAvailableTranslations();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(state.config.transcriptLanguages).toEqual({
                    uk: 'Ukrainian',
                    de: 'German'
                });
                expect(Caption.renderLanguageMenu).toHaveBeenCalledWith({
                    uk: 'Ukrainian',
                    de: 'German'
                });
            });

            msg = 'on success: language menu isn\'t rendered if translations unavailable';
            it(msg, function() {
                state.config.transcriptLanguages = {
                    en: 'English',
                    ru: 'Russian'
                };
                Caption.fetchAvailableTranslations();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(state.config.transcriptLanguages).toEqual({});
                expect(Caption.renderLanguageMenu).not.toHaveBeenCalled();
            });

            msg = 'on error: transcripts are hidden if there are no transcript';
            it(msg, function() {
                $.ajax.and.callFake(function(settings) {
                    _.result(settings, 'error');
                });
                Caption.fetchAvailableTranslations();

                expect($.ajaxWithPrefix).toHaveBeenCalled();
                expect(Caption.hideCaptions).toHaveBeenCalledWith(true);
                expect(Caption.languageChooserEl).toBeHidden();
            });
        });

        describe('play', function() {
            describe('when the transcript was not rendered', function() {
                beforeEach(function(done) {
                    window.onTouchBasedDevice.and.returnValue(['iPad']);

                    state = jasmine.initializePlayer();
                    state.videoCaption.play();

                    jasmine.waitUntil(function() {
                        return state.videoCaption.rendered;
                    }).then(function() {
                        done();
                    });
                });

                it('render the transcript', function() {
                    var captionsData;

                    captionsData = jasmine.stubbedCaption;

                    $('.subtitles li span[data-index]').each(
                        function(index, item) {
                            expect(parseIntAttribute($(item), 'data-index')).toEqual(index);
                            expect(parseIntAttribute($(item), 'data-start')).toEqual(captionsData.start[index]);
                            expect($(item).attr('tabindex')).toEqual('0');
                            expect($(item).text().trim()).toEqual(captionsData.text[index]);
                        });
                });

                it('add a padding element to transcript', function() {
                    expect($('.subtitles li:first')).toHaveClass('spacing');
                    expect($('.subtitles li:last')).toHaveClass('spacing');
                });

                it('set rendered to true', function() {
                    expect(state.videoCaption.rendered).toBeTruthy();
                });

                it('set playing to true', function() {
                    expect(state.videoCaption.playing).toBeTruthy();
                });
            });
        });

        describe('pause', function() {
            beforeEach(function() {
                state = jasmine.initializePlayer();
                state.videoCaption.playing = true;
                state.videoCaption.pause();
            });

            it('set playing to false', function() {
                expect(state.videoCaption.playing).toBeFalsy();
            });
        });

        describe('updatePlayTime', function() {
            beforeEach(function(done) {
                state = jasmine.initializePlayer();

                jasmine.waitUntil(function() {
                    return state.videoCaption.rendered;
                }).then(done);
            });

            describe('when the video speed is 1.0x', function() {
                it('search the caption based on time', function() {
                    state.videoCaption.updatePlayTime(25.000);
                    expect(state.videoCaption.currentIndex).toEqual(5);

                    // Flash mode
                    spyOn(state, 'isFlashMode').and.returnValue(true);
                    state.speed = '1.0';
                    state.videoCaption.updatePlayTime(25.000);
                    expect(state.videoCaption.currentIndex).toEqual(5);
                });
            });

            describe('when the video speed is not 1.0x', function() {
                it('search the transcript based on 1.0x speed', function() {
                    state.videoCaption.updatePlayTime(25.000);
                    expect(state.videoCaption.currentIndex).toEqual(5);

                    // To test speed, don't use start / end times.
                    state.config.startTime = 0;
                    state.config.endTime = null;

                    // Flash mode
                    state.speed = '2.0';
                    spyOn(state, 'isFlashMode').and.returnValue(true);
                    state.videoCaption.updatePlayTime(25.000);
                    expect(state.videoCaption.currentIndex).toEqual(9);
                    state.speed = '0.75';
                    state.videoCaption.updatePlayTime(25.000);
                    expect(state.videoCaption.currentIndex).toEqual(3);
                });
            });

            describe('when the index is not the same', function() {
                beforeEach(function() {
                    state.videoCaption.currentIndex = 1;
                    $('.subtitles li span[data-index=5]').addClass('current');
                    state.videoCaption.updatePlayTime(25.000);
                });

                it('deactivate the previous transcript', function() {
                    expect($('.subtitles li span[data-index=1]'))
                        .not.toHaveClass('current');
                });

                it('activate new transcript', function() {
                    expect($('.subtitles li span[data-index=5]'))
                        .toHaveClass('current');
                });

                it('save new index', function() {
                    expect(state.videoCaption.currentIndex).toEqual(5);
                });

                it('scroll transcript to new position', function() {
                    expect($.fn.scrollTo).toHaveBeenCalled();
                });
            });

            describe('when the index is the same', function() {
                it('does not change current subtitle', function() {
                    state.videoCaption.currentIndex = 1;
                    $('.subtitles li span[data-index=3]').addClass('current');
                    state.videoCaption.updatePlayTime(15.000);
                    expect($('.subtitles li span[data-index=3]'))
                        .toHaveClass('current');
                });
            });
        });

        describe('resize', function() {
            beforeEach(function(done) {
                state = jasmine.initializePlayer();

                jasmine.waitUntil(function() {
                    return state.videoCaption.rendered;
                }).then(function() {
                    $('.subtitles li span[data-index=1]').addClass('current');
                    state.videoCaption.onResize();
                }).always(done);
            });

            describe('set the height of transcript container', function() {
                it('when transcript button is enabled', function() {
                    var realHeight = parseInt(
                            $('.subtitles').css('maxHeight'), 10
                        ),
                        shouldBeHeight = $('.video-wrapper').height();

                    // Because of some problems with rounding on different
                    // environments: Linux * Mac * FF * Chrome
                    expect(realHeight).toBeCloseTo(shouldBeHeight, 2);
                });

                it('when transcript button is disabled ', function() {
                    var realHeight, videoWrapperHeight, progressSliderHeight,
                        controlHeight, shouldBeHeight;

                    state.captionsHidden = true;
                    state.videoCaption.setSubtitlesHeight();

                    realHeight = parseInt(
                        $('.subtitles').css('maxHeight'), 10
                    );
                    videoWrapperHeight = $('.video-wrapper').height();
                    progressSliderHeight = state.el.find('.slider').height();
                    controlHeight = state.el.find('.video-controls').height();
                    shouldBeHeight = parseInt((
                        videoWrapperHeight
                        - 0.5 * progressSliderHeight
                        - controlHeight
                    ), 10);

                    expect(realHeight).toBe(shouldBeHeight);
                });
            });

            it('set the height of transcript spacing', function() {
                var firstSpacing, lastSpacing;

                firstSpacing = Math.abs(parseInt(
                    $('.subtitles .spacing:first').css('height'), 10
                ));
                lastSpacing = Math.abs(parseInt(
                    $('.subtitles .spacing:last').css('height'), 10
                ));

                expect(firstSpacing - state.videoCaption.topSpacingHeight())
                    .toBeLessThan(1);
                expect(lastSpacing - state.videoCaption.bottomSpacingHeight())
                    .toBeLessThan(1);
            });

            it('scroll transcript to new position', function() {
                expect($.fn.scrollTo).toHaveBeenCalled();
            });
        });

        xdescribe('scrollCaption', function() {
            beforeEach(function() {
                runs(function() {
                    state = jasmine.initializePlayer();
                });

                waitsFor(function() {
                    return state.videoCaption.rendered;
                }, 'Transcripts are not rendered', WAIT_TIMEOUT);
            });

            describe('when frozen', function() {
                it('does not scroll the transcript', function() {
                    runs(function() {
                        state.videoCaption.frozen = true;
                        $('.subtitles li span[data-index=1]').addClass('current');
                        state.videoCaption.scrollCaption();
                        expect($.fn.scrollTo).not.toHaveBeenCalled();
                    });
                });
            });

            describe('when not frozen', function() {
                beforeEach(function() {
                    runs(function() {
                        state.videoCaption.frozen = false;
                    });
                });

                describe('when there is no current transcript', function() {
                    it('does not scroll the transcript', function() {
                        runs(function() {
                            state.videoCaption.scrollCaption();
                            expect($.fn.scrollTo).not.toHaveBeenCalled();
                        });
                    });
                });

                describe('when there is a current transcript', function() {
                    it('scroll to current transcript', function() {
                        runs(function() {
                            $('.subtitles li span[data-index=1]').addClass('current');
                            state.videoCaption.scrollCaption();
                            expect($.fn.scrollTo).toHaveBeenCalled();
                        });
                    });
                });
            });
        });

        xdescribe('seekPlayer', function() {
            beforeEach(function() {
                runs(function() {
                    state = jasmine.initializePlayer();
                });

                waitsFor(function() {
                    var duration = state.videoPlayer.duration(),
                        isRendered = state.videoCaption.rendered;

                    return isRendered && duration;
                }, 'Transcripts are not rendered', WAIT_TIMEOUT);
            });

            describe('when the video speed is 1.0x', function() {
                it('trigger seek event with the correct time', function() {
                    runs(function() {
                        state.videoSpeedControl.currentSpeed = '1.0';
                        $('.subtitles li span[data-start="14910"]').trigger('click');
                        expect(state.videoPlayer.currentTime).toEqual(14.91);
                    });
                });
            });

            describe('when the video speed is not 1.0x', function() {
                it('trigger seek event with the correct time', function() {
                    runs(function() {
                        state.videoSpeedControl.currentSpeed = '0.75';
                        $('.subtitles li span[data-start="14910"]').trigger('click');
                        expect(state.videoPlayer.currentTime).toEqual(14.91);
                    });
                });
            });

            describe('when the player type is Flash at speed 0.75x',
                function() {
                    it('trigger seek event with the correct time', function() {
                        runs(function() {
                            state.videoSpeedControl.currentSpeed = '0.75';
                            state.currentPlayerMode = 'flash';
                            $('.subtitles li span[data-start="14910"]').trigger('click');
                            expect(state.videoPlayer.currentTime).toEqual(15);
                        });
                    });
                });
        });

        describe('toggleTranscript', function() {
            beforeEach(function() {
                state = jasmine.initializePlayer();
                $('.subtitles li span[data-index=1]').addClass('current');
            });

            describe('when the transcript is visible', function() {
                beforeEach(function() {
                    state.el.removeClass('closed');
                    state.videoCaption.toggleTranscript(jQuery.Event('click'));
                });

                it('hide the transcript', function() {
                    expect(state.el).toHaveClass('closed');
                });
            });

            describe('when the transcript is hidden', function() {
                beforeEach(function() {
                    state.el.addClass('closed');
                    state.videoCaption.toggleTranscript(jQuery.Event('click'));
                    jasmine.clock().install();
                });

                afterEach(function() {
                    jasmine.clock().uninstall();
                });

                it('show the transcript', function() {
                    expect(state.el).not.toHaveClass('closed');
                });

                // Test turned off due to flakiness (11/25/13)
                xit('scroll the transcript', function() {
                    // After transcripts are shown, and the video plays for a
                    // bit.
                    jasmine.clock().tick(1000);

                    // The transcripts should have advanced by at least one
                    // position. When they advance, the list scrolls. The
                    // current transcript position should be constantly
                    // visible.
                    runs(function() {
                        expect($.fn.scrollTo).toHaveBeenCalled();
                    });
                });
            });
        });

        describe('transcript accessibility', function() {
            beforeEach(function(done) {
                state = jasmine.initializePlayer();

                jasmine.waitUntil(function() {
                    return state.videoCaption.rendered;
                }).then(done);
            });

            describe('when getting focus through TAB key', function() {
                beforeEach(function() {
                    state.videoCaption.isMouseFocus = false;
                    $('.subtitles li span[data-index=0]').trigger(
                        jQuery.Event('focusin')
                    );
                });

                it('shows an outline around the transcript', function() {
                    expect($('.subtitles span[data-index=0]').parent())
                        .toHaveClass('focused');
                });

                it('has automatic scrolling disabled', function() {
                    expect(state.videoCaption.autoScrolling).toBe(false);
                });
            });

            describe('when loosing focus through TAB key', function() {
                beforeEach(function() {
                    $('.subtitles li span[data-index=0]').trigger(
                        jQuery.Event('blur')
                    );
                });

                it('does not show an outline around the transcript', function() {
                    expect($('.subtitles li span[data-index=0]'))
                        .not.toHaveClass('focused');
                });

                it('has automatic scrolling enabled', function() {
                    expect(state.videoCaption.autoScrolling).toBe(true);
                });
            });

            describe(
                'when same transcript gets the focus through mouse after '
                + 'having focus through TAB key',
                function() {
                    beforeEach(function() {
                        state.videoCaption.isMouseFocus = false;
                        $('.subtitles li span[data-index=0]')
                            .trigger(jQuery.Event('focus'));
                        $('.subtitles li span[data-index=0]')
                            .trigger(jQuery.Event('mousedown'));
                    });

                    it('does not show an outline around it', function() {
                        expect($('.subtitles li span[data-index=0]'))
                            .not.toHaveClass('focused');
                    });

                    it('has automatic scrolling enabled', function() {
                        expect(state.videoCaption.autoScrolling).toBe(true);
                    });
                });

            describe(
                'when a second transcript gets focus through mouse after '
                + 'first had focus through TAB key',
                function() {
                    var $subDataLiIdx0, $subDataLiIdx1;

                    beforeEach(function() {
                        $subDataLiIdx0 = $('.subtitles li span[data-index=0]');
                        $subDataLiIdx1 = $('.subtitles li span[data-index=1]');

                        state.videoCaption.isMouseFocus = false;

                        $subDataLiIdx0.trigger(jQuery.Event('focus'));
                        $subDataLiIdx0.trigger(jQuery.Event('blur'));

                        state.videoCaption.isMouseFocus = true;

                        $subDataLiIdx1.trigger(jQuery.Event('mousedown'));
                    });

                    it('does not show an outline around the first', function() {
                        expect($subDataLiIdx0).not.toHaveClass('focused');
                    });

                    it('does not show an outline around the second', function() {
                        expect($subDataLiIdx1).not.toHaveClass('focused');
                    });

                    it('has automatic scrolling enabled', function() {
                        expect(state.videoCaption.autoScrolling).toBe(true);
                    });
                });
        });

        describe('google disclaimer', () => { 
            const BASE_CAPTIONS = [
                'this is a caption',
                'this is another caption',
                'quite lovely weather we\'re having today',
                'indeed',
            ]
            var Caption;

            const makeCaptions = (provider) => {
                const providerAttr = provider ? ` data-provider="${provider}"` : '';
                const aiGeneratedTag = `<span id="captions-ai-generated" ${providerAttr}></span>`;
                var captions = [...BASE_CAPTIONS];
                captions[0] = aiGeneratedTag + captions[0];
                return captions;
            };

            beforeEach(() => {
                state = jasmine.initializePlayer();
                Caption = state.videoCaption;
            })
            
            it('not shown when captions are not ai generated', () => {
                Caption.updateGoogleDisclaimer(BASE_CAPTIONS)
                expect(Caption.shouldShowGoogleDisclaimer).toBe(false);
            });

            it('not shown when captions are not generated by gcp', () => {
                Caption.updateGoogleDisclaimer(makeCaptions('someoneElse'))
                expect(Caption.shouldShowGoogleDisclaimer).toBe(false);
            });

            it('shown when captions are generated by gcp', () => {
                Caption.updateGoogleDisclaimer(makeCaptions('gcp'))
                expect(Caption.shouldShowGoogleDisclaimer).toBe(true);
            });

            it('shown when captions have no provider', () => {
                Caption.updateGoogleDisclaimer(makeCaptions())
                expect(Caption.shouldShowGoogleDisclaimer).toBe(true);
            });
         });
    });
}).call(this);
