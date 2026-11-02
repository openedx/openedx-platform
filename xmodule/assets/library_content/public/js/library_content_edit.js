/* JavaScript for special editing operations that can be done on LibraryContentXBlock */
window.LibraryContentAuthorView = function(runtime, element, initArgs) {
    'use strict';
    var $element = $(element);
    var usage_id = $element.data('usage-id');
    // The "Update Now" button is not a child of 'element', as it is in the validation message area
    // But it is still inside this xblock's wrapper element, which we can easily find:
    var $wrapper = $element.parents('*[data-locator="' + usage_id + '"]');
    var { is_root: isRoot = false } = initArgs;

    function postMessageToParent(body, callbackFn = null) {
        try {
            window.parent.postMessage(body, document.referrer);
            if (callbackFn) {
              callbackFn();
            }
        } catch (e) {
            console.error('Failed to post message:', e);
        }
    };

    function reloadPreviewPage() {
        if (window.self !== window.top) {
            // We are inside iframe
            // Normal location.reload() reloads the iframe but subsequent calls to
            // postMessage fails. So we are using postMessage to tell the parent page
            // to reload the iframe.
            postMessageToParent({
                type: 'refreshIframe',
                message: 'Refresh Iframe',
                payload: {},
            })
        } else {
            location.reload();
        }
    }

    $wrapper.on('click', '.library-update-btn', function(e) {
        e.preventDefault();
        // Update the XBlock with the latest matching content from the library:
        runtime.notify('save', {
            state: 'start',
            element: element,
            message: gettext('Updating with latest library content')
        });
        $.post(runtime.handlerUrl(element, 'upgrade_and_sync')).done(function() {
            runtime.notify('save', {
                state: 'end',
                element: element
            });
            if (isRoot) {
                // We are inside preview page where all children blocks are listed.
                reloadPreviewPage();
            }
        });
    });

    $wrapper.on('click', '.library-block-migrate-btn', function(e) {
        e.preventDefault();
        // migrate library content block to item bank block
        runtime.notify('save', {
            state: 'start',
            element: element,
            message: gettext('Migrating to Problem Bank')
        });
        $.post(runtime.handlerUrl(element, 'upgrade_to_v2_library')).done(function() {
            runtime.notify('save', {
                state: 'end',
                element: element
            });
            if (isRoot) {
                // We are inside preview page where all children blocks are listed.
                reloadPreviewPage();
            }
        });
    });

    // Hide loader and show element when update task finished.
    var $loader = $wrapper.find('.ui-loading');
    var $xblockHeader = $wrapper.find('.xblock-header');
    if (!$loader.hasClass('is-hidden')) {
        var timer = setInterval(function() {
            $.get(runtime.handlerUrl(element, 'children_are_syncing'), function( data ) {
                if (data !== true) {
                    $loader.addClass('is-hidden');
                    $xblockHeader.removeClass('is-hidden');
                    clearInterval(timer);
                    runtime.notify('save', {
                        state: 'end',
                        element: element
                    });
                }
            })
        }, 1000);
    }
};
