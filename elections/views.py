from django.contrib.auth import get_user_model
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    ElectionCandidateCreateForm,
    ElectionCreateForm,
    ElectionLifecycleActionForm,
    RolePermissionAssignmentForm,
    UserRoleAssignmentForm,
)
from .models import (
    Election,
    ElectionCandidate,
    ElectionResult,
    OrganizationalUnit,
    Permission,
    Role,
    RolePermission,
    UserRole,
)
from .rbac import PermissionCodes, require_permission, wants_json_response
from .services import ElectionLifecycleError, ElectionLifecycleService


def healthz_view(request):
    return HttpResponse("ok")


def home_view(request):
    return render(request, "elections/home.html")


def candidates_list_view(request):
    candidates = ElectionCandidate.objects.select_related(
        "person",
        "election",
    ).all()

    context = {
        "candidates": candidates,
    }
    return render(request, "elections/candidates/list.html", context)


def committees_list_view(request):
    committees = OrganizationalUnit.objects.filter(is_active=True).all()

    context = {
        "committees": committees,
    }
    return render(request, "elections/committees/list.html", context)


def elections_list_view(request):
    elections = Election.objects.select_related(
        "election_type",
        "election_status",
        "organizational_unit",
    ).all()

    context = {
        "elections": elections,
    }
    return render(request, "elections/elections/list.html", context)


def results_list_view(request):
    results = ElectionResult.objects.select_related(
        "election",
        "generated_by_user",
    ).all()

    context = {
        "results": results,
    }
    return render(request, "elections/results/list.html", context)


@require_permission(PermissionCodes.ELECTION_CREATE)
def election_create_view(request):
    if request.method == "POST":
        form = ElectionCreateForm(request.POST)
        if form.is_valid():
            election = form.save()
            form = ElectionCreateForm()
            context = {
                "form": form,
                "success_message": f'Dodano wybory: "{election.name}".',
            }
            return render(request, "elections/elections/create.html", context)
    else:
        form = ElectionCreateForm()

    context = {
        "form": form,
    }
    return render(request, "elections/elections/create.html", context)


@require_permission(PermissionCodes.CANDIDATE_CREATE)
def candidate_create_view(request):
    if request.method == "POST":
        form = ElectionCandidateCreateForm(request.POST)
        if form.is_valid():
            candidate = form.save()
            form = ElectionCandidateCreateForm()
            context = {
                "form": form,
                "success_message": (
                    f'Dodano kandydaturę: {candidate.person} '
                    f'(wybory: "{candidate.election.name}").'
                ),
            }
            return render(request, "elections/candidates/create.html", context)
    else:
        form = ElectionCandidateCreateForm()

    context = {
        "form": form,
    }
    return render(request, "elections/candidates/create.html", context)


@require_permission(PermissionCodes.ADMIN_PANEL_VIEW)
def admin_overview_view(request):
    user_model = get_user_model()
    summary = {
        "users_count": user_model.objects.count(),
        "elections_count": Election.objects.count(),
        "active_committees_count": OrganizationalUnit.objects.filter(is_active=True).count(),
        "candidates_count": ElectionCandidate.objects.count(),
    }
    context = {"summary": summary}
    if wants_json_response(request):
        return JsonResponse({"summary": context["summary"]})
    return render(request, "elections/admin/overview.html", context)


@require_permission(PermissionCodes.ADMIN_USERS_VIEW)
def admin_users_roles_view(request):
    user_model = get_user_model()
    users = user_model.objects.order_by("username")
    role_profiles = {
        role_profile.user_id: role_profile.role
        for role_profile in UserRole.objects.filter(user__in=users)
    }
    data = []
    for user in users:
        role_value = role_profiles.get(user.id, UserRole.Role.USER)
        data.append(
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "role": role_value,
            }
        )
    context = {"users": data}
    if wants_json_response(request):
        return JsonResponse({"users": context["users"]})
    return render(request, "elections/admin/users_roles.html", context)


@require_permission(PermissionCodes.ADMIN_ROLE_ASSIGN)
def admin_user_role_assign_view(request):
    if request.method != "POST":
        return redirect("admin_users_roles")

    form = UserRoleAssignmentForm(request.POST)
    if not form.is_valid():
        if wants_json_response(request):
            return JsonResponse({"errors": form.errors}, status=400)
        return redirect("admin_users_roles")

    user_model = get_user_model()
    user = get_object_or_404(user_model, id=form.cleaned_data["user_id"])
    role_profile, _ = UserRole.objects.update_or_create(
        user=user,
        defaults={"role": form.cleaned_data["role"]},
    )
    if wants_json_response(request):
        return JsonResponse(
            {
                "status": "ok",
                "user_id": user.id,
                "username": user.username,
                "role": role_profile.role,
            }
        )
    return redirect("admin_users_roles")


