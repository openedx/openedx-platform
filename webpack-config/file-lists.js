/* eslint-disable max-len */
var path = require('path');

module.exports = {
    namespacedRequire: [
        path.resolve(__dirname, '../common/static/common/js/components/views/feedback_notification.js'),
        path.resolve(__dirname, '../common/static/common/js/components/views/feedback_prompt.js'),
        path.resolve(__dirname, '../common/static/common/js/components/views/feedback.js'),
        path.resolve(__dirname, '../common/static/common/js/components/views/feedback_alert.js'),
        path.resolve(__dirname, '../common/static/common/js/components/views/paging_footer.js'),
        path.resolve(__dirname, '../cms/static/js/views/paging.js'),
        path.resolve(__dirname, '../common/static/common/js/components/utils/view_utils.js'),
        /xmodule\/js\/src/,
        path.resolve(__dirname, '../openedx/features/course_bookmarks/static/course_bookmarks/js/views/bookmark_button.js')
    ],

    // These files are used by RequireJS as well, so we can't remove
    // the instances of "text!some/file.underscore" (which webpack currently
    // processes twice). So instead we have webpack dynamically remove the `text!` prefix
    // until we can remove RequireJS from the system.
    textBangUnderscore: [
        path.resolve(__dirname, '../cms/static/js/certificates/views/certificate_details.js'),
        path.resolve(__dirname, '../cms/static/js/certificates/views/certificate_editor.js'),
        path.resolve(__dirname, '../cms/static/js/certificates/views/certificate_preview.js'),
        path.resolve(__dirname, '../cms/static/js/certificates/views/signatory_details.js'),
        path.resolve(__dirname, '../cms/static/js/certificates/views/signatory_editor.js'),
        path.resolve(__dirname, '../cms/static/js/views/assets.js'),
        path.resolve(__dirname, '../cms/static/js/views/experiment_group_edit.js'),
        path.resolve(__dirname, '../cms/static/js/views/license.js'),
        path.resolve(__dirname, '../cms/static/js/views/modals/move_xblock_modal.js'),
        path.resolve(__dirname, '../cms/static/js/views/move_xblock_breadcrumb.js'),
        path.resolve(__dirname, '../cms/static/js/views/move_xblock_list.js'),
        path.resolve(__dirname, '../cms/static/js/views/paging_header.js'),
        path.resolve(__dirname, '../common/static/common/js/components/views/feedback.js'),
        path.resolve(__dirname, '../common/static/common/js/components/views/paginated_view.js'),
        path.resolve(__dirname, '../common/static/common/js/components/views/paging_footer.js'),
        path.resolve(__dirname, '../common/static/common/js/components/views/paging_header.js'),
        path.resolve(__dirname, '../common/static/common/js/components/views/progress_circle_view.js'),
        path.resolve(__dirname, '../common/static/common/js/components/views/search_field.js'),
        path.resolve(__dirname, '../common/static/common/js/components/views/tabbed_view.js'),
        path.resolve(__dirname, '../lms/djangoapps/discussion/static/discussion/js/views/discussion_board_view.js'),
        path.resolve(__dirname, '../lms/djangoapps/discussion/static/discussion/js/views/discussion_fake_breadcrumbs.js'),
        path.resolve(__dirname, '../lms/djangoapps/discussion/static/discussion/js/views/discussion_search_view.js'),
        path.resolve(__dirname, '../lms/djangoapps/discussion/static/discussion/js/views/discussion_user_profile_view.js'),
        path.resolve(__dirname, '../lms/djangoapps/support/static/support/js/views/certificates.js'),
        path.resolve(__dirname, '../lms/djangoapps/support/static/support/js/views/enrollment_modal.js'),
        path.resolve(__dirname, '../lms/djangoapps/support/static/support/js/views/enrollment.js'),
        path.resolve(__dirname, '../lms/djangoapps/support/static/support/js/views/manage_user.js'),
        path.resolve(__dirname, '../lms/djangoapps/teams/static/teams/js/views/edit_team_members.js'),
        path.resolve(__dirname, '../lms/djangoapps/teams/static/teams/js/views/edit_team.js'),
        path.resolve(__dirname, '../lms/djangoapps/teams/static/teams/js/views/instructor_tools.js'),
        path.resolve(__dirname, '../lms/djangoapps/teams/static/teams/js/views/team_card.js'),
        path.resolve(__dirname, '../lms/djangoapps/teams/static/teams/js/views/team_profile_header_actions.js'),
        path.resolve(__dirname, '../lms/djangoapps/teams/static/teams/js/views/team_profile.js'),
        path.resolve(__dirname, '../lms/djangoapps/teams/static/teams/js/views/teams_tab.js'),
        path.resolve(__dirname, '../lms/djangoapps/teams/static/teams/js/views/topic_teams.js'),
        path.resolve(__dirname, '../lms/static/js/api_admin/views/catalog_preview.js'),
        path.resolve(__dirname, '../lms/static/js/components/card/views/card.js'),
        path.resolve(__dirname, '../lms/static/js/components/header/views/header.js'),
        path.resolve(__dirname, '../lms/static/js/financial-assistance/views/financial_assistance_form_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/certificate_list_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/certificate_status_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/collection_list_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/course_card_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/course_enroll_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/course_entitlement_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/expired_notification_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/explore_new_programs_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/program_card_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/program_details_sidebar_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/program_details_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/program_header_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/sidebar_view.js'),
        path.resolve(__dirname, '../lms/static/js/learner_dashboard/views/upgrade_message_view.js'),
        path.resolve(__dirname, '../lms/static/js/student_account/views/FormView.js'),
        path.resolve(__dirname, '../lms/static/js/student_account/views/LoginView.js'),
        path.resolve(__dirname, '../lms/static/js/student_account/views/RegisterView.js'),
        path.resolve(__dirname, '../lms/static/js/views/fields.js'),
        path.resolve(__dirname, '../lms/static/js/views/image_field.js'),
        path.resolve(__dirname, '../lms/static/js/views/message_banner.js'),
        path.resolve(__dirname, '../openedx/features/course_bookmarks/static/course_bookmarks/js/views/bookmarks_list.js'),
        path.resolve(__dirname, '../openedx/features/course_search/static/course_search/js/spec/course_search_spec.js'),
        path.resolve(
            __dirname,
            '../openedx/features/course_search/static/course_search/js/views/course_search_results_view.js'
        ),
        path.resolve(
            __dirname,
            '../openedx/features/course_search/static/course_search/js/views/dashboard_search_results_view.js'
        ),
        path.resolve(__dirname, '../openedx/features/course_search/static/course_search/js/views/search_results_view.js'),
        path.resolve(
            __dirname,
            '../openedx/features/learner_profile/static/learner_profile/js/views/learner_profile_fields.js'
        ),
        path.resolve(__dirname, '../openedx/features/learner_profile/static/learner_profile/js/views/section_two_tab.js'),
        path.resolve(__dirname, '../openedx/features/learner_profile/static/learner_profile/js/views/share_modal_view.js'),
        path.resolve(__dirname, '../node_modules/edx-ui-toolkit/src/js/dropdown-menu/dropdown-menu-view.js'),
        path.resolve(__dirname, '../node_modules/edx-ui-toolkit/src/js/breadcrumbs/breadcrumbs-view.js')
    ]
};
