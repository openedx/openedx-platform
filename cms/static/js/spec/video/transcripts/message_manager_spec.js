define(
    [
        'jquery', 'underscore', 'backbone',
        'js/views/video/transcripts/utils', 'js/views/video/transcripts/message_manager',
        'js/views/video/transcripts/file_uploader', 'sinon',
        'xmodule', 'accessibility'
    ],
    function($, _, Backbone, Utils, MessageManager, FileUploader, sinon) {
        'use strict';

        describe('Transcripts.MessageManager', function() {
            var videoListEntryTemplate = readFixtures(
                    'video/transcripts/metadata-videolist-entry.underscore'
                ),
                foundTemplate = readFixtures(
                    'video/transcripts/messages/transcripts-found.underscore'
                ),
                handlers = {
                    importHandler: ['replace', 'Error: Import failed.'],
                    replaceHandler: ['replace', 'Error: Replacing failed.'],
                    chooseHandler: ['choose', 'Error: Choosing failed.', 'video_id']
                },
                view, fileUploader, sinonXhr;

            beforeEach(function() {
                var videoList, $container;

                fileUploader = FileUploader.prototype;

                setFixtures(
                    $('<div>', {id: 'metadata-videolist-entry'})
                        .html(videoListEntryTemplate)
                );
                appendSetFixtures(
                    $('<script>',
                        {
                            id: 'transcripts-found',
                            type: 'text/template'
                        }
                    ).text(foundTemplate)
                );

                videoList = jasmine.createSpyObj(
                    'MetadataView.VideoList',
                    ['getVideoObjectsList']
                );
                $container = $('#metadata-videolist-entry');

                spyOn(fileUploader, 'initialize').and.callThrough();
                spyOn(console, 'error');
                spyOn(Utils.Storage, 'set');

                view = new MessageManager({
                    el: $container,
                    parent: videoList,
                    component_locator: 'component_locator'
                });
            });

            it('Initialize', function() {
                expect(fileUploader.initialize).toHaveBeenCalledWith({
                    el: view.$el,
                    messenger: view,
                    component_locator: view.component_locator
                });
            });

            describe('Render', function() {
                beforeEach(function() {
                    spyOn(_, 'template').and.callThrough();
                    spyOn(view.fileUploader, 'render');
                });

                it('Template doesn\'t exist', function() {
                    view.render('incorrect_template_name');

                    expect(console.error).toHaveBeenCalled();
                    expect(_.template).not.toHaveBeenCalled();
                    expect(view.$el.find('.transcripts-status'))
                        .toHaveClass('is-invisible');
                    expect(view.fileUploader.render).not.toHaveBeenCalled();
                });

                it('All works okay if correct data is passed', function() {
                    view.render('found');

                    expect(console.error).not.toHaveBeenCalled();
                    expect(_.template).toHaveBeenCalled();
                    expect(view.$el).not.toHaveClass('is-invisible');
                    expect(view.fileUploader.render).toHaveBeenCalled();
                });
            });

            describe('showError', function() {
                var errorMessage = 'error',
                    $error, $buttons;

                beforeEach(function() {
                    view.render('found');
                    spyOn(view, 'hideError');
                    spyOn($.fn, 'html').and.callThrough();
                    $error = view.$el.find('.transcripts-error-message');
                    $buttons = view.$el.find('.wrapper-transcripts-buttons');
                });

                it('Error message is not passed', function() {
                    view.showError(null);

                    expect(view.hideError).not.toHaveBeenCalled();
                    expect($error.html).not.toHaveBeenCalled();
                    expect($error).toHaveClass('is-invisible');
                    expect($buttons).not.toHaveClass('is-invisible');
                });

                it('Show message and buttons', function() {
                    view.showError(errorMessage);

                    expect(view.hideError).toHaveBeenCalled();
                    expect($error.html).toHaveBeenCalled();
                    expect($error).not.toHaveClass('is-invisible');
                    expect($buttons).not.toHaveClass('is-invisible');
                });

                it('Show message and hide buttons', function() {
                    view.showError(errorMessage, true);

                    expect(view.hideError).toHaveBeenCalled();
                    expect($error.html).toHaveBeenCalled();
                    expect($error).not.toHaveClass('is-invisible');
                    expect($buttons).toHaveClass('is-invisible');
                });
            });

            it('hideError', function() {
                view.render('found');

                var $error = view.$el.find('.transcripts-error-message'),
                    $buttons = view.$el.find('.wrapper-transcripts-buttons');

                expect($error).toHaveClass('is-invisible');
                expect($buttons).not.toHaveClass('is-invisible');
            });

            $.each(handlers, function(key, value) {
                it(key, function() {
                    var eventObj = jasmine.createSpyObj('event', ['preventDefault']);
                    spyOn($.fn, 'data').and.returnValue('video_id');
                    spyOn(view, 'processCommand');
                    view[key](eventObj);
                    expect(view.processCommand.calls.mostRecent().args).toEqual(value);
                });
            });

            describe('processCommand', function() {
                var action = 'replace',
                    errorMessage = 'errorMessage',
                    // eslint-disable-next-line no-void
                    videoList = void 0,
                    extraParamas = 'video_id';

                beforeEach(function() {
                    view.render('found');
                    spyOn(Utils, 'command').and.callThrough();
                    spyOn(view, 'render');
                    spyOn(view, 'showError');

                    sinonXhr = sinon.fakeServer.create();
                    sinonXhr.autoRespond = true;
                });

                afterEach(function() {
                    sinonXhr.restore();
                });

                var assertCommand = function(config) {
                    var defaults = {
                        action: 'replace',
                        errorMessage: 'errorMessage',
                        // eslint-disable-next-line no-void
                        extraParamas: void 0
                    };
                    var args = $.extend({}, defaults, config);

                    return view
                        .processCommand(args.action, args.errorMessage, args.extraParamas);
                };

                it('Invoke without extraParamas', function(done) {
                    spyOn(Backbone, 'trigger');

                    sinonXhr.respondWith([
                        200,
                        {'Content-Type': 'application/json'},
                        JSON.stringify({
                            status: 'Success',
                            edx_video_id: 'video_id'
                        })
                    ]);

                    assertCommand({})
                        .then(function() {
                            expect(Utils.command).toHaveBeenCalledWith(
                                action,
                                view.component_locator,
                                videoList,
                                // eslint-disable-next-line no-void
                                void 0
                            );
                            expect(view.showError).not.toHaveBeenCalled();
                            expect(view.render.calls.mostRecent().args[0]).toEqual('found');
                            expect(Backbone.trigger)
                                .toHaveBeenCalledWith('transcripts:basicTabUpdateEdxVideoId', 'video_id');
                        })
                        .always(done);
                });

                it('Invoke with extraParamas', function(done) {
                    spyOn(Backbone, 'trigger');

                    sinonXhr.respondWith([
                        200,
                        {'Content-Type': 'application/json'},
                        JSON.stringify({
                            status: 'Success',
                            edx_video_id: 'video_id'
                        })
                    ]);

                    view.processCommand(action, errorMessage, extraParamas);

                    assertCommand({extraParamas: extraParamas})
                        .then(function() {
                            expect(Utils.command).toHaveBeenCalledWith(
                                action,
                                view.component_locator,
                                videoList,
                                {
                                    html5_id: extraParamas
                                }
                            );
                            expect(view.showError).not.toHaveBeenCalled();
                            expect(view.render.calls.mostRecent().args[0]).toEqual('found');
                            expect(Backbone.trigger)
                                .toHaveBeenCalledWith('transcripts:basicTabUpdateEdxVideoId', 'video_id');
                        })
                        .always(done);
                });

                it('Fail', function(done) {
                    sinonXhr.respondWith([400, {}, '']);
                    assertCommand({})
                        .then(function() {
                            expect(Utils.command).toHaveBeenCalledWith(
                                action,
                                view.component_locator,
                                videoList,
                                // eslint-disable-next-line no-void
                                void 0
                            );
                            expect(view.showError).toHaveBeenCalled();
                            expect(view.render).not.toHaveBeenCalled();
                            expect(Utils.Storage.set).not.toHaveBeenCalled();
                        })
                        .always(done);
                });
            });
        });
    });
