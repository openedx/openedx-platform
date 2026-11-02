'use strict';

import Iterator from './00_iterator.js';
import HtmlUtils from 'edx-ui-toolkit/js/utils/html-utils';

/**
 * Video speed control module.
 * @exports video/08_video_speed_control.js
 * @constructor
 * @param {object} state The object containing the state of the video player.
 * @return {jquery Promise}
 */
function VideoSpeedControl(state) {
    if (!(this instanceof VideoSpeedControl)) {
        return new VideoSpeedControl(state);
    }

    _.bindAll(this, 'onSetSpeed', 'onRenderSpeed', 'clickLinkHandler',
        'keyDownLinkHandler', 'mouseEnterHandler', 'mouseLeaveHandler',
        'clickMenuHandler', 'keyDownMenuHandler', 'destroy'
    );
    this.state = state;
    this.state.videoSpeedControl = this;
    this.initialize();

    return $.Deferred().resolve().promise();
};

VideoSpeedControl.prototype = {
    template: [
        '<div class="speeds menu-container" role="application">',
        '<p class="sr instructions">',
        gettext('Press UP to enter the speed menu then use the UP and DOWN arrow keys to navigate the different speeds, then press ENTER to change to the selected speed.'), // eslint-disable-line max-len, indent
        '</p>',
        '<button class="control speed-button" aria-disabled="false" aria-expanded="false"',
        'title="',
        gettext('Adjust video speed'),
        '">',
        '<span>',
        '<span class="icon fa fa-caret-right" aria-hidden="true"></span>',
        '</span>',
        '<span class="label">',
        gettext('Speed'),
        ' </span>',
        '<span class="value"></span>',
        '</button>',
        '<ol class="video-speeds menu"></ol>',
        '</div>'
    ].join(''),

    destroy: function () {
        this.el.off({
            mouseenter: this.mouseEnterHandler,
            mouseleave: this.mouseLeaveHandler,
            click: this.clickMenuHandler,
            keydown: this.keyDownMenuHandler
        });

        this.state.el.off({
            'speed:set': this.onSetSpeed,
            'speed:render': this.onRenderSpeed
        });
        this.closeMenu(true);
        this.speedsContainer.remove();
        this.el.remove();
        delete this.state.videoSpeedControl;
    },

    /** Initializes the module. */
    initialize: function () {
        let state = this.state;

        if (!this.isPlaybackRatesSupported(state)) {
            console.log(
                '[Video info]: playbackRate is not supported.'
            );

            return false;
        }
        this.el = $(this.template);
        this.speedsContainer = this.el.find('.video-speeds');
        this.speedButton = this.el.find('.speed-button');
        this.render(state.speeds, state.speed);
        this.setSpeed(state.speed, true, true);
        this.bindHandlers();

        return true;
    },

    /**
     * Creates any necessary DOM elements, attach them, and set their,
     * initial configuration.
     * @param {array} speeds List of speeds available for the player.
     * @param {string} currentSpeed The current speed set to the player.
     */
    render: function (speeds, currentSpeed) {
        let speedsContainer = this.speedsContainer,
            reversedSpeeds = speeds.concat().reverse(),
            instructionsId = 'speed-instructions-' + this.state.id,
            speedsList = $.map(reversedSpeeds, function (speed) {
                return HtmlUtils.interpolateHtml(
                    HtmlUtils.HTML(
                        [
                            '<li data-speed="{speed}">',
                            '<button class="control speed-option" tabindex="-1" aria-pressed="false">',
                            '{speed}x',
                            '</button>',
                            '</li>'
                        ].join('')
                    ),
                    {
                        speed: speed
                    }
                ).toString();
            });

        HtmlUtils.setHtml(
            speedsContainer,
            HtmlUtils.HTML(speedsList)
        );
        this.speedLinks = new Iterator(speedsContainer.find('.speed-option'));
        HtmlUtils.prepend(
            this.state.el.find('.secondary-controls'),
            HtmlUtils.HTML(this.el)
        );
        this.setActiveSpeed(currentSpeed);

        // set dynamic id for instruction element to avoid collisions
        this.el.find('.instructions').attr('id', instructionsId);
        this.speedButton.attr('aria-describedby', instructionsId);
    },

    /**
     * Bind any necessary function callbacks to DOM events (click,
     * mousemove, etc.).
     */
    bindHandlers: function () {
        // Attach various events handlers to the speed menu button.
        this.el.on({
            mouseenter: this.mouseEnterHandler,
            mouseleave: this.mouseLeaveHandler,
            click: this.openMenu,
            keydown: this.keyDownMenuHandler
        });

        // Attach click and keydown event handlers to the individual speed
        // entries.
        this.speedsContainer.on({
            click: this.clickLinkHandler,
            keydown: this.keyDownLinkHandler
        }, '.speed-option');

        this.state.el.on({
            'speed:set': this.onSetSpeed,
            'speed:render': this.onRenderSpeed
        });
        this.state.el.on('destroy', this.destroy);
    },

    onSetSpeed: function (event, speed) {
        this.setSpeed(speed, true);
    },

    onRenderSpeed: function (event, speeds, currentSpeed) {
        this.render(speeds, currentSpeed);
    },

    /**
     * Check if playbackRate supports by browser. If browser supports, 1.0
     * should be returned by playbackRate property. In this case, function
     * return True. Otherwise, False will be returned.
     * iOS doesn't support speed change.
     * @param {object} state The object containing the state of the video
     * player.
     * @return {boolean}
     *   true: Browser support playbackRate functionality.
     *   false: Browser doesn't support playbackRate functionality.
     */
    isPlaybackRatesSupported: function (state) {
        let isHtml5 = state.videoType === 'html5',
            isTouch = state.isTouch,
            video = document.createElement('video');

        // eslint-disable-next-line no-extra-boolean-cast
        return !isTouch || (isHtml5 && !Boolean(video.playbackRate));
    },

    /**
     * Opens speed menu.
     * @param {boolean} [bindEvent] Click event will be attached on window.
     */
    openMenu: function (bindEvent) {
        // When speed entries have focus, the menu stays open on
        // mouseleave. A clickHandler is added to the window
        // element to have clicks close the menu when they happen
        // outside of it.
        if (bindEvent) {
            $(window).on('click.speedMenu', this.clickMenuHandler);
        }

        this.el.addClass('is-opened');
        this.speedButton
            .attr('tabindex', -1)
            .attr('aria-expanded', 'true');
    },

    /**
     * Closes speed menu.
     * @param {boolean} [unBindEvent] Click event will be detached from window.
     */
    closeMenu: function (unBindEvent) {
        // Remove the previously added clickHandler from window element.
        if (unBindEvent) {
            $(window).off('click.speedMenu');
        }

        this.el.removeClass('is-opened');
        this.speedButton
            .attr('tabindex', 0)
            .attr('aria-expanded', 'false');
    },

    /**
     * Sets new current speed for the speed control and triggers `speedchange`
     * event if needed.
     * @param {string|number} speed Speed to be set.
     * @param {boolean} [silent] Sets the new speed without triggering
     * `speedchange` event.
     * @param {boolean} [forceUpdate] Updates the speed even if it's
     * not differs from current speed.
     */
    setSpeed: function (speed, silent, forceUpdate) {
        let newSpeed = this.state.speedToString(speed);
        if (newSpeed !== this.currentSpeed || forceUpdate) {
            this.speedsContainer
                .find('li')
                .siblings("li[data-speed='" + newSpeed + "']");

            this.speedButton.find('.value').text(newSpeed + 'x');
            this.currentSpeed = newSpeed;

            if (!silent) {
                this.el.trigger('speedchange', [newSpeed, this.state.speed]);
            }
        }

        this.resetActiveSpeed();
        this.setActiveSpeed(newSpeed);
    },

    resetActiveSpeed: function () {
        let speedOptions = this.speedsContainer.find('li');

        $(speedOptions).each(function (index, el) {
            $(el).removeClass('is-active')
                .find('.speed-option')
                .attr('aria-pressed', 'false');
        });
    },

    setActiveSpeed: function (speed) {
        let speedOption = this.speedsContainer.find('li[data-speed="' + this.state.speedToString(speed) + '"]');

        speedOption.addClass('is-active')
            .find('.speed-option')
            .attr('aria-pressed', 'true');

        this.speedButton.attr('title', gettext('Video speed: ') + this.state.speedToString(speed) + 'x');
    },

    /**
     * Click event handler for the menu.
     * @param {jquery Event} event
     */
    clickMenuHandler: function () {
        this.closeMenu();

        return false;
    },

    /**
     * Click event handler for speed links.
     * @param {jquery Event} event
     */
    clickLinkHandler: function (event) {
        let el = $(event.currentTarget).parent(),
            speed = $(el).data('speed');

        this.resetActiveSpeed();
        this.setActiveSpeed(speed);
        this.state.videoCommands.execute('speed', speed);
        this.closeMenu(true);

        return false;
    },

    /**
     * Mouseenter event handler for the menu.
     * @param {jquery Event} event
     */
    mouseEnterHandler: function () {
        this.openMenu();

        return false;
    },

    /**
     * Mouseleave event handler for the menu.
     * @param {jquery Event} event
     */
    mouseLeaveHandler: function () {
        // Only close the menu is no speed entry has focus.
        if (!this.speedLinks.list.is(':focus')) {
            this.closeMenu();
        }

        return false;
    },

    /**
     * Keydown event handler for the menu.
     * @param {jquery Event} event
     */
    keyDownMenuHandler: function (event) {
        let KEY = $.ui.keyCode,
            keyCode = event.keyCode;

        // eslint-disable-next-line default-case
        switch (keyCode) {
            // Open menu and focus on last element of list above it.
            case KEY.ENTER:
            case KEY.SPACE:
            case KEY.UP:
                this.openMenu(true);
                this.speedLinks.last().focus();
                break;
            // Close menu.
            case KEY.ESCAPE:
                this.closeMenu(true);
                break;
        }
        // We do not stop propagation and default behavior on a TAB
        // keypress.
        return event.keyCode === KEY.TAB;
    },

    /**
     * Keydown event handler for speed links.
     * @param {jquery Event} event
     */
    keyDownLinkHandler: function (event) {
        // ALT key is used to change (alternate) the function of
        // other pressed keys. In this, do nothing.
        if (event.altKey) {
            return true;
        }

        let KEY = $.ui.keyCode,
            self = this,
            parent = $(event.currentTarget).parent(),
            index = parent.index(),
            speed = parent.data('speed');

        // eslint-disable-next-line default-case
        switch (event.keyCode) {
            // Close menu.
            case KEY.TAB:
                // Closes menu after 25ms delay to change `tabindex` after
                // finishing default behavior.
                setTimeout(function () {
                    self.closeMenu(true);
                }, 25);

                return true;
            // Close menu and give focus to speed control.
            case KEY.ESCAPE:
                this.closeMenu(true);
                this.speedButton.focus();

                return false;
            // Scroll up menu, wrapping at the top. Keep menu open.
            case KEY.UP:
                // Shift + Arrows keyboard shortcut might be used by
                // screen readers. In this, do nothing.
                if (event.shiftKey) {
                    return true;
                }

                this.speedLinks.prev(index).focus();
                return false;
            // Scroll down  menu, wrapping at the bottom. Keep menu
            // open.
            case KEY.DOWN:
                // Shift + Arrows keyboard shortcut might be used by
                // screen readers. In this, do nothing.
                if (event.shiftKey) {
                    return true;
                }

                this.speedLinks.next(index).focus();
                return false;
            // Close menu, give focus to speed control and change
            // speed.
            case KEY.ENTER:
            case KEY.SPACE:
                this.closeMenu(true);
                this.speedButton.focus();
                this.setSpeed(this.state.speedToString(speed));

                return false;
        }

        return true;
    }
};

export default VideoSpeedControl;
