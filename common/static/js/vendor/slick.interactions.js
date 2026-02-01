"use strict";
(() => {
  // src/slick.interactions.ts
  var Utils = Slick.Utils;
  function Draggable(options) {
    let { containerElement } = options, { onDragInit, onDragStart, onDrag, onDragEnd, preventDragFromKeys } = options, element, startX, startY, deltaX, deltaY, dragStarted;
    containerElement || (containerElement = document.body);
    let originaldd = {
      dragSource: containerElement,
      dragHandle: null
    };
    function init() {
      containerElement && (containerElement.addEventListener("mousedown", userPressed), containerElement.addEventListener("touchstart", userPressed));
    }
    function executeDragCallbackWhenDefined(callback, evt, dd) {
      if (typeof callback == "function")
        return callback(evt, dd);
    }
    function destroy() {
      containerElement && (containerElement.removeEventListener("mousedown", userPressed), containerElement.removeEventListener("touchstart", userPressed));
    }
    function preventDrag(event) {
      let eventPrevented = !1;
      return preventDragFromKeys && preventDragFromKeys.forEach((key) => {
        event[key] && (eventPrevented = !0);
      }), eventPrevented;
    }
    function userPressed(event) {
      var _a, _b;
      if (!preventDrag(event)) {
        element = event.target;
        let targetEvent = (_b = (_a = event == null ? void 0 : event.touches) == null ? void 0 : _a[0]) != null ? _b : event, { target } = targetEvent;
        if (!options.allowDragFrom || options.allowDragFrom && element.matches(options.allowDragFrom) || options.allowDragFromClosest && element.closest(options.allowDragFromClosest)) {
          originaldd.dragHandle = element;
          let winScrollPos = Utils.windowScrollPosition();
          startX = winScrollPos.left + targetEvent.clientX, startY = winScrollPos.top + targetEvent.clientY, deltaX = targetEvent.clientX - targetEvent.clientX, deltaY = targetEvent.clientY - targetEvent.clientY, originaldd = Object.assign(originaldd, { deltaX, deltaY, startX, startY, target }), executeDragCallbackWhenDefined(onDragInit, event, originaldd) !== !1 && (document.body.addEventListener("mousemove", userMoved), document.body.addEventListener("touchmove", userMoved), document.body.addEventListener("mouseup", userReleased), document.body.addEventListener("touchend", userReleased), document.body.addEventListener("touchcancel", userReleased));
        }
      }
    }
    function userMoved(event) {
      var _a, _b;
      if (!preventDrag(event)) {
        let targetEvent = (_b = (_a = event == null ? void 0 : event.touches) == null ? void 0 : _a[0]) != null ? _b : event;
        deltaX = targetEvent.clientX - startX, deltaY = targetEvent.clientY - startY;
        let { target } = targetEvent;
        dragStarted || (originaldd = Object.assign(originaldd, { deltaX, deltaY, startX, startY, target }), executeDragCallbackWhenDefined(onDragStart, event, originaldd), dragStarted = !0), originaldd = Object.assign(originaldd, { deltaX, deltaY, startX, startY, target }), executeDragCallbackWhenDefined(onDrag, event, originaldd);
      }
    }
    function userReleased(event) {
      if (document.body.removeEventListener("mousemove", userMoved), document.body.removeEventListener("touchmove", userMoved), document.body.removeEventListener("mouseup", userReleased), document.body.removeEventListener("touchend", userReleased), document.body.removeEventListener("touchcancel", userReleased), dragStarted) {
        let { target } = event;
        originaldd = Object.assign(originaldd, { target }), executeDragCallbackWhenDefined(onDragEnd, event, originaldd), dragStarted = !1;
      }
    }
    return init(), { destroy };
  }
  function MouseWheel(options) {
    let { element, onMouseWheel } = options;
    function destroy() {
      element.removeEventListener("wheel", wheelHandler), element.removeEventListener("mousewheel", wheelHandler);
    }
    function init() {
      element.addEventListener("wheel", wheelHandler), element.addEventListener("mousewheel", wheelHandler);
    }
    function wheelHandler(event) {
      let orgEvent = event || window.event, delta = 0, deltaX = 0, deltaY = 0;
      orgEvent.wheelDelta && (delta = orgEvent.wheelDelta / 120), orgEvent.detail && (delta = -orgEvent.detail / 3), deltaY = delta, orgEvent.axis !== void 0 && orgEvent.axis === orgEvent.HORIZONTAL_AXIS && (deltaY = 0, deltaX = -1 * delta), orgEvent.wheelDeltaY !== void 0 && (deltaY = orgEvent.wheelDeltaY / 120), orgEvent.wheelDeltaX !== void 0 && (deltaX = -1 * orgEvent.wheelDeltaX / 120), typeof onMouseWheel == "function" && onMouseWheel(event, delta, deltaX, deltaY);
    }
    return init(), { destroy };
  }
  function Resizable(options) {
    let { resizeableElement, resizeableHandleElement, onResizeStart, onResize, onResizeEnd } = options;
    if (!resizeableHandleElement || typeof resizeableHandleElement.addEventListener != "function")
      throw new Error("[Slick.Resizable] You did not provide a valid html element that will be used for the handle to resize.");
    function init() {
      resizeableHandleElement.addEventListener("mousedown", resizeStartHandler), resizeableHandleElement.addEventListener("touchstart", resizeStartHandler);
    }
    function destroy() {
      typeof (resizeableHandleElement == null ? void 0 : resizeableHandleElement.removeEventListener) == "function" && (resizeableHandleElement.removeEventListener("mousedown", resizeStartHandler), resizeableHandleElement.removeEventListener("touchstart", resizeStartHandler));
    }
    function executeResizeCallbackWhenDefined(callback, e) {
      if (typeof callback == "function")
        return callback(e, { resizeableElement, resizeableHandleElement });
    }
    function resizeStartHandler(e) {
      e.preventDefault();
      let event = e.touches ? e.changedTouches[0] : e;
      executeResizeCallbackWhenDefined(onResizeStart, event) !== !1 && (document.body.addEventListener("mousemove", resizingHandler), document.body.addEventListener("mouseup", resizeEndHandler), document.body.addEventListener("touchmove", resizingHandler), document.body.addEventListener("touchend", resizeEndHandler));
    }
    function resizingHandler(e) {
      e.preventDefault && e.type !== "touchmove" && e.preventDefault();
      let event = e.touches ? e.changedTouches[0] : e;
      typeof onResize == "function" && onResize(event, { resizeableElement, resizeableHandleElement });
    }
    function resizeEndHandler(e) {
      let event = e.touches ? e.changedTouches[0] : e;
      executeResizeCallbackWhenDefined(onResizeEnd, event), document.body.removeEventListener("mousemove", resizingHandler), document.body.removeEventListener("mouseup", resizeEndHandler), document.body.removeEventListener("touchmove", resizingHandler), document.body.removeEventListener("touchend", resizeEndHandler);
    }
    return init(), { destroy };
  }
  window.Slick && Utils.extend(Slick, {
    Draggable,
    MouseWheel,
    Resizable
  });
})();
//# sourceMappingURL=slick.interactions.js.map