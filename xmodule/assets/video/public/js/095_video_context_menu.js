import _ from 'underscore';
import Component from './00_component.js';

let AbstractItem = Component.extend({
    initialize: function (options) {
        this.options = $.extend(true, {
            label: '',
            prefix: 'edx-',
            dataAttrs: {menu: this},
            attrs: {},
            items: [],
            callback: $.noop,
            initialize: $.noop
        }, options);

        this.id = _.uniqueId();
        this.element = this.createElement();
        this.element.attr(this.options.attrs).data(this.options.dataAttrs);
        this.children = [];
        this.delegateEvents();
        this.options.initialize.call(this, this);
    },
    destroy: function () {
        _.invoke(this.getChildren(), 'destroy');
        this.undelegateEvents();
        this.getElement().remove();
    },
    open: function () {
        this.getElement().addClass('is-opened');
        return this;
    },
    close: function () {
    },
    closeSiblings: function () {
        _.invoke(this.getSiblings(), 'close');
        return this;
    },
    getElement: function () {
        return this.element;
    },
    addChild: function (child) {
        let firstChild = null,
            lastChild = null;
        if (this.hasChildren()) {
            lastChild = this.getLastChild();
            lastChild.next = child;
            firstChild = this.getFirstChild();
            firstChild.prev = child;
        }
        child.parent = this;
        child.next = firstChild;
        child.prev = lastChild;
        this.children.push(child);
        return this;
    },
    getChildren: function () {
        // Returns the copy.
        return this.children.concat();
    },
    hasChildren: function () {
        return this.getChildren().length > 0;
    },
    getFirstChild: function () {
        return _.first(this.children);
    },
    getLastChild: function () {
        return _.last(this.children);
    },
    bindEvent: function (element, events, handler) {
        $(element).on(this.addNamespace(events), handler);
        return this;
    },
    getNext: function () {
        let item = this.next;
        while (item.isHidden() && this.id !== item.id) {
            item = item.next;
        }
        return item;
    },
    getPrev: function () {
        let item = this.prev;
        while (item.isHidden() && this.id !== item.id) {
            item = item.prev;
        }
        return item;
    },
    createElement: function () {
        return null;
    },
    getRoot: function () {
        let item = this;
        while (item.parent) {
            item = item.parent;
        }
        return item;
    },
    populateElement: function () {
    },
    focus: function () {
        this.getElement().focus();
        this.closeSiblings();
        return this;
    },
    isHidden: function () {
        return this.getElement().is(':hidden');
    },
    getSiblings: function () {
        let items = [],
            item = this;
        while (item.next && item.next.id !== this.id) {
            item = item.next;
            items.push(item);
        }
        return items;
    },
    select: function () {
    },
    unselect: function () {
    },
    setLabel: function () {
    },
    itemHandler: function () {
    },
    keyDownHandler: function () {
    },
    delegateEvents: function () {
    },
    undelegateEvents: function () {
        this.getElement().off('.' + this.id);
    },
    addNamespace: function (events) {
        return _.map(events.split(/\s+/), function (event) {
            return event + '.' + this.id;
        }, this).join(' ');
    }
});

let AbstractMenu = AbstractItem.extend({
    delegateEvents: function () {
        this.bindEvent(this.getElement(), 'keydown mouseleave mouseover', this.itemHandler.bind(this))
            .bindEvent(this.getElement(), 'contextmenu', function (event) {
                event.preventDefault();
            });
        return this;
    },

    populateElement: function () {
        let fragment = document.createDocumentFragment();

        _.each(this.getChildren(), function (child) {
            fragment.appendChild(child.populateElement()[0]);
        }, this);

        this.appendContent([fragment]);
        this.isRendered = true;
        return this.getElement();
    },

    close: function () {
        this.closeChildren();
        this.getElement().removeClass('is-opened');
        return this;
    },

    closeChildren: function () {
        _.invoke(this.getChildren(), 'close');
        return this;
    },

    itemHandler: function (event) {
        event.preventDefault();
        let item = $(event.target).data('menu');
        // eslint-disable-next-line default-case
        switch (event.type) {
            case 'keydown':
                this.keyDownHandler.call(this, event, item);
                break;
            case 'mouseover':
                this.mouseOverHandler.call(this, event, item);
                break;
            case 'mouseleave':
                this.mouseLeaveHandler.call(this, event, item);
                break;
        }
    },

    keyDownHandler: function () {
    },
    mouseOverHandler: function () {
    },
    mouseLeaveHandler: function () {
    }
});

