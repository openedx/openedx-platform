(function() {
    'use strict';

    var TooltipManager = function(element) {
        this.element = $(element);
        // If tooltip container already exist, use it.
        this.tooltip = $('div.' + this.className.split(/\s+/).join('.'));
        // Otherwise, create new one.
        if (!this.tooltip.length) {
            this.tooltip = $('<div />', {
                class: this.className
            }).appendTo(this.element); // xss-lint: disable=javascript-jquery-insert-into-target
        }

        this.hide();
        _.bindAll(this, 'show', 'hide', 'showTooltip', 'moveTooltip', 'hideTooltip', 'click');
        this.bindEvents();
    };

    TooltipManager.prototype = {
        // Space separated list of class names for the tooltip container.
        className: 'tooltip',
        SELECTOR: '[data-tooltip]',

        bindEvents: function() {
            this.element.on({
                'mouseover.TooltipManager': this.showTooltip,
                'mousemove.TooltipManager': this.moveTooltip,
                'mouseout.TooltipManager': this.hideTooltip,
                'click.TooltipManager': this.click
            }, this.SELECTOR);
        },

        getCoords: function(pageX, pageY) {
            let left = pageX - 0.5 * this.tooltip.outerWidth();
            const top = pageY - (this.tooltip.outerHeight() + 15);
            // Check if the tooltip is going off the right edge of the screen
            if (left + this.tooltip.outerWidth() > window.innerWidth) {
                left = window.innerWidth - this.tooltip.outerWidth();
            }
            // Check if the tooltip is going off the left edge of the screen
            if (left < 0) {
                left = 0;
            }

            return {
                left,
                top,
            };
        },

        show: function() {
            this.tooltip.show().css('opacity', 1);
        },

        hide: function() {
            this.tooltip.hide().css('opacity', 0);
        },

        showTooltip: function(event) {
            this.prepareTooltip(event.currentTarget, event.pageX, event.pageY);
            if (this.tooltipTimer) {
                clearTimeout(this.tooltipTimer);
            }
            this.tooltipTimer = setTimeout(this.show, 500);
        },

        prepareTooltip: function(element, pageX, pageY) {
            pageX = typeof pageX !== 'undefined' ? pageX : element.offset().left + element.width() / 2;
            pageY = typeof pageY !== 'undefined' ? pageY : element.offset().top + element.height() / 2;
            var tooltipText = $(element).attr('data-tooltip');
            // Tooltip content comes from data-tooltip attributes which are server-rendered
            // with proper escaping using Text() and HTML() from openedx.core.djangolib.markup
            this.tooltip
                .html(tooltipText)  // xss-lint: disable=javascript-jquery-html
                .css(this.getCoords(pageX, pageY));
        },

        // To manually trigger a tooltip to reveal, other than through user mouse movement:
        openTooltip: function(element) {
            this.prepareTooltip(element);
            this.show();
            if (this.tooltipTimer) {
                clearTimeout(this.tooltipTimer);
            }
        },

        moveTooltip: function(event) {
            this.tooltip.css(this.getCoords(event.pageX, event.pageY));
        },

        hideTooltip: function() {
            clearTimeout(this.tooltipTimer);
            // Wait for a 50ms before hiding the tooltip to avoid blinking when
            // the item contains nested elements.
            this.tooltipTimer = setTimeout(this.hide, 50);
        },

        click: function(event) {
            var showOnClick = !!$(event.currentTarget).data('tooltip-show-on-click'); // Default is false
            if (showOnClick) {
                this.show();
                if (this.tooltipTimer) {
                    clearTimeout(this.tooltipTimer);
                }
            } else {
                this.hideTooltip(event);
            }
        },

        destroy: function() {
            this.tooltip.remove();
            // Unbind all delegated event handlers in the ".TooltipManager"
            // namespace.
            this.element.off('.TooltipManager', this.SELECTOR);
        }
    };

    window.TooltipManager = TooltipManager;
    $(document).ready(function() {
        window.globalTooltipManager = new TooltipManager(document.body);
    });
}());
