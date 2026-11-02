// eslint-disable-next-line no-shadow-restricted-names
(function(undefined) {
    'use strict';

    describe('VideoSpeedControl', function() {
        var state, oldOTBD;

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

        describe('constructor', function() {
            describe('always', function() {
                beforeEach(function() {
                    state = jasmine.initializePlayer();
                });

                it('add the video speed control to player', function() {
                    var $secondaryControls = $('.secondary-controls'),
                        li = $secondaryControls.find('.video-speeds li');

                    expect($secondaryControls).toContainElement('.speeds');
                    expect($secondaryControls).toContainElement('.video-speeds');
                    expect($secondaryControls.find('.value').text())
                        .toBe('1.50x');
                    expect(li.filter('.is-active')).toHaveData(
                        'speed', state.videoSpeedControl.currentSpeed
                    );
                    expect(li.length).toBe(state.speeds.length);

                    $.each(li.toArray().reverse(), function(index, link) {
                        expect($(link).attr('data-speed')).toEqual(state.speeds[index]);
                        expect($(link).find('.speed-option').text()).toBe(
                            state.speeds[index] + 'x'
                        );
                    });
                });
            });

            describe('when running on touch based device', function() {
                $.each(['iPad', 'Android'], function(index, device) {
                    it('is not rendered on' + device, function() {
                        window.onTouchBasedDevice.and.returnValue([device]);
                        state = jasmine.initializePlayer();

                        expect(state.el.find('.speeds')).not.toExist();
                    });
                });
            });

            describe('when running on non-touch based device', function() {
                var $speedControl, speedEntries, $speedButton, $speedsContainer,
                    KEY = $.ui.keyCode,

                    keyPressEvent = function(key) {
                        return $.Event('keydown', {keyCode: key});
                    };

                beforeEach(function() {
                    state = jasmine.initializePlayer();
                    $speedControl = $('.speeds');
                    $speedButton = $('.speed-button');
                    $speedsContainer = $('.video-speeds');
                    speedEntries = $speedsContainer.find('.speed-option');
                });

                it('open/close the speed menu on mouseenter/mouseleave',
                    function() {
                        $speedControl.mouseenter();
                        expect($speedControl).toHaveClass('is-opened');
                        $speedControl.mouseleave();
                        expect($speedControl).not.toHaveClass('is-opened');
                    });

                it('do not close the speed menu on mouseleave if a speed '
                    + 'entry has focus', function() {
                    // Open speed meenu. Focus is on last speed entry.
                    $speedControl.trigger(keyPressEvent(KEY.ENTER));
                    $speedControl.mouseenter();
                    expect(speedEntries.last()).toBeFocused();
                    expect($speedControl).toHaveClass('is-opened');
                });

                it('close the speed menu on outside click', function() {
                    $speedControl.trigger(keyPressEvent(KEY.ENTER));
                    $(window).click();
                    expect($speedControl).not.toHaveClass('is-opened');
                });

                it('open the speed menu on ENTER keydown', function() {
                    $speedControl.trigger(keyPressEvent(KEY.ENTER));
                    expect($speedControl).toHaveClass('is-opened');
                    expect(speedEntries.last()).toBeFocused();
                });

                it('open the speed menu on SPACE keydown', function() {
                    $speedControl.trigger(keyPressEvent(KEY.SPACE));
                    expect($speedControl).toHaveClass('is-opened');
                    expect(speedEntries.last()).toBeFocused();
                });

                it('open the speed menu on UP keydown', function() {
                    $speedControl.trigger(keyPressEvent(KEY.UP));
                    expect($speedControl).toHaveClass('is-opened');
                    expect(speedEntries.last()).toBeFocused();
                });

                it('close the speed menu on ESCAPE keydown', function() {
                    $speedControl.trigger(keyPressEvent(KEY.ESCAPE));
                    expect($speedControl).not.toHaveClass('is-opened');
                });

                it('UP and DOWN keydown function as expected on speed entries',
                    function() {
                        var speed_0_75 = speedEntries.filter(':contains("0.75x")'),
                            speed_1_0 = speedEntries.filter(':contains("1.0x")');

                        // First open menu
                        $speedControl.trigger(keyPressEvent(KEY.UP));
                        expect(speed_0_75).toBeFocused();

                        speed_0_75.trigger(keyPressEvent(KEY.UP));
                        expect(speed_1_0).toBeFocused();

                        speed_1_0.trigger(keyPressEvent(KEY.DOWN));
                        expect(speed_0_75).toBeFocused();
                    });

                it('ESC keydown on speed entry closes menu', function() {
                    // First open menu. Focus is on last speed entry.
                    $speedControl.trigger(keyPressEvent(KEY.UP));
                    speedEntries.last().trigger(keyPressEvent(KEY.ESCAPE));

                    // Menu is closed and focus has been returned to speed
                    // control.
                    expect($speedControl).not.toHaveClass('is-opened');
                    expect($speedButton).toBeFocused();
                });

                it('ENTER keydown on speed entry selects 2.0x speed and closes menu',
                    function() {
                    // First open menu.
                        $speedControl.trigger(keyPressEvent(KEY.UP));
                        // Focus on 2.0x speed
                        speedEntries.eq(0).focus();
                        speedEntries.eq(0).trigger(keyPressEvent(KEY.ENTER));

                        // Menu is closed, focus has been returned to speed
                        // control and video speed is 2.0x.
                        expect($speedButton).toBeFocused();
                        expect($('.video-speeds li[data-speed="2.0"]'))
                            .toHaveClass('is-active');
                        expect($('.speeds .value')).toHaveHtml('2.0x');
                    });

                it('SPACE keydown on speed entry selects 0.75x speed and closes menu',
                    function() {
                    // First open menu.
                        $speedControl.trigger(keyPressEvent(KEY.UP));
                        // Focus on 0.75x speed
                        speedEntries.eq(4).focus();
                        speedEntries.eq(4).trigger(keyPressEvent(KEY.SPACE));

                        // Menu is closed, focus has been returned to speed
                        // control and video speed is 0.75x.
                        expect($speedButton).toBeFocused();
                        expect($('.video-speeds li[data-speed="0.75"]'))
                            .toHaveClass('is-active');
                        expect($('.speeds .value')).toHaveHtml('0.75x');
                    });
            });
        });

        describe('changeVideoSpeed', function() {
            // This is an unnecessary test. The internal browser API, and
            // YouTube API detect (and do not do anything) if there is a
            // request for a speed that is already set.
            //
            //     describe("when new speed is the same") ...

            describe('when new speed is not the same', function() {
                beforeEach(function() {
                    state = jasmine.initializePlayer();
                    state.videoSpeedControl.setSpeed(1.0);
                });

                it('trigger speedChange event', function() {
                    spyOnEvent(state.el, 'speedchange');

                    $('li[data-speed="0.75"] .speed-option').click();
                    expect('speedchange').toHaveBeenTriggeredOn(state.el);
                    expect(state.videoSpeedControl.currentSpeed).toEqual('0.75');
                });
            });
        });

        describe('onSpeedChange', function() {
            beforeEach(function() {
                state = jasmine.initializePlayer();
                $('li[data-speed="1.0"]').addClass('is-active').attr('aria-pressed', 'true');
                state.videoSpeedControl.setSpeed(0.75);
            });

            it('set the new speed as active', function() {
                expect($('li[data-speed="1.0"]')).not.toHaveClass('is-active');
                expect($('li[data-speed="1.0"] .speed-option').attr('aria-pressed')).not.toEqual('true');

                expect($('li[data-speed="0.75"]')).toHaveClass('is-active');
                expect($('li[data-speed="0.75"] .speed-option').attr('aria-pressed')).toEqual('true');

                expect($('.speeds .speed-button .value')).toHaveHtml('0.75x');
            });
        });

        it('can destroy itself', function() {
            state = jasmine.initializePlayer();
            state.videoSpeedControl.destroy();
            expect(state.videoSpeedControl).toBeUndefined();
            expect($('.video-speeds')).not.toExist();
            expect($('.speed-button')).not.toExist();
        });
    });
}).call(this);
