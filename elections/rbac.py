from __future__ import annotations

from functools import wraps

from django.http import HttpRequest
from django.http import JsonResponse
from django.shortcuts import render
from rest_framework.permissions import BasePermission

from .models import Role, UserRole


class PermissionCodes:
    ELECTION_READ = "election.read"
    ELECTION_CREATE = "election.create"
    ELECTION_MANAGE_STATE = "election.manage_state"
    ELECTION_DRAFT_VIEW = "election.draft.view"
    CANDIDATE_READ = "candidate.read"
    CANDIDATE_CREATE = "candidate.create"
    COMMITTEE_READ = "committee.read"
    RESULT_READ = "result.read"
    VOTING_TOKEN_ISSUE = "voting.token.issue"
    VOTING_CAST = "voting.cast"
    ADMIN_PANEL_VIEW = "admin.panel.view"
    ADMIN_USERS_VIEW = "admin.users.view"
    ADMIN_ROLE_ASSIGN = "admin.role.assign"
    ADMIN_PERMISSION_ASSIGN = "admin.permission.assign"
    ADMIN_ELECTION_LIFECYCLE = "admin.election.lifecycle"


DEFAULT_PERMISSIONS_BY_ROLE: dict[str, set[str]] = {
    UserRole.Role.ADMIN: {
        PermissionCodes.ELECTION_READ,
        PermissionCodes.ELECTION_CREATE,
        PermissionCodes.ELECTION_MANAGE_STATE,
        PermissionCodes.ELECTION_DRAFT_VIEW,
        PermissionCodes.CANDIDATE_READ,
        PermissionCodes.CANDIDATE_CREATE,
        PermissionCodes.COMMITTEE_READ,
        PermissionCodes.RESULT_READ,
        PermissionCodes.VOTING_TOKEN_ISSUE,
        PermissionCodes.VOTING_CAST,
        PermissionCodes.ADMIN_PANEL_VIEW,
        PermissionCodes.ADMIN_USERS_VIEW,
        PermissionCodes.ADMIN_ROLE_ASSIGN,
        PermissionCodes.ADMIN_PERMISSION_ASSIGN,
        PermissionCodes.ADMIN_ELECTION_LIFECYCLE,
    },
    UserRole.Role.AUDITOR: {
        PermissionCodes.ELECTION_READ,
        PermissionCodes.CANDIDATE_READ,
        PermissionCodes.COMMITTEE_READ,
        PermissionCodes.RESULT_READ,
        PermissionCodes.ELECTION_DRAFT_VIEW,
        PermissionCodes.ADMIN_PANEL_VIEW,
    },
    UserRole.Role.USER: {
        PermissionCodes.ELECTION_READ,
        PermissionCodes.CANDIDATE_READ,
        PermissionCodes.COMMITTEE_READ,
        PermissionCodes.RESULT_READ,
        PermissionCodes.VOTING_CAST,
    },
}


def wants_json_response(request: HttpRequest) -> bool:
    if request.GET.get("format", "").strip().lower() == "json":
        return True
    return "application/json" in request.headers.get("Accept", "").lower()


def _resolve_role_code(request: HttpRequest) -> tuple[str, str]:

    if request.user.is_authenticated:
        role_profile = UserRole.objects.filter(user=request.user).first()
        if role_profile:
            return role_profile.role, "auth_user"

    role_from_header = (
        request.headers.get("X-User-Role", "").strip().upper()
        or request.GET.get("as_role", "").strip().upper()
    )
    if role_from_header in UserRole.Role.values:
        return role_from_header, "header"

    return UserRole.Role.USER, "default"


def _permissions_for_role_code(role_code: str) -> set[str]:
    role = (
        Role.objects.filter(code=role_code)
        .prefetch_related("role_permissions__permission")
        .first()
    )
    if role:
        role_permissions = {
            role_permission.permission.code
            for role_permission in role.role_permissions.all()
        }
        if role_permissions:
            return role_permissions
    return DEFAULT_PERMISSIONS_BY_ROLE.get(role_code, set())


def has_permission(request: HttpRequest, permission_code: str) -> bool:
    role_code, role_source = _resolve_role_code(request)
    request.mvp_role = role_code
    request.mvp_role_source = role_source
    return permission_code in _permissions_for_role_code(role_code)


def forbidden_response(request: HttpRequest, permission_code: str):
    payload = {
        "detail": "Brak dostępu. Nie masz wymaganych uprawnień do tego zasobu.",
        "required_permission": permission_code,
        "current_role": getattr(request, "mvp_role", UserRole.Role.USER),
    }
    if wants_json_response(request):
        return JsonResponse(payload, status=403)
    return render(request, "elections/admin/forbidden.html", payload, status=403)


def require_permission(permission_code: str):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args, **kwargs):
            if not has_permission(request, permission_code):
                return forbidden_response(request, permission_code)
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


class RBACPermission(BasePermission):
    message = "Brak dostępu. Nie masz wymaganych uprawnień."

    def has_permission(self, request, view) -> bool:
        permission_code = getattr(view, "required_permission_code", None)
        if not permission_code:
            return True
        return has_permission(request, permission_code)

