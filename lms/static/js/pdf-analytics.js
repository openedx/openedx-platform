// Analytics shim for the embedded pdf.js viewer.
//
// Subscribes to the upstream viewer's eventBus and to the toolbar buttons
// that still exist in the modern viewer chrome. The previous version of this
// file used jQuery and bound to window-level events that the old vendored
// pdf.js dispatched; modern pdf.js dispatches on `PDFViewerApplication.eventBus`
// and ships its own (non-jQuery) toolbar.
//
// Dropped events vs. the previous shim — the modern viewer's "Views Manager"
// replaces the older sidebar/thumbnail/outline buttons, so the following
// analytics no longer fire and the corresponding dashboards will see them
// disappear at the pdf.js bump:
//     textbook.pdf.thumbnails.toggled
//     textbook.pdf.thumbnail.navigated
//     textbook.pdf.outline.toggled
//     textbook.pdf.page.scrolled  (scroll-direction detection removed)

(function () {
    "use strict";

    function sendLog(name, data, eventType) {
        // The LMS Logger is loaded inside the iframe via the
        // `<%static:js group='application'/>` directive in pdf_viewer.html.
        // Guard defensively so that if the bundle is missing or hasn't
        // finished loading, we drop the event rather than throw -- a throw
        // inside an eventBus.dispatch() callback propagates and cancels the
        // rest of pdf.js's init (which would also break navigation buttons).
        if (!window.Logger || typeof window.Logger.log !== "function") {
            return;
        }
        var message = data || {};
        message.chapter = window.PDF_URL || "";
        message.name = "textbook.pdf." + name;
        window.Logger.log(eventType || message.name, message);
    }

    function bind(app) {
        var bus = app.eventBus;
        var currentPage = app.page || 1;

        bus.on("pagechanging", function (evt) {
            var oldPage = currentPage;
            currentPage = evt.pageNumber;
            sendLog(
                "page.loaded",
                {type: "gotopage", old: oldPage, new: currentPage},
                "book"
            );
        });

        var oldScale = null;
        bus.on("scalechanging", function (evt) {
            if (evt.scale !== oldScale) {
                sendLog("display.scaled", {amount: evt.scale, page: currentPage});
                oldScale = evt.scale;
            }
        });

        // Find-bar events. Modern pdf.js dispatches a single `find` event
        // with a `type` field on the payload (e.g. "", "again",
        // "highlightallchange", "casesensitivitychange"). Old pdf.js used
        // separate event names; we map back to the same analytics names.
        var findSubEventToAnalyticsName = {
            "": "search.executed",
            again: "search.navigatednext",
            highlightallchange: "search.highlight.toggled",
            casesensitivitychange: "searchcasesensitivity.toggled"
        };
        var pendingSearch = null;
        bus.on("find", function (evt) {
            var analyticsName = findSubEventToAnalyticsName[evt && evt.type];
            if (!analyticsName) {
                return;
            }
            if (pendingSearch) {
                clearTimeout(pendingSearch);
            }
            pendingSearch = setTimeout(function () {
                var message = Object.assign({}, evt || {});
                var findMsgEl = document.getElementById("findMsg");
                message.status = findMsgEl ? findMsgEl.textContent : "";
                message.page = currentPage;
                sendLog(analyticsName, message);
            }, 500);
        });

        // Toolbar buttons. IDs are stable from the upstream viewer.html; if a
        // button is missing we just skip the binding rather than throw.
        function onClick(id, fn) {
            var el = document.getElementById(id);
            if (el) {
                el.addEventListener("click", fn);
            }
        }
        onClick("previous", function () {
            sendLog(
                "page.navigatednext",
                {type: "prevpage", new: currentPage - 1},
                "book"
            );
        });
        onClick("next", function () {
            sendLog(
                "page.navigatednext",
                {type: "nextpage", new: currentPage + 1},
                "book"
            );
        });
        onClick("zoomInButton", function () {
            sendLog("zoom.buttons.changed", {direction: "in", page: currentPage});
        });
        onClick("zoomOutButton", function () {
            sendLog("zoom.buttons.changed", {direction: "out", page: currentPage});
        });

        var pageNumberEl = document.getElementById("pageNumber");
        if (pageNumberEl) {
            pageNumberEl.addEventListener("change", function () {
                sendLog("page.navigated", {page: pageNumberEl.value});
            });
        }
        var scaleSelectEl = document.getElementById("scaleSelect");
        if (scaleSelectEl) {
            scaleSelectEl.addEventListener("change", function () {
                sendLog(
                    "zoom.menu.changed",
                    {amount: scaleSelectEl.value, page: currentPage}
                );
            });
        }
    }

    function attach() {
        var app = window.PDFViewerApplication;
        if (!app || !app.initializedPromise) {
            // Viewer hasn't booted yet; try again after the next frame.
            window.requestAnimationFrame(attach);
            return;
        }
        app.initializedPromise.then(function () { bind(app); });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", attach);
    } else {
        attach();
    }
})();
