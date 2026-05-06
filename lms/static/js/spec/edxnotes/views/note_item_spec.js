define([
    'jquery', 'underscore', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'common/js/spec_helpers/template_helpers', 'js/spec/edxnotes/helpers', 'logger',
    'js/edxnotes/models/note', 'js/edxnotes/views/note_item'
], function(
    $, _, sinon, AjaxHelpers, TemplateHelpers, Helpers, Logger, NoteModel, NoteItemView
) {
    'use strict';

    describe('EdxNotes NoteItemView', function() {
        var requests, xhrFactory;
        var getView = function(model, scrollToTag, formattedText) {
            model = new NoteModel(_.defaults(model || {}, {
                id: 'id-123',
                user: 'user-123',
                usage_id: 'usage_id-123',
                created: 'December 11, 2014 at 11:12AM',
                updated: 'December 11, 2014 at 11:12AM',
                text: formattedText || 'Third added model',
                quote: Helpers.LONG_TEXT,
                unit: {
                    url: 'http://example.com/'
                }
            }));

            return new NoteItemView({model: model, scrollToTag: scrollToTag, view: 'Test View'}).render();
        };

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            TemplateHelpers.installTemplate('templates/edxnotes/note-item');
            spyOn(Logger, 'log').and.callThrough();
        });

        afterEach(function() {
            requests.restore();
        });

        it('can be rendered properly', function() {
            var view = getView(),
                unitLink = view.$('.reference-unit-link').get(0);

            expect(view.$el).toContainElement('.note-excerpt-more-link');
            expect(view.$el).toContainText(Helpers.PRUNED_TEXT);
            expect(view.$el).toContainText('More');
            view.$('.note-excerpt-more-link').click();

            expect(view.$el).toContainText(Helpers.LONG_TEXT);
            expect(view.$el).toContainText('Less');

            view = getView({quote: Helpers.SHORT_TEXT});
            expect(view.$el).not.toContain('.note-excerpt-more-link');
            expect(view.$el).toContainText(Helpers.SHORT_TEXT);

            expect(unitLink.hash).toBe('#id-123');
        });

        it('should display update value and accompanying text', function() {
            var view = getView();
            expect(view.$('.reference-title')[1]).toContainText('Last Edited:');
            expect(view.$('.reference-updated-date').last()).toContainText('December 11, 2014 at 11:12AM');
        });

        it('should not display tags if there are none', function() {
            var view = getView();
            expect(view.$el).not.toContain('.reference-tags');
            expect(view.$('.reference-title').length).toBe(2);
        });

        it('should display tags if they exist', function() {
            var view = getView({tags: ['First', 'Second']});
            expect(view.$('.reference-title').length).toBe(3);
            expect(view.$('.reference-title')[2]).toContainText('Tags:');
            expect(view.$('span.reference-tags').length).toBe(2);
            expect(view.$('span.reference-tags')[0]).toContainText('First');
            expect(view.$('span.reference-tags')[1]).toContainText('Second');
        });

        it('should highlight tags & text if they have elasticsearch formatter', function() {
            var view = getView({
                tags: ['First', '{elasticsearch_highlight_start}Second{elasticsearch_highlight_end}']
            }, {}, '{elasticsearch_highlight_start}Sample{elasticsearch_highlight_end}');
            expect(view.$('.reference-title').length).toBe(3);
            expect(view.$('.reference-title')[2]).toContainText('Tags:');
            expect(view.$('span.reference-tags').length).toBe(2);
            expect(view.$('span.reference-tags')[0]).toContainText('First');
            // highlighted tag & text
            expect($.trim($(view.$('span.reference-tags')[1]).html())).toBe(
                '<span class="note-highlight">Second</span>'
            );
            expect($.trim(view.$('.note-comment-p').html())).toBe('<span class="note-highlight">Sample</span>');
        });

        it('should escape html for tags & comments', function() {
            var view = getView({
                tags: ['First', '<b>Second</b>', 'ȗnicode']
            }, {}, '<b>Sample</b>');
            expect(view.$('.reference-title').length).toBe(3);
            expect(view.$('.reference-title')[2]).toContainText('Tags:');
            expect(view.$('span.reference-tags').length).toBe(3);
            expect(view.$('span.reference-tags')[0]).toContainText('First');
            expect($.trim($(view.$('span.reference-tags')[1]).html())).toBe(
                '&lt;b&gt;Second&lt;/b&gt;'
            );
            expect($.trim($(view.$('span.reference-tags')[2]).html())).toBe('ȗnicode');
            expect($.trim(view.$('.note-comment-p').html())).toBe('&lt;b&gt;Sample&lt;/b&gt;');
        });

        xit('should handle a click event on the tag', function() {
            var scrollToTagSpy = {
                scrollToTag: function(tagName) {}
            };
            spyOn(scrollToTagSpy, 'scrollToTag');
            var view = getView({tags: ['only']}, scrollToTagSpy.scrollToTag);
            view.$('a.reference-tags').click();
            expect(scrollToTagSpy.scrollToTag).toHaveBeenCalledWith('only');
        });

        it('should log the edx.course.student_notes.used_unit_link event properly', function() {
            var view = getView();
            spyOn(view, 'redirectTo');
            view.$('.reference-unit-link').click();
            expect(Logger.log).toHaveBeenCalledWith(
                'edx.course.student_notes.used_unit_link',
                {
                    note_id: 'id-123',
                    component_usage_id: 'usage_id-123',
                    view: 'Test View'
                },
                null,
                {
                    timeout: 2000
                }
            );
            expect(view.redirectTo).not.toHaveBeenCalled();
            AjaxHelpers.respondWithJson(requests, {});
            expect(view.redirectTo).toHaveBeenCalledWith('http://example.com/#id-123');
        });
    });
});
