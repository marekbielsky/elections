from django.urls import path
from . import views
from .api_views import (
    CastVoteApiView,
    ElectionCloseApiView,
    ElectionCreateApiView,
    ElectionDetailApiView,
    ElectionPublishApiView,
    ElectionResultsApiView,
    ElectionStartApiView,
    IssueVotingTokenApiView,
)

urlpatterns = [
    path("", views.home_view, name="home"),
    path("candidates/", views.candidates_list_view, name="candidates_list"),
    path("candidates/create/", views.candidate_create_view, name="candidate_create"),
    path("committees/", views.committees_list_view, name="committees_list"),
    path("elections/", views.elections_list_view, name="elections_list"),
    path("elections/create/", views.election_create_view, name="election_create"),
    path("results/", views.results_list_view, name="results_list"),
    path(
        "management/admin/overview/",
        views.admin_overview_view,
        name="admin_overview",
    ),
    path(
        "management/admin/users/",
        views.admin_users_roles_view,
        name="admin_users_roles",
    ),
    path(
        "management/admin/users/assign-role/",
        views.admin_user_role_assign_view,
        name="admin_user_role_assign",
    ),
    path(
        "management/admin/role-permissions/",
        views.admin_role_permissions_view,
        name="admin_role_permissions",
    ),
    path(
        "management/admin/role-permissions/assign/",
        views.admin_role_permission_assign_view,
        name="admin_role_permission_assign",
    ),
    path(
        "management/admin/draft-elections/",
        views.admin_draft_elections_view,
        name="admin_draft_elections",
    ),
    path(
        "management/admin/draft-elections/action/",
        views.admin_election_lifecycle_action_view,
        name="admin_election_lifecycle_action",
    ),
    path("api/elections/", ElectionCreateApiView.as_view(), name="api_election_create"),
    path("api/elections/<int:election_id>/", ElectionDetailApiView.as_view(), name="api_election_detail"),
    path(
        "api/elections/<int:election_id>/results/",
        ElectionResultsApiView.as_view(),
        name="api_election_results",
    ),
    path(
        "api/elections/<int:election_id>/publish/",
        ElectionPublishApiView.as_view(),
        name="api_election_publish",
    ),
    path(
        "api/elections/<int:election_id>/start/",
        ElectionStartApiView.as_view(),
        name="api_election_start",
    ),
    path(
        "api/elections/<int:election_id>/close/",
        ElectionCloseApiView.as_view(),
        name="api_election_close",
    ),
    path(
        "api/elections/<int:election_id>/tokens/issue/",
        IssueVotingTokenApiView.as_view(),
        name="api_issue_voting_token",
    ),
    path(
        "api/elections/<int:election_id>/vote/",
        CastVoteApiView.as_view(),
        name="api_cast_vote",
    ),
]