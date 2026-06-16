(function() {
    'use strict';

    describe('VideoVolumeControl', function() {
        var state, oldOTBD, volumeControl;

        var KEY = $.ui.keyCode,

            keyPressEvent = function(key) {
                return $.Event('keydown', {keyCode: key});
            };

        beforeEach(function() {
            oldOTBD = window.onTouchBasedDevice;
            window.onTouchBasedDevice = jasmine.createSpy('onTouchBasedDevice')
                .and.returnValue(null);
        });

        afterEach(function() {
            $('source').remove();
            window.onTouchBasedDevice = oldOTBD;
            state.storage.clear();
            state.videoPlayer.destroy();
        });

        it('Volume level has correct value even if cookie is broken', function() {
            $.cookie.and.returnValue('broken_cookie');
            state = jasmine.initializePlayer();
            volumeControl = state.videoVolumeControl;
            expect(volumeControl.volume).toEqual(100);
        });

        describe('constructor', function() {
            beforeEach(function() {
                spyOn($.fn, 'slider').and.callThrough();
                $.cookie.and.returnValue('75');
                state = jasmine.initializePlayer();
                volumeControl = state.videoVolumeControl;
            });

            it('initialize volume to 75%', function() {
                expect(volumeControl.volume).toEqual(75);
            });

            it('render the volume control', function(done) {
                jasmine.waitUntil(function() {
                    return state.videoVolumeControl &&
                        state.videoVolumeControl.el &&
                        state.videoVolumeControl.el.closest('.secondary-controls').length === 1;
                }).then(function() {
                    expect(state.videoVolumeControl.el).toBeInDOM();
                    done();
                }).fail(function() {
                    done.fail('VideoVolumeControl did not render into .secondary-controls before the test timed out.');
                });
            });

            it('create the slider', function() {
                expect($.fn.slider.calls.argsFor(2)).toEqual([{
                    orientation: 'vertical',
                    range: 'min',
                    min: 0,
                    max: 100,
                    slide: jasmine.any(Function)
                }]);
                expect($.fn.slider).toHaveBeenCalledWith(
                    'value', volumeControl.volume
                );
            });

            it('add ARIA attributes to live region', function() {
                var $liveRegion = $('.video-live-region');

                expect($liveRegion).toHaveAttrs({
                    'aria-live': 'polite'
                });
            });

            it('add ARIA attributes to volume control', function() {
                var $button = $('.volume .control');

                expect($button).toHaveAttrs({
                    'aria-disabled': 'false'
                });
            });

            it('bind the volume control', function() {
                var $button = $('.volume .control');

                expect($button).toHandle('keydown');
                expect($button).toHandle('mousedown');
                expect($('.volume')).not.toHaveClass('is-opened');

                $('.volume').mouseenter();
                expect($('.volume')).toHaveClass('is-opened');

                $('.volume').mouseleave();
                expect($('.volume')).not.toHaveClass('is-opened');
            });
        });

        describe('setVolume', function() {
            beforeEach(function() {
                state = jasmine.initializePlayer();
                volumeControl = state.videoVolumeControl;

                jasmine.addMatchers({
                    assertLiveRegionState: function() {
                        return {
                            compare: function(actual, volume, expectation) {
                                var $region = $('.video-live-region');

                                var getExpectedText = function(text) {
                                    return text + ' Volume.';
                                };

                                actual.setVolume(volume, true, true);
                                return {
                                    pass: $region.text() === getExpectedText(expectation)
                                };
                            }
                        };
                    }
                });
            });

            it('update is not called, if new volume equals current', function() {
                volumeControl.volume = 60;
                spyOn(volumeControl, 'updateSliderView');
                volumeControl.setVolume(60, false, true);
                expect(volumeControl.updateSliderView).not.toHaveBeenCalled();
            });

            it('volume is changed on sliding', function() {
                volumeControl.onSlideHandler(null, {value: 99});
                expect(volumeControl.volume).toBe(99);
            });

            describe('when the new volume is more than 0', function() {
                beforeEach(function() {
                    volumeControl.setVolume(60, false, true);
                });

                it('set the player volume', function() {
                    expect(volumeControl.volume).toEqual(60);
                });

                it('remove muted class', function() {
                    expect($('.volume')).not.toHaveClass('is-muted');
                });
            });

            describe('when the new volume is more than 0, but was 0', function() {
                it('remove muted class', function() {
                    volumeControl.setVolume(0, false, true);
                    expect($('.volume')).toHaveClass('is-muted');
                    state.el.trigger('volumechange', [20]);
                    expect($('.volume')).not.toHaveClass('is-muted');
                });
            });

            describe('when the new volume is 0', function() {
                beforeEach(function() {
                    volumeControl.setVolume(0, false, true);
                });

                it('set the player volume', function() {
                    expect(volumeControl.volume).toEqual(0);
                });

                it('add muted class', function() {
                    expect($('.volume')).toHaveClass('is-muted');
                });
            });

            it('when the new volume is Muted', function() {
                expect(volumeControl).assertLiveRegionState(0, 'Muted');
            });

            it('when the new volume is in ]0,20]', function() {
                expect(volumeControl).assertLiveRegionState(10, 'Very low');
            });

            it('when the new volume is in ]20,40]', function() {
                expect(volumeControl).assertLiveRegionState(30, 'Low');
            });

            it('when the new volume is in ]40,60]', function() {
                expect(volumeControl).assertLiveRegionState(50, 'Average');
            });

            it('when the new volume is in ]60,80]', function() {
                expect(volumeControl).assertLiveRegionState(70, 'Loud');
            });

            it('when the new volume is in ]80,100[', function() {
                expect(volumeControl).assertLiveRegionState(90, 'Very loud');
            });

            it('when the new volume is Maximum', function() {
                expect(volumeControl).assertLiveRegionState(100, 'Maximum');
            });
        });

        describe('increaseVolume', function() {
            beforeEach(function() {
                state = jasmine.initializePlayer();
                volumeControl = state.videoVolumeControl;
            });

            it('volume is increased correctly', function() {
                var $button = $('.volume .control');
                volumeControl.volume = 60;

                // adjust the volume
                $button.focus();
                $button.trigger(keyPressEvent(KEY.UP));
                expect(volumeControl.volume).toEqual(80);
            });

            it('volume level is not changed if it is already max', function() {
                volumeControl.volume = 100;
                volumeControl.increaseVolume();
                expect(volumeControl.volume).toEqual(100);
            });
        });

        describe('decreaseVolume', function() {
            beforeEach(function() {
                state = jasmine.initializePlayer();
                volumeControl = state.videoVolumeControl;
            });

            it('volume is decreased correctly', function() {
                var $button = $('.volume .control');
                volumeControl.volume = 60;

                // adjust the volume
                $button.focus();
                $button.trigger(keyPressEvent(KEY.DOWN));
                expect(volumeControl.volume).toEqual(40);
            });

            it('volume level is not changed if it is already min', function() {
                volumeControl.volume = 0;
                volumeControl.decreaseVolume();
                expect(volumeControl.volume).toEqual(0);
            });
        });

        describe('toggleMute', function() {
            beforeEach(function() {
                state = jasmine.initializePlayer();
                volumeControl = state.videoVolumeControl;
            });

            describe('when the current volume is more than 0', function() {
                beforeEach(function() {
                    volumeControl.volume = 60;
                    volumeControl.button.trigger('mousedown');
                });

                it('save the previous volume', function() {
                    expect(volumeControl.storedVolume).toEqual(60);
                });

                it('set the player volume', function() {
                    expect(volumeControl.volume).toEqual(0);
                });
            });

            describe('when the current volume is 0', function() {
                beforeEach(function() {
                    volumeControl.volume = 0;
                    volumeControl.storedVolume = 60;
                    volumeControl.button.trigger('mousedown');
                });

                it('set the player volume to previous volume', function() {
                    expect(volumeControl.volume).toEqual(60);
                });
            });
        });

        describe('keyDownHandler', function() {
            beforeEach(function() {
                state = jasmine.initializePlayer();
                volumeControl = state.videoVolumeControl;
            });

            var assertVolumeIsNotChanged = function(eventObject) {
                volumeControl.volume = 60;
                state.el.trigger(jQuery.Event('keydown', eventObject));
                expect(volumeControl.volume).toEqual(60);
            };

            it('nothing happens if ALT+keyUp are pushed down', function() {
                assertVolumeIsNotChanged({
                    keyCode: KEY.UP,
                    altKey: true
                });
            });

            it('nothing happens if SHIFT+keyUp are pushed down', function() {
                assertVolumeIsNotChanged({
                    keyCode: KEY.UP,
                    shiftKey: true
                });
            });

            it('nothing happens if SHIFT+keyDown are pushed down', function() {
                assertVolumeIsNotChanged({
                    keyCode: KEY.DOWN,
                    shiftKey: true
                });
            });
        });

        describe('keyDownButtonHandler', function() {
            beforeEach(function() {
                state = jasmine.initializePlayer();
                volumeControl = state.videoVolumeControl;
            });

            it('nothing happens if ALT+ENTER are pushed down', function() {
                var isMuted = volumeControl.getMuteStatus();
                $('.volume .control').trigger(jQuery.Event('keydown', {
                    keyCode: KEY.ENTER,
                    altKey: true
                }));
                expect(volumeControl.getMuteStatus()).toEqual(isMuted);
            });
        });
    });
}).call(this);