let Menu = AbstractMenu.extend({
    initialize: function (options, contextmenuElement, container) {
        this.contextmenuElement = $(contextmenuElement);
        this.container = $(container);
        this.overlay = this.getOverlay();
        AbstractMenu.prototype.initialize.apply(this, arguments);
        this.build(this, this.options.items);
    },

    createElement: function () {
        return $('<ol />', {
            class: ['contextmenu', this.options.prefix + 'contextmenu'].join(' '),
            role: 'menu',
            tabindex: -1
        });
    },

    delegateEvents: function () {
        AbstractMenu.prototype.delegateEvents.call(this);
        this.bindEvent(this.contextmenuElement, 'contextmenu', this.contextmenuHandler.bind(this))
            .bindEvent(window, 'resize', _.debounce(this.close.bind(this), 100));
        return this;
    },

    destroy: function () {
        AbstractMenu.prototype.destroy.call(this);
        this.overlay.destroy();
        this.contextmenuElement.removeData('contextmenu');
        return this;
    },

    undelegateEvents: function () {
        AbstractMenu.prototype.undelegateEvents.call(this);
        this.contextmenuElement.off(this.addNamespace('contextmenu'));
        this.overlay.undelegateEvents();
        return this;
    },

    appendContent: function (content) {
        let $content = $(content);
        this.getElement().append($content);
        return this;
    },

    addChild: function () {
        AbstractMenu.prototype.addChild.apply(this, arguments);
        this.next = this.getFirstChild();
        this.prev = this.getLastChild();
        return this;
    },

    build: function (container, items) {
        _.each(items, function (item) {
            let child;
            if (_.has(item, 'items')) {
                child = this.build((new Submenu(item, this.contextmenuElement)), item.items);
            } else {
                child = new MenuItem(item);
            }
            container.addChild(child);
        }, this);
        return container;
    },

    focus: function () {
        this.getElement().focus();
        return this;
    },

    open: function () {
        let $menu = (this.isRendered) ? this.getElement() : this.populateElement();
        this.container.append($menu);
        AbstractItem.prototype.open.call(this);
        this.overlay.show(this.container);
        return this;
    },

    close: function () {
        AbstractMenu.prototype.close.call(this);
        this.getElement().detach();
        this.overlay.hide();
        return this;
    },

    position: function (event) {
        this.getElement().position({
            my: 'left top',
            of: event,
            collision: 'flipfit flipfit',
            within: this.contextmenuElement
        });

        return this;
    },

    pointInContainerBox: function (x, y) {
        let containerOffset = this.contextmenuElement.offset(),
            containerBox = {
                x0: containerOffset.left,
                y0: containerOffset.top,
                x1: containerOffset.left + this.contextmenuElement.outerWidth(),
                y1: containerOffset.top + this.contextmenuElement.outerHeight()
            };
        return containerBox.x0 <= x && x <= containerBox.x1 && containerBox.y0 <= y && y <= containerBox.y1;
    },

    getOverlay: function () {
        return new Overlay(
            this.close.bind(this),
            function (event) {
                event.preventDefault();
                if (this.pointInContainerBox(event.pageX, event.pageY)) {
                    this.position(event).focus();
                    this.closeChildren();
                } else {
                    this.close();
                }
            }.bind(this)
        );
    },

    contextmenuHandler: function (event) {
        event.preventDefault();
        event.stopPropagation();
        this.open().position(event).focus();
    },

    keyDownHandler: function (event, item) {
        let KEY = $.ui.keyCode,
            keyCode = event.keyCode;

        // eslint-disable-next-line default-case
        switch (keyCode) {
            case KEY.UP:
                item.getPrev().focus();
                event.stopPropagation();
                break;
            case KEY.DOWN:
                item.getNext().focus();
                event.stopPropagation();
                break;
            case KEY.TAB:
                event.stopPropagation();
                break;
            case KEY.ESCAPE:
                this.close();
                break;
        }

        return false;
    }
});

