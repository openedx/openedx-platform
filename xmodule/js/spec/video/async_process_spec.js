import AsyncProcess from '../../../assets/video/public/js/00_async_process.js';

let getArrayNthLength = function(n, multiplier) {
    let result = [],
        mul = multiplier || 1;

    for (let i = 0; i < n; i++) {
        result[i] = i * mul;
    }

    return result;
},
items = getArrayNthLength(1000);

describe('AsyncProcess', function() {
    it('Array is processed successfully', function(done) {
        var processedArray,
            expectedArray = getArrayNthLength(1000, 2),
            process = function(item) {
                return 2 * item;
            };

        AsyncProcess.array(items, process).done(function(result) {
            processedArray = result;
        });

        jasmine.waitUntil(function() {
            return processedArray;
        }).then(function() {
            expect(processedArray).toEqual(expectedArray);
        }).always(done);
    });

    it('If non-array is passed, error callback is called', function(done) {
        var isError,
            process = function() {};

        AsyncProcess.array('string', process).fail(function() {
            isError = true;
        });

        jasmine.waitUntil(function() {
            return isError;
        }).then(function() {
            expect(isError).toBeTruthy();
        }).always(done);
    });

    it('If an empty array is passed, returns initial array', function(done) {
        var processedArray,
            process = function() {};

        AsyncProcess.array([], process).done(function(result) {
            processedArray = result;
        });

        jasmine.waitUntil(function() {
            return processedArray;
        }).then(function() {
            expect(processedArray).toEqual([]);
        }).always(done);
    });

    it('If no process function passed, returns initial array', function(done) {
        var processedArray;

        AsyncProcess.array(items).done(function(result) {
            processedArray = result;
        });

        jasmine.waitUntil(function() {
            return processedArray;
        }).then(function() {
            expect(processedArray).toEqual(items);
        }).always(done);
    });
});
