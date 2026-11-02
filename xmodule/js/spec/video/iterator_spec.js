import Iterator from '../../../assets/video/public/js/00_iterator.js';

describe('Iterator', function() {
    var list = ['a', 'b', 'c', 'd', 'e'],
        iterator;

    beforeEach(function() {
        iterator = new Iterator(list);
    });

    it('size contains correct list length', function() {
        expect(iterator.size).toBe(list.length);
        expect(iterator.lastIndex).toBe(list.length - 1);
    });

    describe('next', function() {
        describe('with passed `index`', function() {
            it('returns next item in the list', function() {
                expect(iterator.next(2)).toBe('d');
                expect(iterator.next(0)).toBe('b');
            });

            it('returns first item if index equal last item', function() {
                expect(iterator.next(4)).toBe('a');
            });

            it('returns next item if index is not valid', function() {
                expect(iterator.next(-4)).toBe('b'); // index < 0
                expect(iterator.next(100)).toBe('c'); // index > size
                expect(iterator.next('99')).toBe('d'); // incorrect Type
            });
        });

        describe('without passed `index`', function() {
            it('returns next item in the list', function() {
                expect(iterator.next()).toBe('b');
                expect(iterator.next()).toBe('c');
            });

            it('returns first item if index equal last item', function() {
                expect(iterator.next()).toBe('b');
                expect(iterator.next()).toBe('c');
                expect(iterator.next()).toBe('d');
                expect(iterator.next()).toBe('e');
                expect(iterator.next()).toBe('a');
            });
        });
    });

    describe('prev', function() {
        describe('with passed `index`', function() {
            it('returns previous item in the list', function() {
                expect(iterator.prev(3)).toBe('c');
                expect(iterator.prev(1)).toBe('a');
            });

            it('returns last item if index equal first item', function() {
                expect(iterator.prev(0)).toBe('e');
            });

            it('returns previous item if index is not valid', function() {
                expect(iterator.prev(-4)).toBe('e'); // index < 0
                expect(iterator.prev(100)).toBe('d'); // index > size
                expect(iterator.prev('99')).toBe('c'); // incorrect Type
            });
        });

        describe('without passed `index`', function() {
            it('returns previous item in the list', function() {
                expect(iterator.prev()).toBe('e');
                expect(iterator.prev()).toBe('d');
            });

            it('returns last item if index equal first item', function() {
                expect(iterator.prev()).toBe('e');
            });
        });
    });

    it('returns last item in the list', function() {
        expect(iterator.last()).toBe('e');
    });

    it('returns first item in the list', function() {
        expect(iterator.first()).toBe('a');
    });

    it('isEnd works correctly', function() {
        expect(iterator.isEnd()).toBeFalsy();
        iterator.next(); // => index 1
        expect(iterator.isEnd()).toBeFalsy();
        iterator.next(); // => index 2
        expect(iterator.isEnd()).toBeFalsy();
        iterator.next(); // => index 3
        expect(iterator.isEnd()).toBeFalsy();
        iterator.next(); // => index 4 == last
        expect(iterator.isEnd()).toBeTruthy();
    });
});
