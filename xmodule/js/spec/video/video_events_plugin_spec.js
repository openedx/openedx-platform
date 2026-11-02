import '../helper.js';

// eslint-disable-next-line no-shadow-restricted-names
(function(undefined) {
    'use strict';

    var describeInfo, state, oldOTBD;

    describeInfo = new jasmine.DescribeInfo('', function() {
        var Logger = window.Logger;

        beforeEach(function() {
            spyOn(Logger, 'log');
        });

        afterEach(function() {
            $('source').remove();
            window.onTouchBasedDevice = oldOTBD;
            state.storage.clear();
            if (state.videoPlayer) {
                state.videoPlayer.destroy();
            }
        });

        it('can emit "load_video" event', function() {
            state.el.trigger('ready');
            expect(Logger.log).toHaveBeenCalledWith('load_video', {
                id: 'id',
                code: this.code,
                duration: this.duration
            });
        });

        describe('getCurrentTime method', function() {
            it('returns current time adjusted by startTime if video starts from a subsection', function() {
                state.videoPlayer.currentTime = 120;
                state.config.startTime = 30;                
                expect(state.videoEventsPlugin.getCurrentTime()).toBe(90); // 120 - 30 = 90
            });

            it('returns 0 if currentTime is undefined', function() {
                state.config.startTime = 30; // Start time is irrelevant since current time is undefined
                state.videoPlayer.currentTime = undefined;
                expect(state.videoEventsPlugin.getCurrentTime()).toBe(0);
            });

            it('returns unadjusted current time if startTime is not defined', function() {
                state.videoPlayer.currentTime = 60;
                expect(state.videoEventsPlugin.getCurrentTime()).toBe(60); // Returns current time as is
            });
        });

        describe('log method', function() {
            
            it('logs event with adjusted duration when startTime and endTime are defined', function() {
                state.config.startTime = 30;
                state.config.endTime = 150;
                state.duration = 200;

                state.videoEventsPlugin.log('test_event', {});

                expect(Logger.log).toHaveBeenCalledWith('test_event', {
                    id: 'id',
                    code: this.code,
                    duration: 120, // 150 - 30 = 120
                });
            });

            it('can emit "play_video" event when emitPlayVideoEvent is true', function() {
                state.videoEventsPlugin.emitPlayVideoEvent = true;
                spyOn(state.videoEventsPlugin, 'getCurrentTime').and.returnValue(10);
                state.el.trigger('play');
                expect(Logger.log).toHaveBeenCalledWith('play_video', {
                    id: 'id',
                    code: this.code,
                    currentTime: 10,
                    duration: this.duration
                });
                expect(state.videoEventsPlugin.emitPlayVideoEvent).toBeFalsy();
            });

            it('can not emit "play_video" event when emitPlayVideoEvent is false', function() {
                state.videoEventsPlugin.emitPlayVideoEvent = false;
                state.el.trigger('play');
                expect(Logger.log).not.toHaveBeenCalled();
            });

            it('can emit "pause_video" event', function() {
                spyOn(state.videoEventsPlugin, 'getCurrentTime').and.returnValue(10);
                state.el.trigger('pause');
                expect(Logger.log).toHaveBeenCalledWith('pause_video', {
                    id: 'id',
                    code: this.code,
                    currentTime: 10,
                    duration: this.duration
                });
                expect(state.videoEventsPlugin.emitPlayVideoEvent).toBeTruthy();
            });

            it('can emit "complete_video" event when video is marked as complete', function() {
                spyOn(state.videoEventsPlugin, 'getCurrentTime').and.returnValue(10);
                state.el.trigger('complete');
                expect(Logger.log).toHaveBeenCalledWith('complete_video', {
                    id: 'id',
                    code: this.code,
                    currentTime: 10,
                    duration: this.duration
                });
            });

            it('can emit "speed_change_video" event', function() {
                spyOn(state.videoEventsPlugin, 'getCurrentTime').and.returnValue(10);
                state.el.trigger('speedchange', ['2.0', '1.0']);
                expect(Logger.log).toHaveBeenCalledWith('speed_change_video', {
                    id: 'id',
                    code: this.code,
                    current_time: 10,
                    old_speed: '1.0',
                    new_speed: '2.0',
                    duration: this.duration
                });
            });

            it('can emit "seek_video" event', function() {
                state.el.trigger('seek', [1, 0, 'any']);
                expect(Logger.log).toHaveBeenCalledWith('seek_video', {
                    id: 'id',
                    code: this.code,
                    old_time: 0,
                    new_time: 1,
                    type: 'any',
                    duration: this.duration
                });
                expect(state.videoEventsPlugin.emitPlayVideoEvent).toBeTruthy();
            });

            it('can emit "play_video" event after "seek_video" event ', function() {
                state.videoEventsPlugin.emitPlayVideoEvent = false;
                state.el.trigger('seek', [1, 0, 'any']);
                expect(state.videoEventsPlugin.emitPlayVideoEvent).toBeTruthy();
            });

            it('can emit "stop_video" event', function() {
                spyOn(state.videoEventsPlugin, 'getCurrentTime').and.returnValue(10);
                state.el.trigger('ended');
                expect(Logger.log).toHaveBeenCalledWith('stop_video', {
                    id: 'id',
                    code: this.code,
                    currentTime: 10,
                    duration: this.duration
                });
                expect(state.videoEventsPlugin.emitPlayVideoEvent).toBeTruthy();

                Logger.log.calls.reset();
                state.el.trigger('stop');
                expect(Logger.log).toHaveBeenCalledWith('stop_video', {
                    id: 'id',
                    code: this.code,
                    currentTime: 10,
                    duration: this.duration
                });
                expect(state.videoEventsPlugin.emitPlayVideoEvent).toBeTruthy();
            });

            it('can emit "skip_video" event', function() {
                spyOn(state.videoEventsPlugin, 'getCurrentTime').and.returnValue(10);
                state.el.trigger('skip', [false]);
                expect(Logger.log).toHaveBeenCalledWith('skip_video', {
                    id: 'id',
                    code: this.code,
                    currentTime: 10,
                    duration: this.duration
                });
            });

            it('can emit "do_not_show_again_video" event', function() {
                spyOn(state.videoEventsPlugin, 'getCurrentTime').and.returnValue(10);
                state.el.trigger('skip', [true]);
                expect(Logger.log).toHaveBeenCalledWith('do_not_show_again_video', {
                    id: 'id',
                    code: this.code,
                    currentTime: 10,
                    duration: this.duration
                });
            });

            it('can emit "edx.video.language_menu.shown" event', function() {
                state.el.trigger('language_menu:show');
                expect(Logger.log).toHaveBeenCalledWith('edx.video.language_menu.shown', {
                    id: 'id',
                    code: this.code,
                    duration: this.duration
                });
            });

            it('can emit "edx.video.language_menu.hidden" event', function() {
                state.el.trigger('language_menu:hide');
                expect(Logger.log).toHaveBeenCalledWith('edx.video.language_menu.hidden', {
                    id: 'id',
                    code: this.code,
                    language: 'en',
                    duration: this.duration
                });
            });

            it('can emit "show_transcript" event', function() {
                spyOn(state.videoEventsPlugin, 'getCurrentTime').and.returnValue(10);
                state.el.trigger('transcript:show');
                expect(Logger.log).toHaveBeenCalledWith('show_transcript', {
                    id: 'id',
                    code: this.code,
                    current_time: 10,
                    duration: this.duration
                });
            });

            it('can emit "hide_transcript" event', function() {
                spyOn(state.videoEventsPlugin, 'getCurrentTime').and.returnValue(10);
                state.el.trigger('transcript:hide');
                expect(Logger.log).toHaveBeenCalledWith('hide_transcript', {
                    id: 'id',
                    code: this.code,
                    current_time: 10,
                    duration: this.duration
                });
            });

            it('can emit "edx.video.closed_captions.shown" event', function() {
                spyOn(state.videoEventsPlugin, 'getCurrentTime').and.returnValue(10);
                state.el.trigger('captions:show');
                expect(Logger.log).toHaveBeenCalledWith('edx.video.closed_captions.shown', {
                    id: 'id',
                    code: this.code,
                    current_time: 10,
                    duration: this.duration
                });
            });

            it('can emit "edx.video.closed_captions.hidden" event', function() {
                spyOn(state.videoEventsPlugin, 'getCurrentTime').and.returnValue(10);
                state.el.trigger('captions:hide');
                expect(Logger.log).toHaveBeenCalledWith('edx.video.closed_captions.hidden', {
                    id: 'id',
                    code: this.code,
                    current_time: 10,
                    duration: this.duration
                });
            });

            it('can destroy itself', function() {
                var plugin = state.videoEventsPlugin;
                spyOn($.fn, 'off').and.callThrough();
                state.videoEventsPlugin.destroy();
                expect(state.videoEventsPlugin).toBeUndefined();
                expect($.fn.off).toHaveBeenCalledWith({
                    ready: plugin.onReady,
                    play: plugin.onPlay,
                    pause: plugin.onPause,
                    complete: plugin.onComplete,
                    'ended stop': plugin.onEnded,
                    seek: plugin.onSeek,
                    skip: plugin.onSkip,
                    speedchange: plugin.onSpeedChange,
                    autoadvancechange: plugin.onAutoAdvanceChange,
                    'language_menu:show': plugin.onShowLanguageMenu,
                    'language_menu:hide': plugin.onHideLanguageMenu,
                    'transcript:show': plugin.onShowTranscript,
                    'transcript:hide': plugin.onHideTranscript,
                    'captions:show': plugin.onShowCaptions,
                    'captions:hide': plugin.onHideCaptions,
                    destroy: plugin.destroy
                });
            });

            it('logs event with full duration when startTime and endTime are not defined', function() {
                state.config.startTime = undefined;
                state.config.endTime = undefined;
                state.duration = 200;

                state.videoEventsPlugin.log('test_event', {});

                expect(Logger.log).toHaveBeenCalledWith('test_event', {
                    id: 'id',
                    code: this.code,
                    duration: 200 // Full duration as no start/end time adjustment is needed
                });
            });
        });
    });

    describe('VideoPlayer Events plugin', function() {
        beforeEach(function() {
            oldOTBD = window.onTouchBasedDevice;
            window.onTouchBasedDevice = jasmine
                .createSpy('onTouchBasedDevice')
                .and.returnValue(null);
        });

        describe('html5 encoding only', function() {
            beforeEach(function(done) {
                this.code = 'html5';
                this.duration = 111;
                state = jasmine.initializePlayer('video_html5.html');
                done();
            });
            jasmine.getEnv().describe(describeInfo.description, describeInfo.specDefinitions);
        });

        describe('hls encoding', function() {
            beforeEach(function(done) {
                this.code = 'hls';
                this.duration = 111;
                state = jasmine.initializeHLSPlayer();
                done();
            });
            jasmine.getEnv().describe(describeInfo.description, describeInfo.specDefinitions);
        });
    });
}).call(this);
