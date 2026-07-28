/**
 * This is a base modal implementation that provides common utilities.
 *
 * A modal implementation should override the following methods:
 *
 *   getTitle():
 *     returns the title for the modal.
 *   getContentHtml():
 *     returns the HTML content to be shown inside the modal.
 *
 * A modal implementation should also provide the following options:
 *
 *   modalName: A string identifying the modal.
 *   modalType: A string identifying the type of the modal.
 *   modalSize: A string, either 'sm', 'med', or 'lg' indicating the
 *     size of the modal.
 *   viewSpecificClasses: A string of CSS classes to be attached to
 *     the modal window.
 *   addPrimaryActionButton: A boolean indicating whether to include a primary action
 *     button on the modal.
 *   primaryActionButtonType: A string to be used as type for primary action button.
 *   primaryActionButtonTitle: A string to be used as title for primary action button.
 *   showEditorModeButtons: Whether to show editor mode button in the modal header.
 *   showFullscreenButton: Whether to show fullscreen button in the modal header.
 */
define(['jquery', 'underscore', 'gettext', 'js/views/baseview'],
    function($, _, gettext, BaseView) {
        var BaseModal = BaseView.extend({
            events: {
                'click .action-cancel': 'cancel'
            },

            options: _.extend({}, BaseView.prototype.options, {
                type: 'prompt',
                closeIcon: false,
                icon: false,
                modalName: 'basic',
                modalType: 'generic',
                modalSize: 'lg',
                title: '',
                modalWindowClass: '.modal-window',
                // A list of class names, separated by space.
                viewSpecificClasses: '',
                addPrimaryActionButton: false,
                primaryActionButtonType: 'save',
                primaryActionButtonTitle: gettext('Save'),
                showEditorModeButtons: true,
                showFullscreenButton: false,
            }),

            initialize: function() {
                var parent = this.options.parent,
                    parentElement = this.options.parentElement;
                this.modalTemplate = this.loadTemplate('basic-modal');
                this.buttonTemplate = this.loadTemplate('modal-button');
                if (parent) {
                    parentElement = parent.$el;
                } else if (!parentElement) {
                    parentElement = this.$el.closest(this.options.modalWindowClass);
                    if (parentElement.length === 0) {
                        parentElement = $('body');
                    }
                }
                this.parentElement = parentElement;
            },

            render: function() {
                // xss-lint: disable=javascript-jquery-html
                this.$el.html(this.modalTemplate({
                    name: this.options.modalName,
                    type: this.options.modalType,
                    size: this.options.modalSize,
                    title: this.getTitle(),
                    modalSRTitle: this.options.modalSRTitle,
                    showEditorModeButtons: this.options.showEditorModeButtons,
                    viewSpecificClasses: this.options.viewSpecificClasses,
                    showFullscreenButton: this.options.showFullscreenButton,
                }));
                this.addActionButtons();
                this.renderContents();
                this.parentElement.append(this.$el);
            },

            getTitle: function() {
                return this.options.title;
            },

            renderContents: function() {
                const contentHtml = this.getContentHtml();
                // xss-lint: disable=javascript-jquery-html
                this.$('.modal-content').html(contentHtml);

                const fullscreenButton =  this.$('.fullscreen-button');
                if (fullscreenButton.length) {
                    fullscreenButton.bind('click', this.toggleFullscreen.bind(this));
                }
            },

            /**
             * Returns the content to be shown in the modal.
             */
            getContentHtml: function() {
                return '';
            },

            show: function(focusModal) {
                var focusModalWindow = focusModal === undefined;
                this.render();
                this.resize();
                $(window).resize(_.bind(this.resize, this));

                // child may want to have its own focus management
                if (focusModalWindow) {
                    // after showing and resizing, send focus
                    this.$el.find(this.options.modalWindowClass).focus();
                }

            },

            hide: function() {
                try {
                    window.parent.postMessage(
                        {
                            type: 'hideXBlockEditorModal',
                            message: 'Sends a message when the modal window is hided',
                            payload: {}
                        }, document.referrer
                    );
                } catch (e) {
                    console.error(e);
                }
                const fullscreenButton =  this.$('.fullscreen-button');
                if (fullscreenButton.length) {
                    fullscreenButton.unbind('click');
                }

                // Completely remove the modal from the DOM
                this.undelegateEvents();
                this.$el.html('');
            },

            cancel: function(event) {
                if (event) {
                    event.preventDefault();
                    event.stopPropagation(); // Make sure parent modals don't see the click
                }
                try {
                    window.parent.postMessage({
                        type: 'closeXBlockEditorModal',
                        message: 'Sends a message when the modal window is closed',
                        payload: {}
                    }, document.referrer);
                } catch (e) {
                    console.error(e);
                }
                this.hide();
            },

            /**
             * Adds the action buttons to the modal.
             */
            addActionButtons: function() {
                if (this.options.addPrimaryActionButton) {
                    this.addActionButton(
                        this.options.primaryActionButtonType,
                        this.options.primaryActionButtonTitle,
                        true
                    );
                }
                this.addActionButton('cancel', gettext('Cancel'));
            },

            /**
             * Adds a new action button to the modal.
             * @param type The type of the action.
             * @param name The action's name.
             * @param isPrimary True if this button is the primary one.
             */
            addActionButton: function(type, name, isPrimary) {
                var html = this.buttonTemplate({
                    type: type,
                    name: name,
                    isPrimary: isPrimary
                });
                // xss-lint: disable=javascript-jquery-append
                this.getActionBar().find('ul').append(html);
            },

            /**
             * Returns the action bar that contains the modal's action buttons.
             */
            getActionBar: function() {
                return this.$(this.options.modalWindowClass + ' > div > .modal-actions');
            },

            /**
             * Returns the action button of the specified type.
             */
            getActionButton: function(type) {
                return this.getActionBar().find('.action-' + type);
            },

            enableActionButton: function(type) {
                this.getActionBar().find('.action-' + type).prop('disabled', false).removeClass('is-disabled');
            },

            disableActionButton: function(type) {
                this.getActionBar().find('.action-' + type).prop('disabled', true).addClass('is-disabled');
            },

            toggleFullscreen: function() {
                this.$('.fullscreen-button .icon').toggleClass('fa-expand fa-compress');
                this.$('.modal-editor').toggleClass(`modal-${this.options.modalSize} modal-fullscreen`);
                this.resize();
            },

            resize: function() {
                var top, left, modalWindow, modalWidth, modalHeight,
                    availableWidth, availableHeight, maxWidth, maxHeight;

                modalWindow = this.$el.find(this.options.modalWindowClass);
                if (modalWindow.hasClass('modal-fullscreen')) {
                    // Remove previously set width and height from the modal window
                    modalWindow.css({
                        top: '',
                        left: '',
                    });
                    return;
                }
                availableWidth = $(window).width();
                availableHeight = $(window).height();
                maxWidth = availableWidth * 0.98;
                maxHeight = availableHeight * 0.98;
                modalWidth = Math.min(modalWindow.outerWidth(), maxWidth);
                modalHeight = Math.min(modalWindow.outerHeight(), maxHeight);

                left = (availableWidth - modalWidth) / 2;
                top = (availableHeight - modalHeight) / 2;

                modalWindow.css({
                    top: top + $(window).scrollTop(),
                    left: left + $(window).scrollLeft()
                });
            }
        });

        return BaseModal;
    });