let Overlay = Component.extend({
    ns: '.overlay',
    initialize: function (clickHandler, contextmenuHandler) {
        this.element = $('<div />', {
            class: 'overlay'
        });
        this.clickHandler = clickHandler;
        this.contextmenuHandler = contextmenuHandler;
    },

    destroy: function () {
        this.getElement().remove();
        this.undelegateEvents();
    },

    getElement: function () {
        return this.element;
    },

    hide: function () {
        this.getElement().detach();
        this.undelegateEvents();
        return this;
    },

    show: function (container) {
        let $elem = $(this.getElement());
        $(container).append($elem);
        this.delegateEvents();
        return this;
    },

    delegateEvents: function () {
        let self = this;
        $(document)
            .on('click' + this.ns, function () {
                if (_.isFunction(self.clickHandler)) {
                    self.clickHandler.apply(this, arguments);
                }
                self.hide();
            })
            .on('contextmenu' + this.ns, function () {
                if (_.isFunction(self.contextmenuHandler)) {
                    self.contextmenuHandler.apply(this, arguments);
                }
            });
        return this;
    },

    undelegateEvents: function () {
        $(document).off(this.ns);
        return this;
    }
});

let Submenu = AbstractMenu.extend({
    initialize: function (options, contextmenuElement) {
        this.contextmenuElement = contextmenuElement;
        AbstractMenu.prototype.initialize.apply(this, arguments);
    },

    createElement: function () {
        let $spanElem,
            $listElem,
            $element = $('<li />', {
                class: ['submenu-item', 'menu-item', this.options.prefix + 'submenu-item'].join(' '),
                'aria-expanded': 'false',
                'aria-haspopup': 'true',
                'aria-labelledby': 'submenu-item-label-' + this.id,
                role: 'menuitem',
                tabindex: -1
            });

        $spanElem = $('<span />', {
            id: 'submenu-item-label-' + this.id,
            text: this.options.label
        });
        this.label = $spanElem.appendTo($element);

        $listElem = $('<ol />', {
            class: ['submenu', this.options.prefix + 'submenu'].join(' '),
            role: 'menu'
        });

        this.list = $listElem.appendTo($element);

        return $element;
    },

    appendContent: function (content) {
        let $content = $(content);
        this.list.append($content);
        return this;
    },

    setLabel: function (label) {
        this.label.text(label);
        return this;
    },

    openKeyboard: function () {
        if (this.hasChildren()) {
            this.open();
            this.getFirstChild().focus();
        }
        return this;
    },

    keyDownHandler: function (event) {
        let KEY = $.ui.keyCode,
            keyCode = event.keyCode;

        // eslint-disable-next-line default-case
        switch (keyCode) {
            case KEY.LEFT:
                this.close().focus();
                event.stopPropagation();
                break;
            case KEY.RIGHT:
            case KEY.ENTER:
            case KEY.SPACE:
                this.openKeyboard();
                event.stopPropagation();
                break;
        }

        return false;
    },

    open: function () {
        AbstractMenu.prototype.open.call(this);
        this.getElement().attr({'aria-expanded': 'true'});
        this.position();
        return this;
    },

    close: function () {
        AbstractMenu.prototype.close.call(this);
        this.getElement().attr({'aria-expanded': 'false'});
        return this;
    },

    position: function () {
        this.list.position({
            my: 'left top',
            at: 'right top',
            of: this.getElement(),
            collision: 'flipfit flipfit',
            within: this.contextmenuElement
        });
        return this;
    },

    mouseOverHandler: function () {
        clearTimeout(this.timer);
        this.timer = setTimeout(this.open.bind(this), 200);
        this.focus();
    },

    mouseLeaveHandler: function () {
        clearTimeout(this.timer);
        this.timer = setTimeout(this.close.bind(this), 200);
        this.focus();
    }
});

