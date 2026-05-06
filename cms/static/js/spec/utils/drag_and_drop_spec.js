define(['sinon', 'js/utils/drag_and_drop', 'common/js/components/views/feedback_notification',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'jquery', 'underscore'],
function(sinon, ContentDragger, Notification, AjaxHelpers, $, _) {
    'use strict';

    describe('Overview drag and drop functionality', function() {
        beforeEach(function() {
            setFixtures(readFixtures('mock/mock-outline.underscore'));
            _.each(
                $('.unit'),
                function(element) {
                    ContentDragger.makeDraggable(element, {
                        type: '.unit',
                        handleClass: '.unit-drag-handle',
                        droppableClass: 'ol.sortable-unit-list',
                        parentLocationSelector: 'li.courseware-subsection',
                        refresh: jasmine.createSpy('Spy on Unit'),
                        ensureChildrenRendered: jasmine.createSpy('Spy on Unit')
                    });
                }
            );
            _.each(
                $('.courseware-subsection'),
                function(element) {
                    ContentDragger.makeDraggable(element, {
                        type: '.courseware-subsection',
                        handleClass: '.subsection-drag-handle',
                        droppableClass: '.sortable-subsection-list',
                        parentLocationSelector: 'section',
                        refresh: jasmine.createSpy('Spy on Subsection'),
                        ensureChildrenRendered: jasmine.createSpy('Spy on Subsection')
                    });
                }
            );
        });

        describe('findDestination', function() {
            it('correctly finds the drop target of a drag', function() {
                var $ele, destination;
                $ele = $('#unit-1');
                $ele.offset({
                    top: $ele.offset().top + 10,
                    left: $ele.offset().left
                });
                destination = ContentDragger.findDestination($ele, 1);
                expect(destination.ele).toEqual($('#unit-2'));
                expect(destination.attachMethod).toBe('before');
            });
            it('can drag and drop across section boundaries, with special handling for single sibling', function() {
                var $ele, $unit0, $unit4, destination;
                $ele = $('#unit-1');
                $unit4 = $('#unit-4');
                $ele.offset({
                    top: $unit4.offset().top + 8,
                    left: $ele.offset().left
                });
                destination = ContentDragger.findDestination($ele, 1);
                expect(destination.ele).toEqual($unit4);
                expect(destination.attachMethod).toBe('after');
                destination = ContentDragger.findDestination($ele, -1);
                expect(destination.ele).toEqual($unit4);
                expect(destination.attachMethod).toBe('before');
                $ele.offset({
                    top: $unit4.offset().top + $unit4.height() + 1,
                    left: $ele.offset().left
                });
                destination = ContentDragger.findDestination($ele, 0);
                expect(destination.ele).toEqual($unit4);
                expect(destination.attachMethod).toBe('after');
                $unit0 = $('#unit-0');
                $ele.offset({
                    top: $unit0.offset().top - 16,
                    left: $ele.offset().left
                });
                destination = ContentDragger.findDestination($ele, 0);
                expect(destination.ele).toEqual($unit0);
                expect(destination.attachMethod).toBe('before');
            });
            it('can drop before the first element, even if element being dragged is\nslightly before the first element', function() {
                var $ele, destination;
                $ele = $('#subsection-2');
                $ele.offset({
                    top: $('#subsection-0').offset().top - 5,
                    left: $ele.offset().left
                });
                destination = ContentDragger.findDestination($ele, -1);
                expect(destination.ele).toEqual($('#subsection-0'));
                expect(destination.attachMethod).toBe('before');
            });
            it('can drag and drop across section boundaries, with special handling for last element', function() {
                var $ele, destination;
                $ele = $('#unit-4');
                $ele.offset({
                    top: $('#unit-3').offset().bottom + 4,
                    left: $ele.offset().left
                });
                destination = ContentDragger.findDestination($ele, -1);
                expect(destination.ele).toEqual($('#unit-3'));
                expect(destination.attachMethod).toBe('after');
                $ele.offset({
                    top: $('#unit-3').offset().top + 4,
                    left: $ele.offset().left
                });
                destination = ContentDragger.findDestination($ele, -1);
                expect(destination.ele).toEqual($('#unit-3'));
                expect(destination.attachMethod).toBe('before');
            });
            it('can drop past the last element, even if element being dragged is\nslightly before/taller then the last element', function() {
                var $ele, destination;
                $ele = $('#subsection-2');
                $ele.offset({
                    top: $('#subsection-4').offset().top - 1,
                    left: $ele.offset().left
                });
                destination = ContentDragger.findDestination($ele, 1);
                expect(destination.ele).toEqual($('#subsection-4'));
                expect(destination.attachMethod).toBe('after');
            });
            it('can drag into an empty list', function() {
                var $ele, destination;
                $ele = $('#unit-1');
                $ele.offset({
                    top: $('#subsection-3').offset().top + 10,
                    left: $ele.offset().left
                });
                destination = ContentDragger.findDestination($ele, 1);
                expect(destination.ele).toEqual($('#subsection-list-3'));
                expect(destination.attachMethod).toBe('prepend');
            });
            it('reports a null destination on a failed drag', function() {
                var $ele, destination;
                $ele = $('#unit-1');
                $ele.offset({
                    top: $ele.offset().top + 200,
                    left: $ele.offset().left
                });
                destination = ContentDragger.findDestination($ele, 1);
                expect(destination).toEqual({
                    ele: null,
                    attachMethod: ''
                });
            });
            it('can drag into a collapsed list', function() {
                var $ele, destination;
                $('#subsection-2').addClass('is-collapsed');
                $ele = $('#unit-2');
                $ele.offset({
                    top: $('#subsection-2').offset().top + 3,
                    left: $ele.offset().left
                });
                destination = ContentDragger.findDestination($ele, 1);
                expect(destination.ele).toEqual($('#subsection-list-2'));
                expect(destination.parentList).toEqual($('#subsection-2'));
                expect(destination.attachMethod).toBe('prepend');
            });
        });
        describe('onDragStart', function() {
            it('sets the dragState to its default values', function() {
                expect(ContentDragger.dragState).toEqual({});
                ContentDragger.onDragStart({
                    element: $('#unit-1')
                }, null, null);
                expect(ContentDragger.dragState).toEqual({
                    dropDestination: null,
                    attachMethod: '',
                    parentList: null,
                    lastY: 0,
                    dragDirection: 0
                });
            });
            it('collapses expanded elements', function() {
                expect($('#subsection-1')).not.toHaveClass('is-collapsed');
                ContentDragger.onDragStart({
                    element: $('#subsection-1')
                }, null, null);
                expect($('#subsection-1')).toHaveClass('is-collapsed');
                expect($('#subsection-1')).toHaveClass('expand-on-drop');
            });
        });
        describe('onDragMove', function() {
            beforeEach(function() {
                this.redirectSpy = spyOn(window, 'scrollBy').and.callThrough();
            });
            it('adds the correct CSS class to the drop destination', function() {
                var $ele, dragX, dragY;
                $ele = $('#unit-1');
                dragY = $ele.offset().top + 10;
                dragX = $ele.offset().left;
                $ele.offset({
                    top: dragY,
                    left: dragX
                });
                ContentDragger.onDragMove({
                    element: $ele,
                    dragPoint: {
                        y: dragY
                    }
                }, '', {
                    clientX: dragX
                });
                expect($('#unit-2')).toHaveClass('drop-target drop-target-before');
                expect($ele).toHaveClass('valid-drop');
            });
            it('does not add CSS class to the drop destination if out of bounds', function() {
                var $ele, dragY;
                $ele = $('#unit-1');
                dragY = $ele.offset().top + 10;
                $ele.offset({
                    top: dragY,
                    left: $ele.offset().left
                });
                ContentDragger.onDragMove({
                    element: $ele,
                    dragPoint: {
                        y: dragY
                    }
                }, '', {
                    clientX: $ele.offset().left - 3
                });
                expect($('#unit-2')).not.toHaveClass('drop-target drop-target-before');
                expect($ele).not.toHaveClass('valid-drop');
            });
            it('scrolls up if necessary', function() {
                ContentDragger.onDragMove({
                    element: $('#unit-1')
                }, '', {
                    clientY: 2
                });
                expect(this.redirectSpy).toHaveBeenCalledWith(0, -10);
            });
            it('scrolls down if necessary', function() {
                ContentDragger.onDragMove({
                    element: $('#unit-1')
                }, '', {
                    clientY: window.innerHeight - 5
                });
                expect(this.redirectSpy).toHaveBeenCalledWith(0, 10);
            });
        });
        describe('onDragEnd', function() {
            beforeEach(function() {
                this.reorderSpy = spyOn(ContentDragger, 'handleReorder');
            });
            afterEach(function() {
                this.reorderSpy.calls.reset();
            });
            it('calls handleReorder on a successful drag', function() {
                ContentDragger.dragState.dropDestination = $('#unit-2');
                ContentDragger.dragState.attachMethod = 'after';
                ContentDragger.dragState.parentList = $('#subsection-1');
                $('#unit-1').offset({
                    top: $('#unit-1').offset().top + 10,
                    left: $('#unit-1').offset().left
                });
                ContentDragger.onDragEnd({
                    element: $('#unit-1')
                }, null, {
                    clientX: $('#unit-1').offset().left
                });
                expect(this.reorderSpy).toHaveBeenCalled();
            });
            it('clears out the drag state', function() {
                ContentDragger.onDragEnd({
                    element: $('#unit-1')
                }, null, null);
                expect(ContentDragger.dragState).toEqual({});
            });
            it('sets the element to the correct position', function() {
                ContentDragger.onDragEnd({
                    element: $('#unit-1')
                }, null, null);
                expect(['0px', 'auto']).toContain($('#unit-1').css('top'));
                expect(['0px', 'auto']).toContain($('#unit-1').css('left'));
            });
            it('expands an element if it was collapsed on drag start', function() {
                $('#subsection-1').addClass('is-collapsed');
                $('#subsection-1').addClass('expand-on-drop');
                ContentDragger.onDragEnd({
                    element: $('#subsection-1')
                }, null, null);
                expect($('#subsection-1')).not.toHaveClass('is-collapsed');
                expect($('#subsection-1')).not.toHaveClass('expand-on-drop');
            });
            it('expands a collapsed element when something is dropped in it', function() {
                var expandElementSpy = spyOn(ContentDragger, 'expandElement').and.callThrough();
                expect(expandElementSpy).not.toHaveBeenCalled();
                expect($('#subsection-2').data('ensureChildrenRendered')).not.toHaveBeenCalled();

                $('#subsection-2').addClass('is-collapsed');
                ContentDragger.dragState.dropDestination = $('#list-2');
                ContentDragger.dragState.attachMethod = 'prepend';
                ContentDragger.dragState.parentList = $('#subsection-2');
                ContentDragger.onDragEnd({
                    element: $('#unit-1')
                }, null, {
                    clientX: $('#unit-1').offset().left
                });

                // verify collapsed element expands while ensuring its children are properly rendered
                expect(expandElementSpy).toHaveBeenCalled();
                expect($('#subsection-2').data('ensureChildrenRendered')).toHaveBeenCalled();
                expect($('#subsection-2')).not.toHaveClass('is-collapsed');
            });
        });
        describe('AJAX', function() {
            var requests, xhrFactory;
            beforeEach(function() {
                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };
                this.savingSpies = jasmine.stealth.spyOnConstructor(Notification, 'Mini', ['show', 'hide']);
                this.savingSpies.show.and.returnValue(this.savingSpies);
                this.clock = sinon.useFakeTimers();
            });
            afterEach(function() {
                requests.restore();
                this.clock.restore();
                jasmine.stealth.clearSpies();
            });
            it('should send an update on reorder from one parent to another', function() {
                var request, savingOptions;
                ContentDragger.dragState.dropDestination = $('#unit-4');
                ContentDragger.dragState.attachMethod = 'after';
                ContentDragger.dragState.parentList = $('#subsection-2');
                $('#unit-1').offset({
                    top: $('#unit-4').offset().top + 10,
                    left: $('#unit-4').offset().left
                });
                ContentDragger.onDragEnd({
                    element: $('#unit-1')
                }, null, {
                    clientX: $('#unit-1').offset().left
                });
                request = AjaxHelpers.currentRequest(requests);
                expect(this.savingSpies.constructor).toHaveBeenCalled();
                expect(this.savingSpies.show).toHaveBeenCalled();
                expect(this.savingSpies.hide).not.toHaveBeenCalled();
                savingOptions = this.savingSpies.constructor.calls.mostRecent().args[0];
                expect(savingOptions.title).toMatch(/Saving/);
                expect($('#unit-1')).toHaveClass('was-dropped');
                expect(request.requestBody).toEqual('{"children":["fourth-unit-id","first-unit-id"]}');
                request.respond(204);
                expect(this.savingSpies.hide).toHaveBeenCalled();
                this.clock.tick(1001);
                expect($('#unit-1')).not.toHaveClass('was-dropped');
                // source
                expect($('#subsection-1').data('refresh')).toHaveBeenCalled();
                // target
                expect($('#subsection-2').data('refresh')).toHaveBeenCalled();
            });
            it('should send an update on reorder within the same parent', function() {
                var request;
                ContentDragger.dragState.dropDestination = $('#unit-2');
                ContentDragger.dragState.attachMethod = 'after';
                ContentDragger.dragState.parentList = $('#subsection-1');
                $('#unit-1').offset({
                    top: $('#unit-1').offset().top + 10,
                    left: $('#unit-1').offset().left
                });
                ContentDragger.onDragEnd({
                    element: $('#unit-1')
                }, null, {
                    clientX: $('#unit-1').offset().left
                });
                request = AjaxHelpers.currentRequest(requests);
                expect($('#unit-1')).toHaveClass('was-dropped');
                expect(request.requestBody).toEqual(
                    '{"children":["second-unit-id","first-unit-id","third-unit-id"]}'
                );
                request.respond(204);
                this.clock.tick(1001);
                expect($('#unit-1')).not.toHaveClass('was-dropped');
                // parent
                expect($('#subsection-1').data('refresh')).toHaveBeenCalled();
            });
        });
    });
});
