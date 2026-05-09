from functools import wraps

from django.contrib.auth import get_user_model
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render

from .forms import ElectionCandidateCreateForm, ElectionCreateForm
from .models import Election, ElectionCandidate, ElectionResult, OrganizationalUnit, UserRole

def _wants_json_response(request) -> bool:
    if request.GET.get("format", "").strip().lower() == "json":
        return True
    return "application/json" in request.headers.get("Accept", "").lower()


def _resolve_request_role(request) -> tuple[str, str]:
    demo_username = (
        request.headers.get("X-Demo-User", "").strip()
        or request.GET.get("demo_user", "").strip()
    )
    if demo_username:
        role_profile = UserRole.objects.filter(user__username=demo_username).first()
        if role_profile:
            return role_profile.role, demo_username
    role_from_header = (
        request.headers.get("X-User-Role", "").strip().upper()
        or request.GET.get("as_role", "").strip().upper()
    )
    if role_from_header in UserRole.Role.values:
        return role_from_header, "header"

    return UserRole.Role.USER, "default"

def _forbidden_admin_response(request, current_role: str):
    payload = {
        "detail": "Brak dostępu. Ten endpoint jest dostępny tylko dla roli ADMIN.",
        "current_role": current_role,
        "required_role": UserRole.Role.ADMIN,
    }
    if _wants_json_response(request):
        return JsonResponse(payload, status=403)
    return render(
        request,
        "elections/admin/forbidden.html",
        payload,
        status=403,
    )


def require_roles(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            role, role_source = _resolve_request_role(request)
            request.mvp_role = role
            request.mvp_role_source = role_source
            if role not in allowed_roles:
                return _forbidden_admin_response(request, role)
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator

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


@require_roles(UserRole.Role.ADMIN)
def admin_overview_view(request):
    user_model = get_user_model()
    summary = {
        "users_count": user_model.objects.count(),
        "elections_count": Election.objects.count(),
        "active_committees_count": OrganizationalUnit.objects.filter(is_active=True).count(),
        "candidates_count": ElectionCandidate.objects.count(),
    }
    context = {
        "actor_role": request.mvp_role,
        "actor_source": request.mvp_role_source,
        "summary": summary,
        "demo_user": request.GET.get("demo_user", "").strip(),
    }
    if _wants_json_response(request):
        return JsonResponse(
            {
                "actor_role": context["actor_role"],
                "actor_source": context["actor_source"],
                "summary": context["summary"],
            }
        )
    return render(request, "elections/admin/overview.html", context)


@require_roles(UserRole.Role.ADMIN)
def admin_users_roles_view(request):
    users_with_roles = UserRole.objects.select_related("user").order_by("user__username")
    data = [
        {
            "username": role_profile.user.username,
            "email": role_profile.user.email,
            "role": role_profile.role,
        }
        for role_profile in users_with_roles
    ]
    context = {
        "actor_role": request.mvp_role,
        "actor_source": request.mvp_role_source,
        "users": data,
        "demo_user": request.GET.get("demo_user", "").strip(),
    }
    if _wants_json_response(request):
        return JsonResponse(
            {
                "actor_role": context["actor_role"],
                "users": context["users"],
            }
        )
    return render(request, "elections/admin/users_roles.html", context)


@require_roles(UserRole.Role.ADMIN)
def admin_draft_elections_view(request):
    drafts = (
        Election.objects.select_related("election_type", "election_status")
        .filter(election_status__code="DRAFT")
        .order_by("name")
    )
    data = [
        {
            "id": election.id,
            "name": election.name,
            "election_type": election.election_type.code,
            "status": election.election_status.code,
        }
        for election in drafts
    ]
    context = {
        "actor_role": request.mvp_role,
        "actor_source": request.mvp_role_source,
        "draft_elections": data,
        "demo_user": request.GET.get("demo_user", "").strip(),
    }
    if _wants_json_response(request):
        return JsonResponse(
            {
                "actor_role": context["actor_role"],
                "draft_elections": context["draft_elections"],
            }
        )
    return render(request, "elections/admin/draft_elections.html", context)
