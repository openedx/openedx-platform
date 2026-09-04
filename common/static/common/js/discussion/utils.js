/* globals $$course_id, Content, Markdown, MathJax, URI, _ */
(function() {
    'use strict';

    this.DiscussionUtil = (function() {
        function DiscussionUtil() {
        }

        DiscussionUtil.wmdEditors = {};

        DiscussionUtil.leftKey = 37;
        DiscussionUtil.rightKey = 39;

        DiscussionUtil.getTemplate = function(id) {
            return $('script#' + id).html();
        };

        DiscussionUtil.setUser = function(user) {
            this.user = user;
        };

        DiscussionUtil.getUser = function() {
            return this.user;
        };

        DiscussionUtil.loadRoles = function(roles) {
            this.roleIds = roles;
        };

        DiscussionUtil.isStaff = function(userId) {
            var staff;
            if (_.isUndefined(userId)) {
                // eslint-disable-next-line no-void
                userId = this.user ? this.user.id : void 0;
            }
            if (_.isUndefined(this.roleIds)) {
                this.roleIds = {};
            }
            staff = _.union(this.roleIds.Moderator, this.roleIds.Administrator);
            return _.include(staff, parseInt(userId));
        };

        DiscussionUtil.isTA = function(userId) {
            var ta;
            if (_.isUndefined(userId)) {
                // eslint-disable-next-line no-void
                userId = this.user ? this.user.id : void 0;
            }
            ta = _.union(this.roleIds['Community TA']);
            return _.include(ta, parseInt(userId));
        };

        DiscussionUtil.isGroupTA = function(userId) {
            var groupTa,
                localUserId = userId;
            if (_.isUndefined(userId)) {
                // eslint-disable-next-line no-void
                localUserId = this.user ? this.user.id : void 0;
            }
            groupTa = _.union(this.roleIds['Group Moderator']);
            return _.include(groupTa, parseInt(localUserId, 10));
        };

        DiscussionUtil.isPrivilegedUser = function(userId) {
            return this.isStaff(userId) || this.isTA(userId);
        };

        DiscussionUtil.bulkUpdateContentInfo = function(infos) {
            var id, info, _results;
            _results = [];
            for (id in infos) {
                if (infos.hasOwnProperty(id)) {
                    info = infos[id];
                    _results.push(Content.getContent(id).updateInfo(info));
                }
            }
            return _results;
        };

        DiscussionUtil.generateDiscussionLink = function(cls, txt, handler) {
            return $('<a>')
                .addClass('discussion-link').attr('href', '#')
                .addClass(cls)
                .text(txt)
                .click(function() { return handler(this); });
        };

        DiscussionUtil.urlFor = function(name, param, param1, param2) {
            return {
                follow_discussion: '/courses/' + $$course_id + '/discussion/' + param + '/follow',
                unfollow_discussion: '/courses/' + $$course_id + '/discussion/' + param + '/unfollow',
                create_thread: '/courses/' + $$course_id + '/discussion/' + param + '/threads/create',
                update_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/update',
                create_comment: '/courses/' + $$course_id + '/discussion/threads/' + param + '/reply',
                delete_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/delete',
                flagAbuse_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/flagAbuse',
                unFlagAbuse_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/unFlagAbuse',
                flagAbuse_comment: '/courses/' + $$course_id + '/discussion/comments/' + param + '/flagAbuse',
                unFlagAbuse_comment: '/courses/' + $$course_id + '/discussion/comments/' + param + '/unFlagAbuse',
                upvote_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/upvote',
                downvote_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/downvote',
                pin_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/pin',
                un_pin_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/unpin',
                undo_vote_for_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/unvote',
                follow_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/follow',
                unfollow_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/unfollow',
                update_comment: '/courses/' + $$course_id + '/discussion/comments/' + param + '/update',
                endorse_comment: '/courses/' + $$course_id + '/discussion/comments/' + param + '/endorse',
                create_sub_comment: '/courses/' + $$course_id + '/discussion/comments/' + param + '/reply',
                delete_comment: '/courses/' + $$course_id + '/discussion/comments/' + param + '/delete',
                upvote_comment: '/courses/' + $$course_id + '/discussion/comments/' + param + '/upvote',
                downvote_comment: '/courses/' + $$course_id + '/discussion/comments/' + param + '/downvote',
                undo_vote_for_comment: '/courses/' + $$course_id + '/discussion/comments/' + param + '/unvote',
                upload: '/courses/' + $$course_id + '/discussion/upload',
                users: '/courses/' + $$course_id + '/discussion/users',
                search: '/courses/' + $$course_id + '/discussion/forum/search',
                retrieve_discussion: '/courses/' + $$course_id + '/discussion/forum/' + param + '/inline',
                retrieve_single_thread: '/courses/' + $$course_id + '/discussion/forum/' + param + '/threads/' + param1,
                openclose_thread: '/courses/' + $$course_id + '/discussion/threads/' + param + '/close',
                user_profile: '/courses/' + $$course_id + '/discussion/forum/users/' + param,
                followed_threads: '/courses/' + $$course_id + '/discussion/forum/users/' + param + '/followed',
                threads: '/courses/' + $$course_id + '/discussion/forum',
                enable_notifications: '/notification_prefs/enable/',
                disable_notifications: '/notification_prefs/disable/',
                notifications_status: '/notification_prefs/status/'
            }[name];
        };

        DiscussionUtil.ignoreEnterKey = function(event) {
            if (event.which === 13) {
                return event.preventDefault();
            }
        };

        DiscussionUtil.activateOnSpace = function(event, func) {
            if (event.which === 32) {
                event.preventDefault();
                return func(event);
            }
        };

        DiscussionUtil.makeFocusTrap = function(elem) {
            return elem.keydown(function(event) {
                if (event.which === 9) {
                    return event.preventDefault();
                }
            });
        };

        DiscussionUtil.showLoadingIndicator = function(element, takeFocus) {
            var animElem = edx.HtmlUtils.joinHtml(
                edx.HtmlUtils.HTML("<div class='loading-animation' tabindex='0'>"),
                edx.HtmlUtils.HTML("<span class='icon fa fa-spinner' aria-hidden='true'></span><span class='sr'>"),
                gettext('Loading content'),
                edx.HtmlUtils.HTML('</span></div>')
            );
            var $animElem = $(animElem.toString());
            element.after($animElem);
            this.$_loading = $animElem;
            if (takeFocus) {
                this.makeFocusTrap(this.$_loading);
                this.$_loading.focus();
            }
        };

        DiscussionUtil.hideLoadingIndicator = function() {
            return this.$_loading.remove();
        };

        DiscussionUtil.discussionAlert = function(header, body) {
            var $alertDiv, $alertTrigger;
            // Prevents "text" is undefined in underscore.js in tests - looks like some tests use
            // discussions somehow, but never append discussion fixtures or reset them; this causes
            // entire test suite (lms, cms, common) to fail due to unhandled JS exception
            var popupTemplate = $('#alert-popup').html() || '';
            if ($('#discussion-alert').length === 0) {
                $alertDiv = $(
                    edx.HtmlUtils.template(popupTemplate)({}).toString()
                );
                this.makeFocusTrap($alertDiv.find('button'));
                $alertTrigger = $("<a href='#discussion-alert' id='discussion-alert-trigger'/>").css('display', 'none');
                $alertTrigger.leanModal({
                    closeButton: '#discussion-alert .dismiss',
                    overlay: 1,
                    top: 200
                });
                $('body').append($alertDiv).append($alertTrigger);
            }
            $('#discussion-alert header h2').text(header);
            $('#discussion-alert p').text(body);
            $('#discussion-alert-trigger').click();
            $('#discussion-alert button').focus();
        };

        DiscussionUtil.safeAjax = function(params) {
            var $elem, deferred, request,
                self = this;
            $elem = params.$elem;
            if ($elem && $elem.prop('disabled')) {
                deferred = $.Deferred();
                deferred.reject();
                return deferred.promise();
            }
            params.url = URI(params.url).addSearch({
                ajax: 1
            });
            if (!params.error) {
                params.error = function() {
                    self.discussionAlert(
                        gettext('Error'),
                        gettext('Your request could not be processed. Refresh the page and try again.')
                    );
                };
            }

            if ($elem) {
                $elem.prop('disabled', true);
            }
            if (params.$loading) {
                if (params.loadingCallback) {
                    params.loadingCallback.apply(params.$loading);
                } else {
                    self.showLoadingIndicator(params.$loading, params.takeFocus);
                }
            }

            request = $.ajax(params).always(function() {
                if ($elem) {
                    $elem.prop('disabled', false);
                }
                if (params.$loading) {
                    if (params.loadedCallback) {
                        return params.loadedCallback.apply(params.$loading);
                    } else {
                        return self.hideLoadingIndicator();
                    }
                }
            });
            return request;
        };

        DiscussionUtil.updateWithUndo = function(model, updates, safeAjaxParams, errorMsg, beforeSend) {
            var undo,
                self = this;
            if (errorMsg) {
                safeAjaxParams.error = function() {
                    return self.discussionAlert(gettext('Error'), errorMsg);
                };
            }
            undo = _.pick(model.attributes, _.keys(updates));
            model.set(updates);
            if (typeof beforeSend === 'function') {
                beforeSend();
            }
            return this.safeAjax(safeAjaxParams).fail(function() {
                return model.set(undo);
            });
        };

        DiscussionUtil.bindLocalEvents = function($local, eventsHandler) {
            var event, eventSelector, handler, selector, _ref, _results;
            _results = [];
            for (eventSelector in eventsHandler) {
                if (eventsHandler.hasOwnProperty(eventSelector)) {
                    handler = eventsHandler[eventSelector];
                    _ref = eventSelector.split(' ');
                    event = _ref[0];
                    selector = _ref[1];
                    _results.push($local(selector).unbind(event)[event](handler));
                }
            }
            return _results;
        };

        DiscussionUtil.formErrorHandler = function(errorsField) {
            return function(xhr) {
                var makeErrorElem, response, i, $errorItem;
                makeErrorElem = function(message, alertId) {
                    return edx.HtmlUtils.joinHtml(
                        edx.HtmlUtils.HTML('<li>'),
                        edx.HtmlUtils.template(
                            $('#new-post-alert-template').html()
                        )({
                            message: message,
                            alertId: alertId
                        }),
                        edx.HtmlUtils.HTML('</li>')
                    );
                };
                errorsField.empty().show();
                if (xhr.status === 400) {
                    response = JSON.parse(xhr.responseText);
                    if (response.errors) {
                        for (i = 0; i < response.errors.length; i++) {
                            $errorItem = makeErrorElem(response.errors[i], i);
                            edx.HtmlUtils.append(errorsField, $errorItem);
                        }
                    }
                } else {
                    $errorItem = makeErrorElem('Your request could not be processed. Refresh the page and try again.', 0); // eslint-disable-line max-len
                    edx.HtmlUtils.append(errorsField, $errorItem);
                }

                // Set focus on the first error displayed
                $('div[role="alert"]', errorsField).first().focus();
            };
        };

        DiscussionUtil.clearFormErrors = function(errorsField) {
            return errorsField.empty();
        };

        DiscussionUtil.postMathJaxProcessor = function(htmlSnippet) {
            var RE_DISPLAYMATH, RE_INLINEMATH;
            RE_INLINEMATH = /^\$([^\$]*)\$/g;
            RE_DISPLAYMATH = /^\$\$([^\$]*)\$\$/g;
            return this.processEachMathAndCode(htmlSnippet, function(s, type) {
                if (type === 'display') {
                    return s.replace(RE_DISPLAYMATH, function($0, $1) {
                        return '\\[' + $1 + '\\]';
                    });
                } else if (type === 'inline') {
                    return s.replace(RE_INLINEMATH, function($0, $1) {
                        return '\\(' + $1 + '\\)';
                    });
                } else {
                    return s;
                }
            });
        };

        DiscussionUtil.makeWmdEditor = function($content, $local, cls_identifier) {
            var appended_id, editor, elem, id, imageUploadUrl, placeholder, _processor;
            elem = $local('.' + cls_identifier);
            placeholder = elem.data('placeholder');
            id = elem.data('id');
            appended_id = '-' + cls_identifier + '-' + id;
            imageUploadUrl = this.urlFor('upload');
            _processor = function(self) {
                return function(text) {
                    // HTML returned by Markdown is assumed to be safe to render
                    return self.postMathJaxProcessor(edx.HtmlUtils.HTML(text)).toString();
                };
            };
            editor = Markdown.makeWmdEditor(elem, appended_id, imageUploadUrl, _processor(this));
            this.wmdEditors['' + cls_identifier + '-' + id] = editor;
            if (placeholder) {
                elem.find('#wmd-input' + appended_id).attr('placeholder', placeholder);
            }
            return editor;
        };

        DiscussionUtil.getWmdEditor = function($content, $local, cls_identifier) {
            var elem, id;
            elem = $local('.' + cls_identifier);
            id = elem.attr('data-id');
            return this.wmdEditors['' + cls_identifier + '-' + id];
        };

        DiscussionUtil.getWmdInput = function($content, $local, cls_identifier) {
            var elem, id;
            elem = $local('.' + cls_identifier);
            id = elem.attr('data-id');
            return $local('#wmd-input-' + cls_identifier + '-' + id);
        };

        DiscussionUtil.getWmdContent = function($content, $local, cls_identifier) {
            return this.getWmdInput($content, $local, cls_identifier).val();
        };

        DiscussionUtil.setWmdContent = function($content, $local, cls_identifier, text) {
            this.getWmdInput($content, $local, cls_identifier).val(text);
            return this.getWmdEditor($content, $local, cls_identifier).refreshPreview();
        };

        var RE_DISPLAYMATH = /^([^\$]*?)\$\$([^\$]*?)\$\$(.*)$/m,
            RE_INLINEMATH = /^([^\$]*?)\$([^\$]+?)\$(.*)$/m,
            ESCAPED_DOLLAR = '@@ESCAPED_D@@',
            ESCAPED_BACKSLASH = '@@ESCAPED_B@@',
            LATEX_SCRIPT = '\{javascript\:(.+?)\}';

        /**
         * Formats math and code chunks
         * @param htmlSnippet - post contents in form of safe (escaped and/or stripped) HTML
         * @param processor - callback to post-process math and code chunks. Should return HtmlUtils.HTML or "subclass"
         * @returns {*}
         */
        DiscussionUtil.processEachMathAndCode = function(htmlSnippet, processor) {
            var $div, codeArchive, processedHtmlString, htmlString;
            codeArchive = {};
            processedHtmlString = '';
            $div = edx.HtmlUtils.setHtml($('<div>'), edx.HtmlUtils.ensureHtml(htmlSnippet));
            $div.find('code').each(function(index, code) {
                codeArchive[index] = $(code).html();
                return $(code).text(index);
            });
            htmlString = $div.html();
            htmlString = htmlString.replace(/\\\$/g, ESCAPED_DOLLAR);
            /* eslint-disable no-loop-func */
            // eslint-disable-next-line no-constant-condition
            while (true) {
                if (RE_INLINEMATH.test(htmlString)) {
                    htmlString = htmlString.replace(RE_INLINEMATH, function($0, $1, $2, $3) {
                        processedHtmlString += $1 + processor('$' + $2 + '$', 'inline');
                        return $3;
                    });
                } else if (RE_DISPLAYMATH.test(htmlString)) {
                    htmlString = htmlString.replace(RE_DISPLAYMATH, function($0, $1, $2, $3) {
                        /*
                         corrected mathjax rendering in preview
                         */
                        processedHtmlString += $1 + processor('$$' + $2 + '$$', 'display');
                        return $3;
                    });
                } else {
                    processedHtmlString += htmlString;
                    break;
                }
            }
            /* eslint-enable no-loop-func */
            htmlString = processedHtmlString;
            htmlString = htmlString.replace(new RegExp(ESCAPED_DOLLAR, 'g'), '\\$');
            htmlString = htmlString.replace(/\\\\\\\\/g, ESCAPED_BACKSLASH);
            htmlString = htmlString.replace(/\\begin\{([a-z]*\*?)\}([\s\S]*?)\\end\{\1\}/img, function($0, $1, $2) {
                return processor(('\\begin{' + $1 + '}') + $2 + ('\\end{' + $1 + '}'));
            });
            htmlString = htmlString.replace(new RegExp(ESCAPED_BACKSLASH, 'g'), '\\\\\\\\');
            htmlString = htmlString.replace(new RegExp(LATEX_SCRIPT, 'g'), '{}');
            $div = edx.HtmlUtils.setHtml($('<div>'), edx.HtmlUtils.HTML(htmlString));
            $div.find('code').each(function(index, code) {
                edx.HtmlUtils.setHtml(
                    $(code),
                    edx.HtmlUtils.HTML(processor(codeArchive[index], 'code'))
                );
            });
            return edx.HtmlUtils.HTML($div.html());
        };

        DiscussionUtil.unescapeHighlightTag = function(htmlSnippet) {
            return edx.HtmlUtils.HTML(
                htmlSnippet.toString().replace(
                    /\&lt\;highlight\&gt\;/g,
                    "<span class='search-highlight'>").replace(/\&lt\;\/highlight\&gt\;/g, '</span>'
                )
            );
        };

        DiscussionUtil.stripHighlight = function(htmlString) {
            return htmlString
                .replace(/\&(amp\;)?lt\;highlight\&(amp\;)?gt\;/g, '')
                .replace(/\&(amp\;)?lt\;\/highlight\&(amp\;)?gt\;/g, '');
        };

        DiscussionUtil.stripLatexHighlight = function(htmlSnippet) {
            return this.processEachMathAndCode(htmlSnippet, this.stripHighlight);
        };

        /**
         * Processes markdown into formatted text and handles highlighting.
         * @param unsafeText - raw markdown text, with all HTML entitites being *unescaped*.
         * @returns HtmlSnippet
         */
        DiscussionUtil.markdownWithHighlight = function(unsafeText) {
            var converter;
            unsafeText = unsafeText.replace(/^\&gt\;/gm, '>');
            converter = Markdown.getMathCompatibleConverter();
            /*
            * converter.makeHtml and HTML escaping:
            * - converter.makeHtml is not HtmlSnippet aware, so we must pass unescaped raw text
            * - converter.makeHtml strips html tags in post body and escapes in code blocks by design.
            *    HTML tags are not supported.  Only markdown is supported.
            */
            var htmlSnippet = edx.HtmlUtils.HTML(converter.makeHtml(unsafeText));
            return this.unescapeHighlightTag(this.stripLatexHighlight(htmlSnippet));
        };

        DiscussionUtil.abbreviateString = function(text, minLength) {
            if (text.length < minLength) {
                return text;
            } else {
                while (minLength < text.length && text[minLength] !== ' ') {
                    minLength++;
                }
                return text.substr(0, minLength) + gettext('…');
            }
        };

        DiscussionUtil.convertMath = function(element) {
            edx.HtmlUtils.setHtml(
                element,
                this.postMathJaxProcessor(this.markdownWithHighlight(element.text()))
            );
        };

        DiscussionUtil.typesetMathJax = function(element) {
            if (typeof MathJax !== 'undefined' && MathJax !== null && typeof MathJax.startup !== 'undefined') {
                MathJax.startup.promise
                    .then(() => MathJax.typesetPromise([element[0]]));
            }
        };

        DiscussionUtil.abbreviateHTML = function(htmlSnippet, maxLength) {
            var $result, imagesToReplace, truncated_text;
            truncated_text = edx.HtmlUtils.HTML(jQuery.truncate(htmlSnippet.toString(), {
                length: maxLength,
                noBreaks: true,
                ellipsis: gettext('…')
            }));
            $result = $(edx.HtmlUtils.joinHtml(
                edx.HtmlUtils.HTML('<div>'),
                truncated_text,
                edx.HtmlUtils.HTML('</div>')
            ).toString());
            imagesToReplace = $result.find('img:not(:first)');
            if (imagesToReplace.length > 0) {
                edx.HtmlUtils.append(
                    $result,
                    edx.HtmlUtils.interpolateHtml(
                        edx.HtmlUtils.HTML('<p><em>{text}</em></p>'),
                        {text: gettext('Some images in this post have been omitted')}
                    )
                );
            }
            // See TNL-4983 for an explanation of why the linter requires ensureHtml()
            var afterMessage = edx.HtmlUtils.interpolateHtml(
                edx.HtmlUtils.HTML('<em>{text}</em>'), {text: gettext('image omitted')}
            );
            imagesToReplace.after(edx.HtmlUtils.ensureHtml(afterMessage).toString()).remove();
            return $result.html();
        };

        DiscussionUtil.getPaginationParams = function(curPage, numPages, pageUrlFunc) {
            var delta, maxPage, minPage, pageInfo;
            delta = 2;
            minPage = Math.max(curPage - delta, 1);
            maxPage = Math.min(curPage + delta, numPages);
            pageInfo = function(pageNum) {
                return {
                    number: pageNum,
                    url: pageUrlFunc(pageNum)
                };
            };
            return {
                page: curPage,
                lowPages: _.range(minPage, curPage).map(pageInfo),
                highPages: _.range(curPage + 1, maxPage + 1).map(pageInfo),
                previous: curPage > 1 ? pageInfo(curPage - 1) : null,
                next: curPage < numPages ? pageInfo(curPage + 1) : null,
                leftdots: minPage > 2,
                rightdots: maxPage < numPages - 1,
                first: minPage > 1 ? pageInfo(1) : null,
                last: maxPage < numPages ? pageInfo(numPages) : null
            };
        };

        DiscussionUtil.handleKeypressInToolbar = function(event) {
            var $currentButton, $nextButton, $toolbar, $allButtons,
                keyPressed, nextIndex, currentButtonIndex,
                validKeyPress, toolbarHasButtons;

            $currentButton = $(event.target);
            keyPressed = event.which || event.keyCode;
            $toolbar = $currentButton.parent();
            $allButtons = $toolbar.children('.wmd-button');

            validKeyPress = keyPressed === this.leftKey || keyPressed === this.rightKey;
            toolbarHasButtons = $allButtons.length > 0;

            if (validKeyPress && toolbarHasButtons) {
                currentButtonIndex = $allButtons.index($currentButton);
                nextIndex = keyPressed === this.leftKey ? currentButtonIndex - 1 : currentButtonIndex + 1;
                nextIndex = Math.max(Math.min(nextIndex, $allButtons.length - 1), 0);

                $nextButton = $($allButtons[nextIndex]);
                this.moveSelectionToNextItem($currentButton, $nextButton);
            }
        };

        DiscussionUtil.moveSelectionToNextItem = function(prevItem, nextItem) {
            prevItem.attr('aria-selected', 'false');
            prevItem.attr('tabindex', '-1');

            nextItem.attr('aria-selected', 'true');
            nextItem.attr('tabindex', '0');
            nextItem.focus();
        };

        return DiscussionUtil;
    }).call(this);
}).call(window);
