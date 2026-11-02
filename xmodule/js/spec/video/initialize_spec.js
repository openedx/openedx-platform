import Initialize from '../../../assets/video/public/js/01_initialize.js';

describe('Initialize', function() {
    var state = {};

    afterEach(function() {
        state = {};
    });

    describe('getCurrentLanguage', function() {
        var msg;

        beforeEach(function() {
            state.config = {};
            state.config.transcriptLanguages = {
                de: 'German',
                en: 'English',
                uk: 'Ukrainian'
            };
        });

        it('returns current language', function() {
            var expected;

            state.lang = 'de';
            expected = Initialize.prototype.getCurrentLanguage.call(state);
            expect(expected).toBe('de');
        });

        msg = 'returns `en`, if language isn\'t available for the video';
        it(msg, function() {
            var expected;

            state.lang = 'zh';
            expected = Initialize.prototype.getCurrentLanguage.call(state);
            expect(expected).toBe('en');
        });

        msg = 'returns any available language, if current and `en` '
        + 'languages aren\'t available for the video';
        it(msg, function() {
            var expected;

            state.lang = 'zh';
            state.config.transcriptLanguages = {
                de: 'German',
                uk: 'Ukrainian'
            };
            expected = Initialize.prototype.getCurrentLanguage.call(state);
            expect(expected).toBe('uk');
        });

        it('returns `null`, if transcript unavailable', function() {
            var expected;

            state.lang = 'zh';
            state.config.transcriptLanguages = {};
            expected = Initialize.prototype.getCurrentLanguage.call(state);
            expect(expected).toBeNull();
        });
    });

    describe('getDuration', function() {
        beforeEach(function() {
            state = {
                speed: '1.50',
                metadata: {
                    testId: {
                        duration: 'PT6M40S'
                    },
                    videoId: {
                        duration: 'PT1M40S'
                    }
                },
                videos: {
                    '1.0': 'testId',
                    '1.50': 'videoId'
                },
                youtubeId: Initialize.prototype.youtubeId,
                isFlashMode: jasmine.createSpy().and.returnValue(false)
            };
        });

        it('returns duration for the 1.0 speed if speed is not 1.0', function() {
            var expected;

            state.speed = '1.50';
            expected = Initialize.prototype.getDuration.call(state);

            expect(expected).toEqual(400);
        });

        describe('Flash mode', function() {
            it('returns duration for current video', function() {
                var expected;

                state.isFlashMode.and.returnValue(true);
                expected = Initialize.prototype.getDuration.call(state);

                expect(expected).toEqual(100);
            });

            it('returns duration for the 1.0 speed as a fall-back', function() {
                var expected;

                state.isFlashMode.and.returnValue(true);
                state.speed = '2.0';
                expected = Initialize.prototype.getDuration.call(state);

                expect(expected).toEqual(400);
            });
        });
    });

    describe('youtubeId', function() {
        beforeEach(function() {
            state = {
                speed: '1.50',
                videos: {
                    '0.50': '7tqY6eQzVhE',
                    '1.0': 'cogebirgzzM',
                    '1.50': 'abcdefghijkl'
                },
                isFlashMode: jasmine.createSpy().and.returnValue(false)
            };
        });

        describe('with speed', function() {
            it('return the video id for given speed', function() {
                $.each(state.videos, function(speed, videoId) {
                    var expected = Initialize.prototype.youtubeId.call(
                        state, speed
                    );

                    expect(videoId).toBe(expected);
                });
            });
        });

        describe('without speed for flash mode', function() {
            it('return the video id for current speed', function() {
                var expected;

                state.isFlashMode.and.returnValue(true);
                expected = Initialize.prototype.youtubeId.call(state);

                expect(expected).toEqual('abcdefghijkl');
            });
        });

        describe('without speed for youtube html5 mode', function() {
            it('return the video id for 1.0x speed', function() {
                var expected = Initialize.prototype.youtubeId.call(state);

                expect(expected).toEqual('cogebirgzzM');
            });
        });

        describe('speed is absent in the list of video speeds', function() {
            it('return the video id for 1.0x speed', function() {
                var expected = Initialize.prototype.youtubeId.call(state, '0.0');

                expect(expected).toEqual('cogebirgzzM');
            });
        });
    });

    describe('setSpeed', function() {
        describe('YT', function() {
            beforeEach(function() {
                state = {
                    speeds: ['0.25', '0.50', '1.0', '1.50', '2.0'],
                    storage: jasmine.createSpyObj('storage', ['setItem'])
                };
            });

            it('check mapping', function() {
                var map = {
                    0.75: '0.50',
                    1.25: '1.50'
                };

                $.each(map, function(key, expected) {
                    Initialize.prototype.setSpeed.call(state, key);
                    expect(state.speed).toBe(parseFloat(expected));
                });
            });
        });

        describe('HTML5', function() {
            beforeEach(function() {
                state = {
                    speeds: ['0.75', '1.0', '1.25', '1.50', '2.0'],
                    storage: jasmine.createSpyObj('storage', ['setItem'])
                };
            });

            describe('when 0.75 speed is available', function() {
                beforeEach(function() {
                    Initialize.prototype.setSpeed.call(state, '0.75');
                });

                it('set new speed', function() {
                    expect(state.speed).toEqual(0.75);
                });
            });

            describe('when 2.0 speed is available', function() {
                beforeEach(function() {
                    Initialize.prototype.setSpeed.call(state, '2.0');
                });

                it('set new speed', function() {
                    expect(state.speed).toEqual(2.0);
                });
            });

            describe('when new speed is not available', function() {
                beforeEach(function() {
                    Initialize.prototype.setSpeed.call(state, '1.75');
                });

                it('set speed to 1.0x', function() {
                    expect(state.speed).toEqual(1);
                });
            });

            it('check mapping', function() {
                var map = {
                    0.25: '0.75',
                    '0.50': '0.75'
                };

                $.each(map, function(key, expected) {
                    Initialize.prototype.setSpeed.call(state, key);
                    expect(state.speed).toBe(parseFloat(expected));
                });
            });
        });
    });

    describe('setPlayerMode', function() {
        beforeEach(function() {
            state = {
                currentPlayerMode: 'flash'
            };
        });

        it('updates player mode', function() {
            var setPlayerMode = Initialize.prototype.setPlayerMode;

            setPlayerMode.call(state, 'html5');
            expect(state.currentPlayerMode).toBe('html5');
            setPlayerMode.call(state, 'flash');
            expect(state.currentPlayerMode).toBe('flash');
        });

        it('sets default mode if passed is not supported', function() {
            var setPlayerMode = Initialize.prototype.setPlayerMode;

            setPlayerMode.call(state, '77html77');
            expect(state.currentPlayerMode).toBe('html5');
        });
    });

    describe('getPlayerMode', function() {
        beforeEach(function() {
            state = {
                currentPlayerMode: 'flash'
            };
        });

        it('returns current player mode', function() {
            var getPlayerMode = Initialize.prototype.getPlayerMode,
                actual = getPlayerMode.call(state);

            expect(actual).toBe(state.currentPlayerMode);
        });
    });

    describe('isFlashMode', function() {
        it('returns `true` if player in `flash` mode', function() {
            var testState = {
                    getPlayerMode: jasmine.createSpy().and.returnValue('flash')
                },
                isFlashMode = Initialize.prototype.isFlashMode,
                actual = isFlashMode.call(testState);

            expect(actual).toBeTruthy();
        });

        it('returns `false` if player is not in `flash` mode', function() {
            var testState = {
                    getPlayerMode: jasmine.createSpy().and.returnValue('html5')
                },
                isFlashMode = Initialize.prototype.isFlashMode,
                actual = isFlashMode.call(testState);

            expect(actual).toBeFalsy();
        });
    });

    describe('isHtml5Mode', function() {
        it('returns `true` if player in `html5` mode', function() {
            var testState = {
                    getPlayerMode: jasmine.createSpy().and.returnValue('html5')
                },
                isHtml5Mode = Initialize.prototype.isHtml5Mode,
                actual = isHtml5Mode.call(testState);

            expect(actual).toBeTruthy();
        });

        it('returns `false` if player is not in `html5` mode', function() {
            var testState = {
                    getPlayerMode: jasmine.createSpy().and.returnValue('flash')
                },
                isHtml5Mode = Initialize.prototype.isHtml5Mode,
                actual = isHtml5Mode.call(testState);

            expect(actual).toBeFalsy();
        });
    });
});
