/* globals
    DiscussionCourseSettings, DiscussionSpecHelper, DiscussionThreadEditView, DiscussionUtil,
    DiscussionViewSpecHelper, Thread
*/
(function() {
    'use strict';

    describe('DiscussionThreadEditView', function() {
        var testUpdate, testCancel;

        beforeEach(function() {
            DiscussionSpecHelper.setUpGlobals();
            DiscussionSpecHelper.setUnderscoreFixtures();
            spyOn(DiscussionUtil, 'makeWmdEditor');
            this.threadData = DiscussionViewSpecHelper.makeThreadWithProps({
                commentable_id: 'test_topic',
                title: 'test thread title'
            });
            this.thread = new Thread(this.threadData);
            this.course_settings = DiscussionSpecHelper.createTestCourseSettings();

            this.createEditView = function(options) {
                options = _.extend({
                    container: $('#fixture-element'),
                    model: this.thread,
                    mode: 'tab',
                    course_settings: this.course_settings
                }, options);
                this.view = new DiscussionThreadEditView(options);
                this.view.render();
            };
        });

        testUpdate = function(view, thread, newTopicId, newTopicName, mode) {
            var discussionMode = mode || 'tab';

            spyOn($, 'ajax').and.callFake(function(params) {
                expect(params.url.path()).toEqual(DiscussionUtil.urlFor('update_thread', 'dummy_id'));
                expect(params.data.thread_type).toBe('discussion');
                if (discussionMode !== 'inline') {
                    expect(params.data.commentable_id).toBe(newTopicId);
                }
                expect(params.data.title).toBe('changed thread title');
                params.success();
                return {
                    always: function() {
                    }
                };
            });
            view.$el.find('.topic-title').filter(function(idx, el) {
                return $(el).data('discussionId') === newTopicId;
            }).prop('selected', true); // set new topic
            view.$('.post-topic').trigger('change');
            view.$('.edit-post-title').val('changed thread title'); // set new title
            if (discussionMode !== 'inline') {
                view.$("label[for$='post-type-discussion']").click(); // set new thread type
            }
            view.$('.post-update').click();
            expect($.ajax).toHaveBeenCalled();

            expect(thread.get('title')).toBe('changed thread title');
            expect(thread.get('thread_type')).toBe('discussion');
            if (discussionMode !== 'inline') {
                expect(thread.get('commentable_id')).toBe(newTopicId);
                expect(thread.get('courseware_title')).toBe(newTopicName);
            }
            expect(view.$('.edit-post-title')).toHaveValue('');
            expect(view.$('.wmd-preview p')).toHaveText('');
        };

        it('can save new data correctly in tab mode', function() {
            this.createEditView();
            testUpdate(this.view, this.thread, 'other_topic', 'Other Topic');
        });

        it('can save new data correctly in inline mode', function() {
            this.createEditView({mode: 'inline'});
            testUpdate(this.view, this.thread, 'other_topic', 'Other Topic', 'inline');
        });

        testCancel = function(view) {
            view.$('.post-cancel').click();
            expect($('.edit-post-form')).not.toExist();
        };

        it('can close the view in tab mode', function() {
            this.createEditView();
            testCancel(this.view);
        });

        it('can close the view in inline mode', function() {
            this.createEditView({mode: 'inline'});
            testCancel(this.view);
        });

        describe('Enter key behavior in title input', function() {
            beforeEach(function() {
                this.createEditView();
                this.titleInput = this.view.$('.edit-post-title');
            });

            it('prevents form submission when Enter is pressed in title input', function() {
                var submitSpy = jasmine.createSpy('submitSpy');
                this.view.$el.on('submit', submitSpy);
                
                var enterKeyEvent = $.Event('keypress', {which: 13, keyCode: 13});
                this.titleInput.trigger(enterKeyEvent);
                expect(submitSpy).not.toHaveBeenCalled();
            });

            it('prevents default behavior when Enter is pressed in title input', function() {
                var enterKeyEvent = $.Event('keypress', {which: 13, keyCode: 13});
                var preventDefaultSpy = spyOn(enterKeyEvent, 'preventDefault');
                
                this.titleInput.trigger(enterKeyEvent);
                expect(preventDefaultSpy).toHaveBeenCalled();
            });

            it('does not prevent non-Enter key presses in title input', function() {
                var submitSpy = jasmine.createSpy('submitSpy');
                this.view.$el.on('submit', submitSpy);
                
                var aKeyEvent = $.Event('keypress', {which: 65, keyCode: 65});
                var preventDefaultSpy = spyOn(aKeyEvent, 'preventDefault');
                this.titleInput.trigger(aKeyEvent);
                expect(preventDefaultSpy).not.toHaveBeenCalled();
            });

            it('does not interfere with body editor when Enter is pressed', function() {
                var bodyEditor = this.view.$('.edit-post-body textarea');
                var enterKeyEvent = $.Event('keypress', {which: 13, keyCode: 13});
                var preventDefaultSpy = spyOn(enterKeyEvent, 'preventDefault');
                
                bodyEditor.trigger(enterKeyEvent);                
                expect(preventDefaultSpy).not.toHaveBeenCalled();
            });
        });

        describe('renderComments', function() {
            beforeEach(function() {
                this.course_settings = new DiscussionCourseSettings({
                    category_map: {
                        children: [
                            ['Topic', 'entry'],
                            ['General', 'entry'],
                            ['Basic Question', 'entry']
                        ],
                        entries: {
                            Topic: {
                                is_divided: true,
                                id: 'topic'
                            },
                            General: {
                                sort_key: 'General',
                                is_divided: false,
                                id: '6.00.1x_General'
                            },
                            'Basic Question': {
                                is_divided: false,
                                id: "6>00'1x\"Basic_Question"
                            }
                        }
                    },
                    is_cohorted: true
                });
            });

            it('can save new data correctly for current discussion id without dots', function() {
                this.createEditView({topicId: 'topic'});
                testUpdate(this.view, this.thread, '6.00.1x_General', 'General');
            });

            it('can save new data correctly for current discussion id with dots', function() {
                this.createEditView({topicId: '6.00.1x_General'});
                testUpdate(this.view, this.thread, "6>00\'1x\"Basic_Question", 'Basic Question');
            });

            it('can save new data correctly for current discussion id with special characters', function() {
                this.createEditView({topicId: "6>00\'1x\"Basic_Question"});
                testUpdate(this.view, this.thread, '6.00.1x_General', 'General');
            });
        });
    });
}).call(this);