@require_permission(PermissionCodes.ADMIN_USERS_VIEW)
def admin_role_permissions_view(request):
    roles = Role.objects.prefetch_related("role_permissions__permission").order_by("code")
    permissions = Permission.objects.order_by("code")
    role_permission_rows = [
        {
            "role_id": role.id,
            "role_code": role.code,
            "permission_codes": sorted(
                role_permission.permission.code for role_permission in role.role_permissions.all()
            ),
        }
        for role in roles
    ]
    context = {
        "roles": roles,
        "permissions": permissions,
        "role_permission_rows": role_permission_rows,
        "assignment_form": RolePermissionAssignmentForm(),
    }
    if wants_json_response(request):
        return JsonResponse(
            {
                "roles": [
                    {
                        "id": row["role_id"],
                        "code": row["role_code"],
                        "permissions": row["permission_codes"],
                    }
                    for row in role_permission_rows
                ],
            }
        )
    return render(request, "elections/admin/role_permissions.html", context)


@require_permission(PermissionCodes.ADMIN_PERMISSION_ASSIGN)
def admin_role_permission_assign_view(request):
    if request.method != "POST":
        return redirect("admin_role_permissions")

    form = RolePermissionAssignmentForm(request.POST)
    if not form.is_valid():
        if wants_json_response(request):
            return JsonResponse({"errors": form.errors}, status=400)
        return redirect("admin_role_permissions")

    role = form.cleaned_data["role_id"]
    permission = form.cleaned_data["permission_id"]
    grant = form.cleaned_data["grant"]

    if grant:
        _, created = RolePermission.objects.get_or_create(
            role=role,
            permission=permission,
            defaults={"granted_by_user": request.user if request.user.is_authenticated else None},
        )
        action = "granted"
    else:
        deleted_count, _ = RolePermission.objects.filter(
            role=role,
            permission=permission,
        ).delete()
        created = False
        action = "revoked" if deleted_count else "noop"

    if wants_json_response(request):
        return JsonResponse(
            {
                "status": "ok",
                "action": action,
                "role": role.code,
                "permission": permission.code,
                "created": created,
            }
        )
    return redirect("admin_role_permissions")


@require_permission(PermissionCodes.ELECTION_DRAFT_VIEW)
def admin_draft_elections_view(request):
    drafts = (
        Election.objects.select_related("election_type", "election_status", "schedule")
        .filter(election_status__code__in=["DRAFT", "PUBLISHED", "IN_PROGRESS"])
        .order_by("name")
    )
    data = [
        {
            "id": election.id,
            "name": election.name,
            "election_type": election.election_type.code,
            "status": election.election_status.code,
            "schedule_start": election.schedule.start_at if hasattr(election, "schedule") else None,
            "schedule_end": election.schedule.end_at if hasattr(election, "schedule") else None,
        }
        for election in drafts
    ]
    context = {"draft_elections": data}
    if wants_json_response(request):
        return JsonResponse({"draft_elections": context["draft_elections"]})
    return render(request, "elections/admin/draft_elections.html", context)


@require_permission(PermissionCodes.ADMIN_ELECTION_LIFECYCLE)
def admin_election_lifecycle_action_view(request):
    if request.method != "POST":
        return redirect("admin_draft_elections")

    form = ElectionLifecycleActionForm(request.POST)
    if not form.is_valid():
        if wants_json_response(request):
            return JsonResponse({"errors": form.errors}, status=400)
        return redirect("admin_draft_elections")

    election = get_object_or_404(
        Election.objects.select_related("election_status"),
        id=form.cleaned_data["election_id"],
    )
    action = form.cleaned_data["action"]
    force_close = form.cleaned_data.get("force_close", False)

    try:
        if action == "publish":
            election = ElectionLifecycleService.publish_election(election)
        elif action == "start":
            election = ElectionLifecycleService.start_election(election)
        elif action == "close":
            election = ElectionLifecycleService.close_election(
                election,
                force=force_close,
                generated_by_user=request.user if request.user.is_authenticated else None,
            )
        else:
            if wants_json_response(request):
                return JsonResponse({"detail": "Unsupported action."}, status=400)
            return redirect("admin_draft_elections")
    except ElectionLifecycleError as exc:
        if wants_json_response(request):
            return JsonResponse({"detail": str(exc)}, status=400)
        return redirect("admin_draft_elections")

    if wants_json_response(request):
        return JsonResponse(
            {
                "status": "ok",
                "election_id": election.id,
                "action": action,
                "new_status": election.election_status.code,
            }
        )
    return redirect("admin_draft_elections")
