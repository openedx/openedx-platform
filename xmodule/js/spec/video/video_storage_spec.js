import VideoStorage from '../../../assets/video/public/js/00_video_storage.js';

describe('VideoStorage', function() {
    var namespace = 'test_storage',
        id = 'video_id';

    afterEach(function() {
        VideoStorage(namespace, id).clear();
    });

    describe('initialize', function() {
        it('with namespace and id', function() {
            var storage = VideoStorage(namespace, id);

            expect(window[namespace]).toBeDefined();
            expect(window[namespace][id]).toBeDefined();
        });

        it('without namespace and id', function() {
            spyOn(Number.prototype, 'toString').and.returnValue('0.abcdedg');
            var storage = VideoStorage();

            expect(window.VideoStorage).toBeDefined();
            expect(window.VideoStorage.abcdedg).toBeDefined();
        });
    });

    describe('methods: ', function() {
        var data, storage;

        beforeEach(function() {
            data = {
                item_2: 'value_2'
            };
            data[id] = {
                item_1: 'value_1'
            };

            window[namespace] = data;
            storage = VideoStorage(namespace, id);
        });

        it('setItem', function() {
            var expected = $.extend(true, {}, data, {item_4: 'value_4'});

            expected[id].item_3 = 'value_3';
            storage.setItem('item_3', 'value_3', true);
            storage.setItem('item_4', 'value_4');
            expect(window[namespace]).toEqual(expected);
        });

        it('getItem', function() {
            // eslint-disable-next-line no-shadow
            var data = window[namespace],
                getItem = storage.getItem;

            expect(getItem('item_1', true)).toBe(data[id].item_1);
            expect(getItem('item_2')).toBe(data.item_2);
            expect(getItem('item_3')).toBeUndefined();
        });

        it('removeItem', function() {
            // eslint-disable-next-line no-shadow
            var data = window[namespace],
                removeItem = storage.removeItem;

            removeItem('item_1', true);
            removeItem('item_2');
            expect(data[id].item_1).toBeUndefined();
            expect(data.item_2).toBeUndefined();
        });

        it('clear', function() {
            var expected = {};

            expected[id] = {};
            storage.clear();
            expect(window[namespace]).toEqual(expected);
        });
    });
});
