"use strict";
var Slick = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: !0, configurable: !0, writable: !0, value }) : obj[key] = value;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: !0 });
  }, __copyProps = (to, from, except, desc) => {
    if (from && typeof from == "object" || typeof from == "function")
      for (let key of __getOwnPropNames(from))
        !__hasOwnProp.call(to, key) && key !== except && __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: !0 }), mod);
  var __publicField = (obj, key, value) => __defNormalProp(obj, typeof key != "symbol" ? key + "" : key, value);

  // src/slick.core.ts
  var slick_core_exports = {};
  __export(slick_core_exports, {
    BindingEventService: () => BindingEventService,
    ColAutosizeMode: () => ColAutosizeMode,
    EditorLock: () => EditorLock,
    Event: () => Event,
    EventData: () => EventData,
    EventHandler: () => EventHandler,
    GlobalEditorLock: () => GlobalEditorLock,
    GridAutosizeColsMode: () => GridAutosizeColsMode,
    Group: () => Group,
    GroupTotals: () => GroupTotals,
    NonDataRow: () => NonDataRow,
    Range: () => Range,
    RegexSanitizer: () => RegexSanitizer,
    RowSelectionMode: () => RowSelectionMode,
    SlickEditorLock: () => SlickEditorLock,
    SlickEvent: () => SlickEvent,
    SlickEventData: () => SlickEventData,
    SlickEventHandler: () => SlickEventHandler,
    SlickGlobalEditorLock: () => SlickGlobalEditorLock,
    SlickGroup: () => SlickGroup,
    SlickGroupTotals: () => SlickGroupTotals,
    SlickNonDataItem: () => SlickNonDataItem,
    SlickRange: () => SlickRange,
    Utils: () => Utils,
    ValueFilterMode: () => ValueFilterMode,
    WidthEvalMode: () => WidthEvalMode,
    keyCode: () => keyCode,
    preClickClassName: () => preClickClassName
  });
  var SlickEventData = class {
    constructor(event, args) {
      this.event = event;
      this.args = args;
      __publicField(this, "_isPropagationStopped", !1);
      __publicField(this, "_isImmediatePropagationStopped", !1);
      __publicField(this, "_isDefaultPrevented", !1);
      __publicField(this, "returnValues", []);
      __publicField(this, "returnValue");
      __publicField(this, "_eventTarget");
      __publicField(this, "nativeEvent");
      __publicField(this, "arguments_");
      // public props that can be optionally pulled from the provided Event in constructor
      // they are all optional props because it really depends on the type of Event provided (KeyboardEvent, MouseEvent, ...)
      __publicField(this, "altKey");
      __publicField(this, "ctrlKey");
      __publicField(this, "metaKey");
      __publicField(this, "shiftKey");
      __publicField(this, "key");
      __publicField(this, "keyCode");
      __publicField(this, "clientX");
      __publicField(this, "clientY");
      __publicField(this, "offsetX");
      __publicField(this, "offsetY");
      __publicField(this, "pageX");
      __publicField(this, "pageY");
      __publicField(this, "bubbles");
      __publicField(this, "target");
      __publicField(this, "type");
      __publicField(this, "which");
      __publicField(this, "x");
      __publicField(this, "y");
      this.nativeEvent = event, this.arguments_ = args, event && [
        "altKey",
        "ctrlKey",
        "metaKey",
        "shiftKey",
        "key",
        "keyCode",
        "clientX",
        "clientY",
        "offsetX",
        "offsetY",
        "pageX",
        "pageY",
        "bubbles",
        "target",
        "type",
        "which",
        "x",
        "y"
      ].forEach((key) => this[key] = event[key]), this._eventTarget = this.nativeEvent ? this.nativeEvent.target : void 0;
    }
    get defaultPrevented() {
      return this._isDefaultPrevented;
    }
    /**
     * Stops event from propagating up the DOM tree.
     * @method stopPropagation
     */
    stopPropagation() {
      var _a;
      this._isPropagationStopped = !0, (_a = this.nativeEvent) == null || _a.stopPropagation();
    }
    /**
     * Returns whether stopPropagation was called on this event object.
     * @method isPropagationStopped
     * @return {Boolean}
     */
    isPropagationStopped() {
      return this._isPropagationStopped;
    }
    /**
     * Prevents the rest of the handlers from being executed.
     * @method stopImmediatePropagation
     */
    stopImmediatePropagation() {
      this._isImmediatePropagationStopped = !0, this.nativeEvent && this.nativeEvent.stopImmediatePropagation();
    }
    /**
     * Returns whether stopImmediatePropagation was called on this event object.\
     * @method isImmediatePropagationStopped
     * @return {Boolean}
     */
    isImmediatePropagationStopped() {
      return this._isImmediatePropagationStopped;
    }
    getNativeEvent() {
      return this.nativeEvent;
    }
    preventDefault() {
      this.nativeEvent && this.nativeEvent.preventDefault(), this._isDefaultPrevented = !0;
    }
    isDefaultPrevented() {
      return this.nativeEvent ? this.nativeEvent.defaultPrevented : this._isDefaultPrevented;
    }
    addReturnValue(value) {
      this.returnValues.push(value), this.returnValue === void 0 && value !== void 0 && (this.returnValue = value);
    }
    getReturnValue() {
      return this.returnValue;
    }
    getArguments() {
      return this.arguments_;
    }
  }, SlickEvent = class {
    /**
     * Constructor
     * @param {String} [eventName] - event name that could be used for dispatching CustomEvent (when enabled)
     * @param {BasePubSub} [pubSubService] - event name that could be used for dispatching CustomEvent (when enabled)
     */
    constructor(eventName, pubSub) {
      this.eventName = eventName;
      this.pubSub = pubSub;
      __publicField(this, "_handlers", []);
      __publicField(this, "_pubSubService");
      this._pubSubService = pubSub;
    }
    get subscriberCount() {
      return this._handlers.length;
    }
    /**
     * Adds an event handler to be called when the event is fired.
     * <p>Event handler will receive two arguments - an <code>EventData</code> and the <code>data</code>
     * object the event was fired with.<p>
     * @method subscribe
     * @param {Function} fn - Event handler.
     */
    subscribe(fn) {
      this._handlers.push(fn);
    }
    /**
     * Removes an event handler added with <code>subscribe(fn)</code>.
     * @method unsubscribe
     * @param {Function} [fn] - Event handler to be removed.
     */
    unsubscribe(fn) {
      for (let i = this._handlers.length - 1; i >= 0; i--)
        this._handlers[i] === fn && this._handlers.splice(i, 1);
    }
    /**
     * Fires an event notifying all subscribers.
     * @method notify
     * @param {Object} args Additional data object to be passed to all handlers.
     * @param {EventData} [event] - An <code>EventData</code> object to be passed to all handlers.
     *      For DOM events, an existing W3C event object can be passed in.
     * @param {Object} [scope] - The scope ("this") within which the handler will be executed.
     *      If not specified, the scope will be set to the <code>Event</code> instance.
     */
    notify(args, evt, scope) {
      var _a;
      let sed = evt instanceof SlickEventData ? evt : new SlickEventData(evt, args);
      scope = scope || this;
      for (let i = 0; i < this._handlers.length && !(sed.isPropagationStopped() || sed.isImmediatePropagationStopped()); i++) {
        let returnValue = this._handlers[i].call(scope, sed, args);
        sed.addReturnValue(returnValue);
      }
      if (typeof ((_a = this._pubSubService) == null ? void 0 : _a.publish) == "function" && this.eventName) {
        let ret = this._pubSubService.publish(this.eventName, { args, eventData: sed });
        sed.addReturnValue(ret);
      }
      return sed;
    }
    setPubSubService(pubSub) {
      this._pubSubService = pubSub;
    }
  }, SlickEventHandler = class {
    constructor() {
      __publicField(this, "handlers", []);
    }
    subscribe(event, handler) {
      return this.handlers.push({ event, handler }), event.subscribe(handler), this;
    }
    unsubscribe(event, handler) {
      let i = this.handlers.length;
      for (; i--; )
        if (this.handlers[i].event === event && this.handlers[i].handler === handler) {
          this.handlers.splice(i, 1), event.unsubscribe(handler);
          return;
        }
      return this;
    }
    unsubscribeAll() {
      let i = this.handlers.length;
      for (; i--; )
        this.handlers[i].event.unsubscribe(this.handlers[i].handler);
      return this.handlers = [], this;
    }
  }, SlickRange = class {
    constructor(fromRow, fromCell, toRow, toCell) {
      __publicField(this, "fromRow");
      __publicField(this, "fromCell");
      __publicField(this, "toCell");
      __publicField(this, "toRow");
      toRow === void 0 && toCell === void 0 && (toRow = fromRow, toCell = fromCell), this.fromRow = Math.min(fromRow, toRow), this.fromCell = Math.min(fromCell, toCell), this.toCell = Math.max(fromCell, toCell), this.toRow = Math.max(fromRow, toRow);
    }
    /**
     * Returns whether a range represents a single row.
     * @method isSingleRow
     * @return {Boolean}
     */
    isSingleRow() {
      return this.fromRow === this.toRow;
    }
    /**
     * Returns whether a range represents a single cell.
     * @method isSingleCell
     * @return {Boolean}
     */
    isSingleCell() {
      return this.fromRow === this.toRow && this.fromCell === this.toCell;
    }
    /**
     * Returns whether a range contains a given cell.
     * @method contains
     * @param row {Integer}
     * @param cell {Integer}
     * @return {Boolean}
     */
    contains(row, cell) {
      return row >= this.fromRow && row <= this.toRow && cell >= this.fromCell && cell <= this.toCell;
    }
    /**
     * Returns a readable representation of a range.
     * @method toString
     * @return {String}
     */
    toString() {
      return this.isSingleCell() ? `(${this.fromRow}:${this.fromCell})` : `(${this.fromRow}:${this.fromCell} - ${this.toRow}:${this.toCell})`;
    }
  }, SlickNonDataItem = class {
    constructor() {
      __publicField(this, "__nonDataRow", !0);
    }
  }, SlickGroup = class extends SlickNonDataItem {
    constructor() {
      super();
      __publicField(this, "__group", !0);
      /**
       * Grouping level, starting with 0.
       * @property level
       * @type {Number}
       */
      __publicField(this, "level", 0);
      /**
       * Number of rows in the group.
       * @property count
       * @type {Integer}
       */
      __publicField(this, "count", 0);
      /**
       * Grouping value.
       * @property value
       * @type {Object}
       */
      __publicField(this, "value", null);
      /**
       * Formatted display value of the group.
       * @property title
       * @type {String}
       */
      __publicField(this, "title", null);
      /**
       * Whether a group is collapsed.
       * @property collapsed
       * @type {Boolean}
       */
      __publicField(this, "collapsed", !1);
      /**
       * Whether a group selection checkbox is checked.
       * @property selectChecked
       * @type {Boolean}
       */
      __publicField(this, "selectChecked", !1);
      /**
       * GroupTotals, if any.
       * @property totals
       * @type {GroupTotals}
       */
      __publicField(this, "totals", null);
      /**
       * Rows that are part of the group.
       * @property rows
       * @type {Array}
       */
      __publicField(this, "rows", []);
      /**
       * Sub-groups that are part of the group.
       * @property groups
       * @type {Array}
       */
      __publicField(this, "groups", null);
      /**
       * A unique key used to identify the group.  This key can be used in calls to DataView
       * collapseGroup() or expandGroup().
       * @property groupingKey
       * @type {Object}
       */
      __publicField(this, "groupingKey", null);
    }
    /**
     * Compares two Group instances.
     * @method equals
     * @return {Boolean}
     * @param group {Group} Group instance to compare to.
     */
    equals(group) {
      return this.value === group.value && this.count === group.count && this.collapsed === group.collapsed && this.title === group.title;
    }
  }, SlickGroupTotals = class extends SlickNonDataItem {
    constructor() {
      super();
      __publicField(this, "__groupTotals", !0);
      /**
       * Parent Group.
       * @param group
       * @type {Group}
       */
      __publicField(this, "group", null);
      /**
       * Whether the totals have been fully initialized / calculated.
       * Will be set to false for lazy-calculated group totals.
       * @param initialized
       * @type {Boolean}
       */
      __publicField(this, "initialized", !1);
    }
  }, SlickEditorLock = class {
    constructor() {
      __publicField(this, "activeEditController", null);
    }
    /**
     * Returns true if a specified edit controller is active (has the edit lock).
     * If the parameter is not specified, returns true if any edit controller is active.
     * @method isActive
     * @param editController {EditController}
     * @return {Boolean}
     */
    isActive(editController) {
      return editController ? this.activeEditController === editController : this.activeEditController !== null;
    }
    /**
     * Sets the specified edit controller as the active edit controller (acquire edit lock).
     * If another edit controller is already active, and exception will be throw new Error(.
     * @method activate
     * @param editController {EditController} edit controller acquiring the lock
     */
    activate(editController) {
      if (editController !== this.activeEditController) {
        if (this.activeEditController !== null)
          throw new Error("Slick.EditorLock.activate: an editController is still active, can't activate another editController");
        if (!editController.commitCurrentEdit)
          throw new Error("Slick.EditorLock.activate: editController must implement .commitCurrentEdit()");
        if (!editController.cancelCurrentEdit)
          throw new Error("Slick.EditorLock.activate: editController must implement .cancelCurrentEdit()");
        this.activeEditController = editController;
      }
    }
    /**
     * Unsets the specified edit controller as the active edit controller (release edit lock).
     * If the specified edit controller is not the active one, an exception will be throw new Error(.
     * @method deactivate
     * @param editController {EditController} edit controller releasing the lock
     */
    deactivate(editController) {
      if (this.activeEditController) {
        if (this.activeEditController !== editController)
          throw new Error("Slick.EditorLock.deactivate: specified editController is not the currently active one");
        this.activeEditController = null;
      }
    }
    /**
     * Attempts to commit the current edit by calling "commitCurrentEdit" method on the active edit
     * controller and returns whether the commit attempt was successful (commit may fail due to validation
     * errors, etc.).  Edit controller's "commitCurrentEdit" must return true if the commit has succeeded
     * and false otherwise.  If no edit controller is active, returns true.
     * @method commitCurrentEdit
     * @return {Boolean}
     */
    commitCurrentEdit() {
      return this.activeEditController ? this.activeEditController.commitCurrentEdit() : !0;
    }
    /**
     * Attempts to cancel the current edit by calling "cancelCurrentEdit" method on the active edit
     * controller and returns whether the edit was successfully cancelled.  If no edit controller is
     * active, returns true.
     * @method cancelCurrentEdit
     * @return {Boolean}
     */
    cancelCurrentEdit() {
      return this.activeEditController ? this.activeEditController.cancelCurrentEdit() : !0;
    }
  };
  function regexSanitizer(dirtyHtml) {
    return dirtyHtml.replace(/(\b)(on[a-z]+)(\s*)=|javascript:([^>]*)[^>]*|(<\s*)(\/*)script([<>]*).*(<\s*)(\/*)script(>*)|(&lt;)(\/*)(script|script defer)(.*)(&gt;|&gt;">)/gi, "");
  }
  var BindingEventService = class {
    constructor() {
      __publicField(this, "_boundedEvents", []);
    }
    getBoundedEvents() {
      return this._boundedEvents;
    }
    destroy() {
      this.unbindAll();
    }
    /** Bind an event listener to any element */
    bind(element, eventName, listener, options, groupName = "") {
      element && (element.addEventListener(eventName, listener, options), this._boundedEvents.push({ element, eventName, listener, groupName }));
    }
    /** Unbind all will remove every every event handlers that were bounded earlier */
    unbind(element, eventName, listener) {
      element != null && element.removeEventListener && element.removeEventListener(eventName, listener);
    }
    unbindByEventName(element, eventName) {
      let boundedEvent = this._boundedEvents.find((e) => e.element === element && e.eventName === eventName);
      boundedEvent && this.unbind(boundedEvent.element, boundedEvent.eventName, boundedEvent.listener);
    }
    /**
     * Unbind all event listeners that were bounded, optionally provide a group name to unbind all listeners assigned to that specific group only.
     */
    unbindAll(groupName) {
      if (groupName) {
        let groupNames = Array.isArray(groupName) ? groupName : [groupName];
        for (let i = this._boundedEvents.length - 1; i >= 0; --i) {
          let boundedEvent = this._boundedEvents[i];
          if (groupNames.some((g) => g === boundedEvent.groupName)) {
            let { element, eventName, listener } = boundedEvent;
            this.unbind(element, eventName, listener), this._boundedEvents.splice(i, 1);
          }
        }
      } else
        for (; this._boundedEvents.length > 0; ) {
          let boundedEvent = this._boundedEvents.pop(), { element, eventName, listener } = boundedEvent;
          this.unbind(element, eventName, listener);
        }
    }
  }, _Utils = class _Utils {
    static isFunction(obj) {
      return typeof obj == "function" && typeof obj.nodeType != "number" && typeof obj.item != "function";
    }
    static isPlainObject(obj) {
      if (!obj || _Utils.toString.call(obj) !== "[object Object]")
        return !1;
      let proto = _Utils.getProto(obj);
      if (!proto)
        return !0;
      let Ctor = _Utils.hasOwn.call(proto, "constructor") && proto.constructor;
      return typeof Ctor == "function" && _Utils.fnToString.call(Ctor) === _Utils.ObjectFunctionString;
    }
    static calculateAvailableSpace(element) {
      let bottom = 0, top = 0, left = 0, right = 0, windowHeight = window.innerHeight || 0, windowWidth = window.innerWidth || 0, scrollPosition = _Utils.windowScrollPosition(), pageScrollTop = scrollPosition.top, pageScrollLeft = scrollPosition.left, elmOffset = _Utils.offset(element);
      if (elmOffset) {
        let elementOffsetTop = elmOffset.top || 0, elementOffsetLeft = elmOffset.left || 0;
        top = elementOffsetTop - pageScrollTop, bottom = windowHeight - (elementOffsetTop - pageScrollTop), left = elementOffsetLeft - pageScrollLeft, right = windowWidth - (elementOffsetLeft - pageScrollLeft);
      }
      return { top, bottom, left, right };
    }
    static extend(...args) {
      let options, name, src, copy, copyIsArray, clone, target = args[0], i = 1, deep = !1, length = args.length;
      for (typeof target == "boolean" ? (deep = target, target = args[i] || {}, i++) : target = target || {}, typeof target != "object" && !_Utils.isFunction(target) && (target = {}), i === length && (target = this, i--); i < length; i++)
        if (_Utils.isDefined(options = args[i]))
          for (name in options)
            copy = options[name], !(name === "__proto__" || target === copy) && (deep && copy && (_Utils.isPlainObject(copy) || (copyIsArray = Array.isArray(copy))) ? (src = target[name], copyIsArray && !Array.isArray(src) ? clone = [] : !copyIsArray && !_Utils.isPlainObject(src) ? clone = {} : clone = src, copyIsArray = !1, target[name] = _Utils.extend(deep, clone, copy)) : copy !== void 0 && (target[name] = copy));
      return target;
    }
    /**
     * Create a DOM Element with any optional attributes or properties.
     * It will only accept valid DOM element properties that `createElement` would accept.
     * For example: `createDomElement('div', { className: 'my-css-class' })`,
     * for style or dataset you need to use nested object `{ style: { display: 'none' }}
     * The last argument is to optionally append the created element to a parent container element.
     * @param {String} tagName - html tag
     * @param {Object} options - element properties
     * @param {[HTMLElement]} appendToParent - parent element to append to
     */
    static createDomElement(tagName, elementOptions, appendToParent) {
      let elm = document.createElement(tagName);
      return elementOptions && Object.keys(elementOptions).forEach((elmOptionKey) => {
        elmOptionKey === "innerHTML" && console.warn(`[SlickGrid] For better CSP (Content Security Policy) support, do not use "innerHTML" directly in "createDomElement('${tagName}', { innerHTML: 'some html'})", it is better as separate assignment: "const elm = createDomElement('span'); elm.innerHTML = 'some html';"`);
        let elmValue = elementOptions[elmOptionKey];
        typeof elmValue == "object" ? Object.assign(elm[elmOptionKey], elmValue) : elm[elmOptionKey] = elementOptions[elmOptionKey];
      }), appendToParent != null && appendToParent.appendChild && appendToParent.appendChild(elm), elm;
    }
    /**
     * From any input provided, return the HTML string (when a string is provided, it will be returned "as is" but when it's a number it will be converted to string)
     * When detecting HTMLElement/DocumentFragment, we can also specify which HTML type to retrieve innerHTML or outerHTML.
     * We can get the HTML by looping through all fragment `childNodes`
     * @param {DocumentFragment | HTMLElement | string | number} input
     * @param {'innerHTML' | 'outerHTML'} [type] - when the input is a DocumentFragment or HTMLElement, which type of HTML do you want to return? 'innerHTML' or 'outerHTML'
     * @returns {String}
     */
    static getHtmlStringOutput(input, type = "innerHTML") {
      return input instanceof DocumentFragment ? [].map.call(input.childNodes, (x) => x[type]).join("") || input.textContent || "" : input instanceof HTMLElement ? input[type] : String(input);
    }
    static emptyElement(element) {
      for (; element != null && element.firstChild; )
        element.removeChild(element.firstChild);
      return element;
    }
    /**
     * Accepts string containing the class or space-separated list of classes, and
     * returns list of individual classes.
     * Method properly takes into account extra whitespaces in the `className`
     * e.g.: " class1    class2   " => will result in `['class1', 'class2']`.
     * @param {String} className - space separated list of class names
     */
    static classNameToList(className = "") {
      return className.split(" ").filter((cls) => cls);
    }
    static innerSize(elm, type) {
      let size = 0;
      if (elm) {
        let clientSize = type === "height" ? "clientHeight" : "clientWidth", sides = type === "height" ? ["top", "bottom"] : ["left", "right"];
        size = elm[clientSize];
        for (let side of sides) {
          let sideSize = parseFloat(_Utils.getElementProp(elm, `padding-${side}`) || "") || 0;
          size -= sideSize;
        }
      }
      return size;
    }
    static isDefined(value) {
      return value != null && value !== "";
    }
    static getElementProp(elm, property) {
      return elm != null && elm.getComputedStyle ? window.getComputedStyle(elm, null).getPropertyValue(property) : null;
    }
    /**
     * Get the function details (param & body) of a function.
     * It supports regular function and also ES6 arrow functions
     * @param {Function} fn - function to analyze
     * @param {Boolean} [addReturn] - when using ES6 function as single liner, we could add the missing `return ...`
     * @returns
     */
    static getFunctionDetails(fn, addReturn = !0) {
      let isAsyncFn = !1, getFunctionBody = (func) => {
        let fnStr = func.toString();
        if (isAsyncFn = fnStr.includes("async "), fnStr.replaceAll(" ", "").includes("=>({")) {
          let matches = fnStr.match(/(({.*}))/g) || [];
          return matches.length >= 1 ? `return ${matches[0].trimStart()}` : fnStr;
        }
        let isOneLinerArrowFn = !fnStr.includes("{") && fnStr.includes("=>"), body = fnStr.substring(
          fnStr.indexOf("{") + 1 || fnStr.indexOf("=>") + 2,
          fnStr.includes("}") ? fnStr.lastIndexOf("}") : fnStr.length
        );
        return addReturn && isOneLinerArrowFn && !body.startsWith("return") ? "return " + body.trimStart() : body;
      };
      return {
        params: ((func) => {
          var _a;
          let STRIP_COMMENTS = /(\/\/.*$)|(\/\*[\s\S]*?\*\/)|(\s*=[^,)]*(('(?:\\'|[^'\r\n])*')|("(?:\\"|[^"\r\n])*"))|(\s*=[^,)]*))/mg, ARG_NAMES = /([^\s,]+)/g, fnStr = func.toString().replace(STRIP_COMMENTS, "");
          return (_a = fnStr.slice(fnStr.indexOf("(") + 1, fnStr.indexOf(")")).match(ARG_NAMES)) != null ? _a : [];
        })(fn),
        body: getFunctionBody(fn),
        isAsync: isAsyncFn
      };
    }
    static insertAfterElement(referenceNode, newNode) {
      var _a;
      (_a = referenceNode.parentNode) == null || _a.insertBefore(newNode, referenceNode.nextSibling);
    }
    static isEmptyObject(obj) {
      return obj == null ? !0 : Object.entries(obj).length === 0;
    }
    static noop() {
    }
    static offset(el) {
      if (!el || !el.getBoundingClientRect)
        return;
      let box = el.getBoundingClientRect(), docElem = document.documentElement;
      return {
        top: box.top + window.pageYOffset - docElem.clientTop,
        left: box.left + window.pageXOffset - docElem.clientLeft
      };
    }
    static windowScrollPosition() {
      return {
        left: window.pageXOffset || document.documentElement.scrollLeft || 0,
        top: window.pageYOffset || document.documentElement.scrollTop || 0
      };
    }
    static width(el, value) {
      if (!(!el || !el.getBoundingClientRect)) {
        if (value === void 0)
          return el.getBoundingClientRect().width;
        _Utils.setStyleSize(el, "width", value);
      }
    }
    static height(el, value) {
      if (el) {
        if (value === void 0)
          return el.getBoundingClientRect().height;
        _Utils.setStyleSize(el, "height", value);
      }
    }
    static setStyleSize(el, style, val) {
      typeof val == "function" ? val = val() : typeof val == "string" ? el.style[style] = val : el.style[style] = val + "px";
    }
    static contains(parent, child) {
      return !parent || !child ? !1 : !_Utils.parents(child).every((p) => parent !== p);
    }
    static isHidden(el) {
      return el.offsetWidth === 0 && el.offsetHeight === 0;
    }
    static parents(el, selector) {
      let parents = [], visible = selector === ":visible", hidden = selector === ":hidden";
      for (; (el = el.parentNode) && el !== document && !(!el || !el.parentNode); )
        hidden ? _Utils.isHidden(el) && parents.push(el) : visible ? _Utils.isHidden(el) || parents.push(el) : (!selector || el.matches(selector)) && parents.push(el);
      return parents;
    }
    static toFloat(value) {
      let x = parseFloat(value);
      return isNaN(x) ? 0 : x;
    }
    static show(el, type = "") {
      Array.isArray(el) ? el.forEach((e) => e.style.display = type) : el.style.display = type;
    }
    static hide(el) {
      Array.isArray(el) ? el.forEach((e) => e.style.display = "none") : el.style.display = "none";
    }
    static slideUp(el, callback) {
      return _Utils.slideAnimation(el, "slideUp", callback);
    }
    static slideDown(el, callback) {
      return _Utils.slideAnimation(el, "slideDown", callback);
    }
    static slideAnimation(el, slideDirection, callback) {
      if (window.jQuery !== void 0) {
        window.jQuery(el)[slideDirection]("fast", callback);
        return;
      }
      slideDirection === "slideUp" ? _Utils.hide(el) : _Utils.show(el), callback();
    }
    static applyDefaults(targetObj, srcObj) {
      typeof srcObj == "object" && Object.keys(srcObj).forEach((key) => {
        srcObj.hasOwnProperty(key) && !targetObj.hasOwnProperty(key) && (targetObj[key] = srcObj[key]);
      });
    }
    /**
     * User could optionally add PubSub Service to SlickEvent
     * When it is defined then a SlickEvent `notify()` call will also dispatch it by using the PubSub publish() method
     * @param {BasePubSub} [pubSubService]
     * @param {*} scope
     */
    static addSlickEventPubSubWhenDefined(pubSub, scope) {
      if (pubSub)
        for (let prop in scope)
          scope[prop] instanceof SlickEvent && typeof scope[prop].setPubSubService == "function" && scope[prop].setPubSubService(pubSub);
    }
  };
  // jQuery's extend
  __publicField(_Utils, "getProto", Object.getPrototypeOf), __publicField(_Utils, "class2type", {}), __publicField(_Utils, "toString", _Utils.class2type.toString), __publicField(_Utils, "hasOwn", _Utils.class2type.hasOwnProperty), __publicField(_Utils, "fnToString", _Utils.hasOwn.toString), __publicField(_Utils, "ObjectFunctionString", _Utils.fnToString.call(Object)), __publicField(_Utils, "storage", {
    // https://stackoverflow.com/questions/29222027/vanilla-alternative-to-jquery-data-function-any-native-javascript-alternati
    _storage: /* @__PURE__ */ new WeakMap(),
    // eslint-disable-next-line object-shorthand
    put: function(element, key, obj) {
      this._storage.has(element) || this._storage.set(element, /* @__PURE__ */ new Map()), this._storage.get(element).set(key, obj);
    },
    // eslint-disable-next-line object-shorthand
    get: function(element, key) {
      let el = this._storage.get(element);
      return el ? el.get(key) : null;
    },
    // eslint-disable-next-line object-shorthand
    remove: function(element, key) {
      let ret = this._storage.get(element).delete(key);
      return this._storage.get(element).size !== 0 && this._storage.delete(element), ret;
    }
  });
  var Utils = _Utils, SlickGlobalEditorLock = new SlickEditorLock(), SlickCore = {
    Event: SlickEvent,
    EventData: SlickEventData,
    EventHandler: SlickEventHandler,
    Range: SlickRange,
    NonDataRow: SlickNonDataItem,
    Group: SlickGroup,
    GroupTotals: SlickGroupTotals,
    EditorLock: SlickEditorLock,
    RegexSanitizer: regexSanitizer,
    /**
     * A global singleton editor lock.
     * @class GlobalEditorLock
     * @static
     * @constructor
     */
    GlobalEditorLock: SlickGlobalEditorLock,
    keyCode: {
      SPACE: 8,
      BACKSPACE: 8,
      DELETE: 46,
      DOWN: 40,
      END: 35,
      ENTER: 13,
      ESCAPE: 27,
      HOME: 36,
      INSERT: 45,
      LEFT: 37,
      PAGE_DOWN: 34,
      PAGE_UP: 33,
      RIGHT: 39,
      TAB: 9,
      UP: 38,
      A: 65
    },
    preClickClassName: "slick-edit-preclick",
    GridAutosizeColsMode: {
      None: "NOA",
      LegacyOff: "LOF",
      LegacyForceFit: "LFF",
      IgnoreViewport: "IGV",
      FitColsToViewport: "FCV",
      FitViewportToCols: "FVC"
    },
    ColAutosizeMode: {
      Locked: "LCK",
      Guide: "GUI",
      Content: "CON",
      ContentExpandOnly: "CXO",
      ContentIntelligent: "CTI"
    },
    RowSelectionMode: {
      FirstRow: "FS1",
      FirstNRows: "FSN",
      AllRows: "ALL",
      LastRow: "LS1"
    },
    ValueFilterMode: {
      None: "NONE",
      DeDuplicate: "DEDP",
      GetGreatestAndSub: "GR8T",
      GetLongestTextAndSub: "LNSB",
      GetLongestText: "LNSC"
    },
    WidthEvalMode: {
      Auto: "AUTO",
      TextOnly: "CANV",
      HTML: "HTML"
    }
  }, {
    EditorLock,
    Event,
    EventData,
    EventHandler,
    Group,
    GroupTotals,
    NonDataRow,
    Range,
    RegexSanitizer,
    GlobalEditorLock,
    keyCode,
    preClickClassName,
    GridAutosizeColsMode,
    ColAutosizeMode,
    RowSelectionMode,
    ValueFilterMode,
    WidthEvalMode
  } = SlickCore;
  typeof global != "undefined" && window.Slick && (global.Slick = window.Slick);
  return __toCommonJS(slick_core_exports);
})();
//# sourceMappingURL=slick.core.js.map