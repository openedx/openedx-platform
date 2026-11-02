'use strict';

/**
 * Provides convenient way to process big amount of data without UI blocking.
 *
 * @param {array} list Array to process.
 * @param {function} process Calls this function on each item in the list.
 * @return {array} Returns a Promise object to observe when all actions of a
 *                 certain type bound to the collection, queued or not, have finished.
 */
let AsyncProcess = {
    array: function(list, process) {
        if (!_.isArray(list)) {
            return $.Deferred().reject().promise();
        }

        if (!_.isFunction(process) || !list.length) {
            return $.Deferred().resolve(list).promise();
        }

        let MAX_DELAY = 50, // maximum amount of time that js code should be allowed to run continuously
            dfd = $.Deferred();
        let result = [];
        let index = 0;
        let len = list.length;

        let getCurrentTime = function() {
            return (new Date()).getTime();
        };

        let handler = function() {
            let start = getCurrentTime();

            do {
                result[index] = process(list[index], index);
                index++;
            } while (index < len && getCurrentTime() - start < MAX_DELAY);

            if (index < len) {
                setTimeout(handler, 25);
            } else {
                dfd.resolve(result);
            }
        };

        setTimeout(handler, 25);

        return dfd.promise();
    }
};

export default AsyncProcess;
