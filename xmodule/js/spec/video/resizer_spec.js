import Resizer from '../../../assets/video/public/js/00_resizer.js';
import _ from 'underscore';

describe('Resizer', function() {
    var html = [
            '<div '
        + 'class="rszr-wrapper" '
        + 'style="width:200px; height: 200px;"'
    + '>',
            '<div '
            + 'class="rszr-el" '
            + 'style="width:100px; height: 150px;"'
        + '>',
            'Content',
            '</div>',
            '</div>'
        ].join(''),
        config, $container, $element;

    beforeEach(function() {
        setFixtures(html);

        $container = $('.rszr-wrapper');
        $element = $('.rszr-el');
        config = {
            container: $container,
            element: $element
        };

        spyOn(console, 'log');
    });

    it('When Initialize without required parameters, log message is shown',
        function() {
            // eslint-disable-next-line no-new
            new Resizer({ });
            expect(console.log).toHaveBeenCalled();
        }
    );

    it('`alignByWidthOnly` works correctly', function() {
        var resizer = new Resizer(config).alignByWidthOnly(),
            expectedWidth = $container.width(),
            realWidth = $element.width();

        expect(realWidth).toBe(expectedWidth);
    });

    it('`alignByHeightOnly` works correctly', function() {
        var resizer = new Resizer(config).alignByHeightOnly(),
            expectedHeight = $container.height(),
            realHeight = $element.height();

        expect(realHeight).toBe(expectedHeight);
    });

    it('`align` works correctly', function() {
        var resizer = new Resizer(config).align(),
            expectedHeight = $container.height(),
            realHeight = $element.height(),
            expectedWidth = 50,
            realWidth;

        // containerRatio >= elementRatio
        expect(realHeight).toBe(expectedHeight);

        // containerRatio < elementRatio
        $container.width(expectedWidth);
        resizer.align();
        realWidth = $element.width();

        expect(realWidth).toBe(expectedWidth);
    });

    it('`setMode` works correctly', function() {
        var resizer = new Resizer(config).setMode('height'),
            expectedHeight = $container.height(),
            realHeight = $element.height(),
            expectedWidth = 50,
            realWidth;

        // containerRatio >= elementRatio
        expect(realHeight).toBe(expectedHeight);

        // containerRatio < elementRatio
        $container.width(expectedWidth);
        resizer.setMode('width');
        realWidth = $element.width();

        expect(realWidth).toBe(expectedWidth);
    });

    it('`setElement` works correctly', function() {
        var $newElement,
            expectedHeight;

        $container.append('<div '
    + 'id="Another-el" '
    + 'style="width:100px; height: 150px;"'
+ '>');
        $newElement = $('#Another-el');
        expectedHeight = $container.height();

        new Resizer(config).setElement($newElement).alignByHeightOnly();
        expect($element.height()).not.toBe(expectedHeight);
        expect($newElement.height()).toBe(expectedHeight);
    });

    describe('Callbacks', function() {
        var resizer,
            spiesList = [];

        beforeEach(function() {
            var spiesCount = _.range(3);

            spiesList = $.map(spiesCount, function() {
                return jasmine.createSpy();
            });

            resizer = new Resizer(config);
        });

        it('callbacks are called', function() {
            $.each(spiesList, function(index, spy) {
                resizer.callbacks.add(spy);
            });

            resizer.align();

            $.each(spiesList, function(index, spy) {
                expect(spy).toHaveBeenCalled();
            });
        });

        it('callback called just once', function() {
            resizer.callbacks.once(spiesList[0]);

            resizer
                .align()
                .alignByHeightOnly();

            expect(spiesList[0].calls.count()).toEqual(1);
        });

        it('all callbacks are removed', function() {
            $.each(spiesList, function(index, spy) {
                resizer.callbacks.add(spy);
            });

            resizer.callbacks.removeAll();
            resizer.align();

            $.each(spiesList, function(index, spy) {
                expect(spy).not.toHaveBeenCalled();
            });
        });

        it('specific callback is removed', function() {
            $.each(spiesList, function(index, spy) {
                resizer.callbacks.add(spy);
            });

            resizer.callbacks.remove(spiesList[1]);
            resizer.align();

            expect(spiesList[1]).not.toHaveBeenCalled();
        });

        it(
            'Error message is shown when wrong argument type is passed',
            function() {
                var methods = ['add', 'once'],
                    errorMessage = '[Video info]: TypeError: Argument is not a function.',
                    arg = {};

                spyOn(console, 'error');

                $.each(methods, function(index, methodName) {
                    resizer.callbacks[methodName](arg);
                    expect(console.error).toHaveBeenCalledWith(errorMessage);
                    // reset spy
                    console.log.calls.reset();
                });
            });
    });

    describe('Delta', function() {
        var resizer;

        beforeEach(function() {
            resizer = new Resizer(config);
        });

        it('adding delta align correctly by height', function() {
            var delta = 100,
                expectedHeight = $container.height() + delta,
                realHeight;

            resizer
                .delta.add(delta, 'height')
                .setMode('height');

            realHeight = $element.height();

            expect(realHeight).toBe(expectedHeight);
        });

        it('adding delta align correctly by width', function() {
            var delta = 100,
                expectedWidth = $container.width() + delta,
                realWidth;

            resizer
                .delta.add(delta, 'width')
                .setMode('width');

            realWidth = $element.width();

            expect(realWidth).toBe(expectedWidth);
        });

        it('substract delta align correctly by height', function() {
            var delta = 100,
                expectedHeight = $container.height() - delta,
                realHeight;

            resizer
                .delta.substract(delta, 'height')
                .setMode('height');

            realHeight = $element.height();

            expect(realHeight).toBe(expectedHeight);
        });

        it('substract delta align correctly by width', function() {
            var delta = 100,
                expectedWidth = $container.width() - delta,
                realWidth;

            resizer
                .delta.substract(delta, 'width')
                .setMode('width');

            realWidth = $element.width();

            expect(realWidth).toBe(expectedWidth);
        });

        it('reset delta', function() {
            var delta = 100,
                expectedWidth = $container.width(),
                realWidth;

            resizer
                .delta.substract(delta, 'width')
                .delta.reset()
                .setMode('width');

            realWidth = $element.width();

            expect(realWidth).toBe(expectedWidth);
        });
    });
});