let MenuItem = AbstractItem.extend({
    createElement: function () {
        let classNames = [
            'menu-item', this.options.prefix + 'menu-item',
            this.options.isSelected ? 'is-selected' : ''
        ].join(' ');

        return $('<li />', {
            class: classNames,
            'aria-selected': this.options.isSelected ? 'true' : 'false',
            role: 'menuitem',
            tabindex: -1,
            text: this.options.label
        });
    },

    populateElement: function () {
        return this.getElement();
    },

    delegateEvents: function () {
        this.bindEvent(this.getElement(), 'click keydown contextmenu mouseover', this.itemHandler.bind(this));
        return this;
    },

    setLabel: function (label) {
        this.getElement().text(label);
        return this;
    },

    select: function (event) {
        this.options.callback.call(this, event, this, this.options);
        this.getElement()
            .addClass('is-selected')
            .attr({'aria-selected': 'true'});
        _.invoke(this.getSiblings(), 'unselect');
        // Hide the menu.
        this.getRoot().close();
        return this;
    },

    unselect: function () {
        this.getElement()
            .removeClass('is-selected')
            .attr({'aria-selected': 'false'});
        return this;
    },

    itemHandler: function (event) {
        event.preventDefault();
        // eslint-disable-next-line default-case
        switch (event.type) {
            case 'contextmenu':
            case 'click':
                this.select();
                break;
            case 'mouseover':
                this.focus();
                event.stopPropagation();
                break;
            case 'keydown':
                this.keyDownHandler.call(this, event, this);
                break;
        }
    },

    keyDownHandler: function (event) {
        let KEY = $.ui.keyCode,
            keyCode = event.keyCode;

        // eslint-disable-next-line default-case
        switch (keyCode) {
            case KEY.RIGHT:
                event.stopPropagation();
                break;
            case KEY.ENTER:
            case KEY.SPACE:
                this.select();
                event.stopPropagation();
                break;
        }

        return false;
    }
});

let VideoContextMenu = function(state, i18n) {
    let speedCallback = function(event, menuitem, options) {
            let speed = parseFloat(options.label);
            state.videoCommands.execute('speed', speed);
        }
     let options = {
            items: [{
                label: i18n.Play,
                callback: function () {
                    state.videoCommands.execute('togglePlayback');
                },
                initialize: function (menuitem) {
                    state.el.on({
                        play: function () {
                            menuitem.setLabel(i18n.Pause);
                        },
                        pause: function () {
                            menuitem.setLabel(i18n.Play);
                        }
                    });
                }
            }, {
                label: state.videoVolumeControl.getMuteStatus() ? i18n.Unmute : i18n.Mute,
                callback: function () {
                    state.videoCommands.execute('toggleMute');
                },
                initialize: function (menuitem) {
                    state.el.on({
                        volumechange: function () {
                            if (state.videoVolumeControl.getMuteStatus()) {
                                menuitem.setLabel(i18n.Unmute);
                            } else {
                                menuitem.setLabel(i18n.Mute);
                            }
                        }
                    });
                }
            }, {
                label: i18n['Fill browser'],
                callback: function () {
                    state.videoCommands.execute('toggleFullScreen');
                },
                initialize: function (menuitem) {
                    state.el.on({
                        fullscreen: function (event, isFullscreen) {
                            if (isFullscreen) {
                                menuitem.setLabel(i18n['Exit full browser']);
                            } else {
                                menuitem.setLabel(i18n['Fill browser']);
                            }
                        }
                    });
                }
            }, {
                label: i18n.Speed,
                items: _.map(state.speeds, function (speed) {
                    let isSelected = parseFloat(speed) === state.speed;
                    return {
                        label: speed + 'x', callback: speedCallback, speed: speed, isSelected: isSelected
                    };
                }),
                initialize: function (menuitem) {
                    state.el.on({
                        speedchange: function (event, speed) {
                            // eslint-disable-next-line no-shadow
                            let item = menuitem.getChildren().filter(function (item) {
                                return item.options.speed === speed;
                            })[0];
                            if (item) {
                                item.select();
                            }
                        }
                    });
                }
            }
            ]
        };

    // eslint-disable-next-line no-shadow
    $.fn.contextmenu = function(container, options) {
        return this.each(function () {
            $(this).data('contextmenu', new Menu(options, this, container));
        });
    };

    if (!state.isYoutubeType()) {
        state.el.find('video').contextmenu(state.el, options);
        state.el.on('destroy', function () {
            let contextmenu = $(this).find('video').data('contextmenu');
            if (contextmenu) {
                contextmenu.destroy();
            }
        });
    }

    return $.Deferred().resolve().promise();
}

export default VideoContextMenu
