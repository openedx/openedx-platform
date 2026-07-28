define([
    'jquery', 'underscore', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'js/spec/edxnotes/helpers',
    'annotator_1.2.9', 'logger', 'js/edxnotes/views/notes_factory'
], function($, _, sinon, AjaxHelpers, Helpers, Annotator, Logger, NotesFactory) {
    'use strict';

    describe('EdxNotes Events Plugin', function() {
        var note = {
                user: 'user-123',
                id: 'note-123',
                text: 'text-123',
                quote: 'quote-123',
                usage_id: 'usage-123',
                tags: ['tag1', 'tag2']
            },
            noteWithoutId = {
                user: 'user-123',
                text: 'text-123',
                quote: 'quote-123',
                usage_id: 'usage-123',
                tags: ['tag1', 'tag2']
            };
        var requests, xhrFactory;

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            this.annotator = NotesFactory.factory(
                $('<div />').get(0), {
                    endpoint: 'http://example.com/',
                    eventStringLimit: 300
                }
            );
            spyOn(Logger, 'log');
        });

        afterEach(function() {
            while (Annotator._instances.length > 0) {
                Annotator._instances[0].destroy();
            }
            requests.restore();
        });

        it('should log edx.course.student_notes.viewed event properly', function() {
            this.annotator.publish('annotationViewerShown', [
                this.annotator.viewer,
                [note, {user: 'user-456'}, {user: 'user-789', id: 'note-789'}]
            ]);
            expect(Logger.log).toHaveBeenCalledWith(
                'edx.course.student_notes.viewed', {
                    notes: [{note_id: 'note-123'}, {note_id: 'note-789'}]
                }
            );
        });

        it('should not log edx.course.student_notes.viewed event if all notes are new', function() {
            this.annotator.publish('annotationViewerShown', [
                this.annotator.viewer, [{user: 'user-456'}, {user: 'user-789'}]
            ]);
            expect(Logger.log).not.toHaveBeenCalled();
        });

        it('should log edx.course.student_notes.added event properly', function() {
            var newNote = {
                    user: 'user-123',
                    text: 'text-123',
                    quote: 'quote-123',
                    usage_id: 'usage-123'
                };

            this.annotator.publish('annotationCreated', newNote);
            AjaxHelpers.respondWithJson(requests, note);
            expect(Logger.log).toHaveBeenCalledWith(
                'edx.course.student_notes.added', {
                    note_id: 'note-123',
                    note_text: 'text-123',
                    tags: ['tag1', 'tag2'],
                    highlighted_content: 'quote-123',
                    truncated: [],
                    component_usage_id: 'usage-123'
                }
            );
        });

        it('should log the edx.course.student_notes.edited event properly', function() {
            var oldNote = note,
                newNote = $.extend({}, note, {text: 'text-456', tags: []});

            this.annotator.publish('annotationEditorShown', [this.annotator.editor, oldNote]);
            expect(this.annotator.plugins.Events.oldNoteText).toBe('text-123');
            this.annotator.publish('annotationUpdated', newNote);
            this.annotator.publish('annotationEditorHidden', [this.annotator.editor, newNote]);

            expect(Logger.log).toHaveBeenCalledWith(
                'edx.course.student_notes.edited', {
                    note_id: 'note-123',
                    old_note_text: 'text-123',
                    note_text: 'text-456',
                    old_tags: ['tag1', 'tag2'],
                    tags: [],
                    highlighted_content: 'quote-123',
                    truncated: [],
                    component_usage_id: 'usage-123'
                }
            );
            expect(this.annotator.plugins.Events.oldNoteText).toBeNull();
        });

        it('should not log the edx.course.student_notes.edited event if the note is new', function() {
            var oldNote = noteWithoutId,
                newNote = $.extend({}, noteWithoutId, {text: 'text-456'});

            this.annotator.publish('annotationEditorShown', [this.annotator.editor, oldNote]);
            expect(this.annotator.plugins.Events.oldNoteText).toBe('text-123');
            this.annotator.publish('annotationUpdated', newNote);
            this.annotator.publish('annotationEditorHidden', [this.annotator.editor, newNote]);
            expect(Logger.log).not.toHaveBeenCalled();
            expect(this.annotator.plugins.Events.oldNoteText).toBeNull();
        });

        it('should log the edx.course.student_notes.deleted event properly', function() {
            this.annotator.publish('annotationDeleted', note);
            expect(Logger.log).toHaveBeenCalledWith(
                'edx.course.student_notes.deleted', {
                    note_id: 'note-123',
                    note_text: 'text-123',
                    tags: ['tag1', 'tag2'],
                    highlighted_content: 'quote-123',
                    truncated: [],
                    component_usage_id: 'usage-123'
                }
            );
        });

        it('should not log the edx.course.student_notes.deleted event if the note is new', function() {
            this.annotator.publish('annotationDeleted', noteWithoutId);
            expect(Logger.log).not.toHaveBeenCalled();
        });

        it('should truncate values of some fields', function() {
            var oldNote = $.extend({}, note, {text: Helpers.LONG_TEXT, tags: ['review', Helpers.LONG_TEXT]}),
                newNote = $.extend({}, note, {
                    text: Helpers.LONG_TEXT + '123',
                    quote: Helpers.LONG_TEXT + '123',
                    tags: ['short', 'tags', 'will', 'stay', Helpers.LONG_TEXT]
                });

            this.annotator.publish('annotationEditorShown', [this.annotator.editor, oldNote]);
            expect(this.annotator.plugins.Events.oldNoteText).toBe(Helpers.LONG_TEXT);
            this.annotator.publish('annotationUpdated', newNote);
            this.annotator.publish('annotationEditorHidden', [this.annotator.editor, newNote]);

            expect(Logger.log).toHaveBeenCalledWith(
                'edx.course.student_notes.edited', {
                    note_id: 'note-123',
                    old_note_text: Helpers.TRUNCATED_TEXT,
                    old_tags: ['review'],
                    tags: ['short', 'tags', 'will', 'stay'],
                    note_text: Helpers.TRUNCATED_TEXT,
                    highlighted_content: Helpers.TRUNCATED_TEXT,
                    truncated: ['note_text', 'highlighted_content', 'tags', 'old_note_text', 'old_tags'],
                    component_usage_id: 'usage-123'
                }
            );
            expect(this.annotator.plugins.Events.oldNoteText).toBeNull();
        });
    });
});
