'use strict';

/**
 * Provides convenient way to work with iterable data.
 * @exports video/00_iterator.js
 * @constructor
 * @param {array} list Array to be iterated.
 */
let Iterator = function(list) {
    this.list = list;
    this.index = 0;
    this.size = this.list.length;
    this.lastIndex = this.list.length - 1;
};

Iterator.prototype = {

    /**
     * Checks validity of provided index for the iterator.
     * @access protected
     * @param {numebr} index
     * @return {boolean}
     */
    _isValid: function(index) {
        return _.isNumber(index) && index < this.size && index >= 0;
    },

    /**
     * Returns next element.
     * @param {number} [index] Updates current position.
     * @return {any}
     */
    next: function(index) {
        if (!(this._isValid(index))) {
            index = this.index;
        }

        this.index = (index >= this.lastIndex) ? 0 : index + 1;

        return this.list[this.index];
    },

    /**
     * Returns previous element.
     * @param {number} [index] Updates current position.
     * @return {any}
     */
    prev: function(index) {
        if (!(this._isValid(index))) {
            index = this.index;
        }

        this.index = (index < 1) ? this.lastIndex : index - 1;

        return this.list[this.index];
    },

    /**
     * Returns last element in the list.
     * @return {any}
     */
    last: function() {
        return this.list[this.lastIndex];
    },

    /**
     * Returns first element in the list.
     * @return {any}
     */
    first: function() {
        return this.list[0];
    },

    /**
     * Returns `true` if current position is last for the iterator.
     * @return {boolean}
     */
    isEnd: function() {
        return this.index === this.lastIndex;
    }
};

export default Iterator;
