'use strict';

import _ from 'underscore';


let Resizer = function(params) {
    let defaults = {
            container: window,
            element: null,
            containerRatio: null,
            elementRatio: null
        },
        callbacksList = [],
        delta = {
            height: 0,
            width: 0
        },
        module = {};
    let mode = null,
        config;

    // eslint-disable-next-line no-shadow
    let initialize = function(params) {
        if (!config) {
            config = defaults;
        }

        config = $.extend(true, {}, config, params);

        if (!config.element) {
            console.log(
                'Required parameter `element` is not passed.'
            );
        }

        return module;
    };

    let getData = function() {
        let $container = $(config.container),
            containerWidth = $container.width() + delta.width,
            containerHeight = $container.height() + delta.height;
        let containerRatio = config.containerRatio;

        let $element = $(config.element);
        let elementRatio = config.elementRatio;

        if (!containerRatio) {
            containerRatio = containerWidth / containerHeight;
        }

        if (!elementRatio) {
            elementRatio = $element.width() / $element.height();
        }

        return {
            containerWidth: containerWidth,
            containerHeight: containerHeight,
            containerRatio: containerRatio,
            element: $element,
            elementRatio: elementRatio
        };
    };

    let align = function() {
        let data = getData();

        switch (mode) {
        case 'height':
            alignByHeightOnly();
            break;

        case 'width':
            alignByWidthOnly();
            break;

        default:
            if (data.containerRatio >= data.elementRatio) {
                alignByHeightOnly();
            } else {
                alignByWidthOnly();
            }
            break;
        }

        fireCallbacks();

        return module;
    };

    let alignByWidthOnly = function() {
        let data = getData(),
            height = data.containerWidth / data.elementRatio;

        data.element.css({
            height: height,
            width: data.containerWidth,
            top: 0.5 * (data.containerHeight - height),
            left: 0
        });

        return module;
    };

    let alignByHeightOnly = function() {
        let data = getData(),
            width = data.containerHeight * data.elementRatio;

        data.element.css({
            height: data.containerHeight,
            width: data.containerHeight * data.elementRatio,
            top: 0,
            left: 0.5 * (data.containerWidth - width)
        });

        return module;
    };

    let setMode = function(param) {
        if (_.isString(param)) {
            mode = param;
            align();
        }

        return module;
    };

    let setElement = function(element) {
        config.element = element;

        return module;
    };

    let addCallback = function(func) {
        if ($.isFunction(func)) {
            callbacksList.push(func);
        } else {
            console.error('[Video info]: TypeError: Argument is not a function.');
        }

        return module;
    };

    let addOnceCallback = function(func) {
        if ($.isFunction(func)) {
            let decorator = function() {
                func();
                removeCallback(func);
            };

            addCallback(decorator);
        } else {
            console.error('TypeError: Argument is not a function.');
        }

        return module;
    };

    let fireCallbacks = function() {
        $.each(callbacksList, function(index, callback) {
            callback();
        });
    };

    let removeCallbacks = function() {
        callbacksList.length = 0;

        return module;
    };

    let removeCallback = function(func) {
        let index = $.inArray(func, callbacksList);

        if (index !== -1) {
            return callbacksList.splice(index, 1);
        }
    };

    let resetDelta = function() {
        // eslint-disable-next-line no-multi-assign
        delta.height = delta.width = 0;

        return module;
    };

    let addDelta = function(value, side) {
        if (_.isNumber(value) && _.isNumber(delta[side])) {
            delta[side] += value;
        }

        return module;
    };

    let substractDelta = function(value, side) {
        if (_.isNumber(value) && _.isNumber(delta[side])) {
            delta[side] -= value;
        }

        return module;
    };

    let destroy = function() {
        let data = getData();
        data.element.css({
            height: '', width: '', top: '', left: ''
        });
        removeCallbacks();
        resetDelta();
        mode = null;
    };

    initialize.apply(module, arguments);

    return $.extend(true, module, {
        align: align,
        alignByWidthOnly: alignByWidthOnly,
        alignByHeightOnly: alignByHeightOnly,
        destroy: destroy,
        setParams: initialize,
        setMode: setMode,
        setElement: setElement,
        callbacks: {
            add: addCallback,
            once: addOnceCallback,
            remove: removeCallback,
            removeAll: removeCallbacks
        },
        delta: {
            add: addDelta,
            substract: substractDelta,
            reset: resetDelta
        }
    });
};

export default Resizer;
