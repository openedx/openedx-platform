(function(WAIT_TIMEOUT) {
    'use strict';

    describe('VideoControl', function() {
        var state, oldOTBD;

        beforeEach(function() {
            oldOTBD = window.onTouchBasedDevice;
            window.onTouchBasedDevice = jasmine
                .createSpy('onTouchBasedDevice').and.returnValue(null);
        });

        afterEach(function() {
            $('source').remove();
            state.storage.clear();
            state.videoPlayer.destroy();
            window.onTouchBasedDevice = oldOTBD;
        });

        describe('constructor', function() {
            beforeEach(function() {
                window.VideoState = {};
                state = jasmine.initializePlayer();
            });

            it('render the video controls', function() {
                expect($('.video-controls')).toContainElement(
                    [
                        '.slider',
                        'ul.vcr',
                        'a.play',
                        '.vidtime'
                    ].join(',')
                );

                expect($('.video-controls').find('.vidtime'))
                    .toHaveText('0:00 / 0:00');
            });
        });

        describe('constructor with start-time', function() {
            it(
                'saved position is 0, timer slider and VCR set to start-time',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        start: 10,
                        savedVideoPosition: 0
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();
                        return isFinite(duration) && duration > 0 && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 1:00');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(10);
                    }).always(done);
                });

            it(
                'saved position is after start-time, '
                + 'timer slider and VCR set to saved position',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        start: 10,
                        savedVideoPosition: 15
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:05 / 1:00');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(15);

                        state.storage.clear();
                    }).always(done);
                });

            it(
                'saved position is negative, '
                + 'timer slider and VCR set to start-time',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        start: 10,
                        savedVideoPosition: -15
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 1:00');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(10);

                        state.storage.clear();
                    }).always(done);
                });

            it(
                'saved position is not a number, '
                + 'timer slider and VCR set to start-time',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        start: 10,
                        savedVideoPosition: 'a'
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 1:00');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(10);

                        state.storage.clear();
                    }).always(done);
                });

            it(
                'saved position is greater than end-time, '
                + 'timer slider and VCR set to start-time',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        start: 10,
                        savedVideoPosition: 10000
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 1:00');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(10);

                        state.storage.clear();
                    }).always(done);
                });
        });

        describe('constructor with end-time', function() {
            it('displays the correct time when startTime and endTime are specified', function(done) {
                let duration;
                state = jasmine.initializePlayer({
                    start: 10,
                    end: 20
                });
                spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                jasmine.waitUntil(function() {
                    duration = state.videoPlayer.duration();
                    return isFinite(duration) && duration > 0 && isFinite(state.videoPlayer.startTime);
                }).then(function() {
                    // Update the video control time display
                    state.videoControl.updateVcrVidTime({
                        time: 15,
                        duration: 60
                    });

                    jasmine.waitUntil(function() {
                        var expectedValue = $('.video-controls').find('.vidtime');
                        return expectedValue.text().indexOf('0:05 / 0:10') !== -1; // Expecting 15 seconds - 10 seconds = 5 seconds
                    }).then(function() {
                        expect($('.video-controls').find('.vidtime')).toHaveText('0:05 / 0:10');
                    }).always(done);
                }).always(done);
            });

            it(
                'saved position is 0, timer slider and VCR set to 0:00 '
                + 'and ending at specified end-time',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        end: 20,
                        savedVideoPosition: 0
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 0:20');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(0);

                        state.storage.clear();
                    }).always(done);
                });

            it(
                'saved position is after start-time, '
                + 'timer slider and VCR set to saved position',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        end: 20,
                        savedVideoPosition: 15
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:15 / 0:20');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(15);

                        state.storage.clear();
                    }).always(done);
                });

            // TODO: Fix!
            it(
                'saved position is negative, timer slider and VCR set to 0:00',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        end: 20,
                        savedVideoPosition: -15
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 0:20');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(0);

                        state.storage.clear();
                    }).always(done);
                });

            it(
                'saved position is not a number, '
                + 'timer slider and VCR set to 0:00',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        end: 20,
                        savedVideoPosition: 'a'
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 0:20');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(0);

                        state.storage.clear();
                    }).always(done);
                });

            // TODO: Fix!
            it(
                'saved position is greater than end-time, '
                + 'timer slider and VCR set to 0:00',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        end: 20,
                        savedVideoPosition: 10000
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 0:20');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(0);

                        state.storage.clear();
                    }).always(done);
                });
        });

        describe('constructor with start-time and end-time', function() {
            it(
                'saved position is 0, timer slider and VCR set to appropriate start and end times',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        start: 10,
                        end: 20,
                        savedVideoPosition: 0
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 0:10');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(10);

                        state.storage.clear();
                    }).always(done);
                });

            it(
                'saved position is after start-time, '
                + 'timer slider and VCR set to saved position',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        start: 10,
                        end: 20,
                        savedVideoPosition: 15
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:05 / 0:10');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(15);

                        state.storage.clear();
                    }).always(done);
                });

            it(
                'saved position is negative, '
                + 'timer slider and VCR set to start-time',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        start: 10,
                        end: 20,
                        savedVideoPosition: -15
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 0:10');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(10);

                        state.storage.clear();
                    }).always(done);
                });

            it(
                'saved position is not a number, '
                + 'timer slider and VCR set to start-time',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        start: 10,
                        end: 20,
                        savedVideoPosition: 'a'
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 0:10');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(10);

                        state.storage.clear();
                    }).always(done);
                });

            it(
                'saved position is greater than end-time, '
                + 'timer slider and VCR set to start-time',
                function(done) {
                    var duration, sliderEl, expectedValue;

                    window.VideoState = {};
                    state = jasmine.initializePlayer({
                        start: 10,
                        end: 20,
                        savedVideoPosition: 10000
                    });
                    sliderEl = state.videoProgressSlider.slider;
                    spyOn(state.videoPlayer, 'duration').and.returnValue(60);

                    jasmine.waitUntil(function() {
                        duration = state.videoPlayer.duration();

                        return isFinite(duration) && duration > 0
                        && isFinite(state.videoPlayer.startTime);
                    }).then(function() {
                        expectedValue = $('.video-controls').find('.vidtime');
                        expect(expectedValue).toHaveText('0:00 / 0:10');

                        expectedValue = sliderEl.slider('option', 'value');
                        expect(expectedValue).toBe(10);

                        state.storage.clear();
                    }).always(done);
                });
        });

        it('show', function() {
            var controls;
            state = jasmine.initializePlayer();
            controls = state.el.find('.video-controls');
            controls.addClass('is-hidden');

            state.videoControl.show();
            expect(controls).not.toHaveClass('is-hidden');
        });

        it('can destroy itself', function() {
            state = jasmine.initializePlayer();
            state.videoControl.destroy();
            expect(state.videoControl).toBeUndefined();
        });

        it('can focus the first control', function(done) {
            var btnPlay;
            state = jasmine.initializePlayer({focusFirstControl: true});
            btnPlay = state.el.find('.video-controls .play');
            jasmine.waitUntil(function() {
                return state.el.hasClass('is-initialized');
            }).then(function() {
                expect(btnPlay).toBeFocused();
            }).always(done);
        });
    });
}).call(this, window.WAIT_TIMEOUT);
