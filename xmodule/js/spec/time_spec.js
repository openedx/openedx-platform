'use strict';

import * as Time from '../../assets/video/public/js/utils/time.js';

describe('Time', function() {
    describe('format', function() {
        describe('with NAN', function() {
            it('return a correct time format', function() {
                expect(Time.format('string')).toEqual('0:00');
                // eslint-disable-next-line no-void
                expect(Time.format(void 0)).toEqual('0:00');
            });
        });

        describe('with duration more than or equal to 1 hour', function() {
            it('return a correct time format', function() {
                expect(Time.format(3600)).toEqual('1:00:00');
                expect(Time.format(7272)).toEqual('2:01:12');
            });
        });

        describe('with duration less than 1 hour', function() {
            it('return a correct time format', function() {
                expect(Time.format(1)).toEqual('0:01');
                expect(Time.format(61)).toEqual('1:01');
                expect(Time.format(3599)).toEqual('59:59');
            });
        });
    });

    describe('formatFull', function() {
        it('gives correct string for times', function() {
            var testTimes = [
                [0, '00:00:00'], [60, '00:01:00'],
                [488, '00:08:08'], [2452, '00:40:52'],
                [3600, '01:00:00'], [28800, '08:00:00'],
                [144532, '40:08:52'], [190360, '52:52:40'],
                [294008, '81:40:08'], [-5, '00:00:00']
            ];

            $.each(testTimes, function(index, times) {
                var timeInt = times[0],
                    timeStr = times[1];

                expect(Time.formatFull(timeInt)).toBe(timeStr);
            });
        });
    });

    describe('convert', function() {
        it('return a correct time based on speed modifier', function() {
            expect(Time.convert(0, 1, 1.5)).toEqual('0.000');
            expect(Time.convert(100, 1, 1.5)).toEqual('66.667');
            expect(Time.convert(100, 1.5, 1)).toEqual('150.000');
        });
    });
});
