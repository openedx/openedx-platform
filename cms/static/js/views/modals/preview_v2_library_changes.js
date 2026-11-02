/**
 * The PreviewLibraryChangesModal is a Backbone view that shows an iframe in a
 * modal window. The iframe embeds a view from the Authoring MFE that allows
 * authors to preview the new version of a library-sourced XBlock, and decide
 * whether to accept ("sync") or reject ("ignore") the changes.
 */
define(['jquery', 'underscore', 'gettext', 'js/views/modals/base_modal', 'common/js/components/utils/view_utils'],
function($, _, gettext, BaseModal, ViewUtils) {
    'use strict';

    var PreviewLibraryChangesModal = BaseModal.extend({
        events: _.extend({}, BaseModal.prototype.events, {
            'click .action-accept': 'acceptChanges',
            'click .action-ignore': 'ignoreChanges',
        }),

        options: $.extend({}, BaseModal.prototype.options, {
            modalName: 'preview-lib-changes',
            modalSize: 'lg',
            view: 'studio_view',
            viewSpecificClasses: 'modal-lib-preview confirm',
            // Translators: "title" is the name of the current component being edited.
            titleFormat: gettext('Preview changes to: {title}'),
            addPrimaryActionButton: false,
        }),

        initialize: function() {
            BaseModal.prototype.initialize.call(this);
        },

        /**
         * Adds the action buttons to the modal.
         */
        addActionButtons: function() {
            this.addActionButton('accept', gettext('Accept changes'), true);
            this.addActionButton('ignore', gettext('Ignore changes'));
            this.addActionButton('cancel', gettext('Cancel'));
        },

        /**
         * Show an edit modal for the specified xblock
         * @param xblockInfo The XBlockInfo model that describes the xblock.
         * @param courseAuthoringMfeUrl The course authoring mfe url.
         * @param upstreamBlockId The library block id.
         * @param upstreamBlockVersionSynced The library block current version.
         * @param refreshFunction A function to refresh the block after it has been updated
         */
        showPreviewFor: function(
          xblockInfo,
          courseAuthoringMfeUrl,
          upstreamBlockId,
          upstreamBlockVersionSynced,
          refreshFunction
        ) {
            this.xblockInfo = xblockInfo;
            this.courseAuthoringMfeUrl = courseAuthoringMfeUrl;
            this.downstreamBlockId = this.xblockInfo.get('id');
            this.upstreamBlockId = upstreamBlockId;
            this.upstreamBlockVersionSynced = upstreamBlockVersionSynced;
            this.refreshFunction = refreshFunction;

            this.render();
            this.show();
        },

        getContentHtml: function() {
            return `
                <iframe src="${this.courseAuthoringMfeUrl}/legacy/preview-changes/${this.upstreamBlockId}?old=${this.upstreamBlockVersionSynced}&downstream_block_id=${this.downstreamBlockId}">
            `;
        },

        getTitle: function() {
            var displayName = this.xblockInfo.get('display_name');
            if (!displayName) {
                if (this.xblockInfo.isVertical()) {
                    displayName = gettext('Unit');
                } else {
                    displayName = gettext('Component');
                }
            }
            return edx.StringUtils.interpolate(
                this.options.titleFormat, {
                    title: displayName
                }
            );
        },

        acceptChanges: function(event) {
            event.preventDefault();
            $.post(`/api/contentstore/v2/downstreams/${this.downstreamBlockId}/sync`).done(() => {
                this.hide();
                this.refreshFunction();
            }); // Note: if this POST request fails, Studio will display an error toast automatically.
        },

        ignoreChanges: function(event) {
            event.preventDefault();
            ViewUtils.confirmThenRunOperation(
                gettext('Ignore these changes?'),
                gettext('Would you like to permanently ignore this updated version? If so, you won\'t be able to update this until a newer version is published (in the library).'),
                gettext('Ignore'),
                () => {
                    $.ajax({
                        type: 'DELETE',
                        url: `/api/contentstore/v2/downstreams/${this.downstreamBlockId}/sync`,
                        data: {},
                    }).done(() => {
                        this.hide();
                        this.refreshFunction();
                    }); // Note: if this DELETE request fails, Studio will display an error toast automatically.
                }
            );
        },
    });

    return PreviewLibraryChangesModal;
});
