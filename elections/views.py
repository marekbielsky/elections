import secrets
from django.contrib.auth import get_user_model, login as auth_login, logout as auth_logout
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    CastVoteForm,
    ElectionCandidateCreateForm,
    ElectionCreateForm,
    ElectionLifecycleActionForm,
    RolePermissionAssignmentForm,
    UserLoginForm,
    UserRegistrationForm,
    UserRoleAssignmentForm,
)
from .models import (
    Election,
    ElectionCandidate,
    ElectionResult,
    ElectionType,
    OrganizationalUnit,
    Permission,
    Person,
    Role,
    RolePermission,
    UserRole,
    VotingToken,
)
from .rbac import PermissionCodes, require_permission, wants_json_response
from .services import ElectionLifecycleError, ElectionLifecycleService, VotingError, VotingService


def healthz_view(request):
    return HttpResponse("ok")
def _ensure_person_profile(user):
    if not user:
        return None
    profile = Person.objects.filter(user=user).first()
    if profile:
        return profile
    base_identifier = f"AUTO-{user.id}"
    identifier = base_identifier
    suffix = 1
    while Person.objects.filter(student_or_employee_no=identifier).exists():
        suffix += 1
        identifier = f"{base_identifier}-{suffix}"
    return Person.objects.create(
        user=user,
        first_name=(user.first_name or user.username or "Użytkownik")[:100],
        last_name=(user.last_name or "Systemowy")[:150],
        student_or_employee_no=identifier,
    )


def register_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            UserRole.objects.get_or_create(user=user, defaults={"role": UserRole.Role.USER})
            _ensure_person_profile(user)
            auth_login(request, user)
            return redirect("home")
    else:
        form = UserRegistrationForm()
    return render(request, "elections/auth/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = UserLoginForm(request=request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        auth_login(request, user)
        UserRole.objects.get_or_create(user=user, defaults={"role": UserRole.Role.USER})
        _ensure_person_profile(user)
        return redirect("home")
    return render(request, "elections/auth/login.html", {"form": form})


def logout_view(request):
    if request.method == "POST":
        auth_logout(request)
    return redirect("home")


def home_view(request):
    return render(request, "elections/home.html")


def candidates_list_view(request):
    elections = Election.objects.order_by("name")
    selected_election_id = request.GET.get("election_id", "").strip()
    candidates_queryset = ElectionCandidate.objects.select_related(
        "person",
        "election",
    ).order_by("candidate_number", "person__last_name", "person__first_name")
    if not selected_election_id and elections.exists():
        selected_election_id = str(elections.first().id)

    if selected_election_id and selected_election_id != "all":
        try:
            candidates_queryset = candidates_queryset.filter(election_id=int(selected_election_id))
        except ValueError:
            selected_election_id = str(elections.first().id) if elections.exists() else ""
            if selected_election_id:
                candidates_queryset = candidates_queryset.filter(election_id=int(selected_election_id))

    context = {
        "candidates": candidates_queryset,
        "selected_election_id": selected_election_id,
        "elections": elections,
    }
    return render(request, "elections/candidates/list.html", context)


def committees_list_view(request):
    committees = OrganizationalUnit.objects.filter(is_active=True).all()

    context = {
        "committees": committees,
    }
    return render(request, "elections/committees/list.html", context)

def _build_calendar_context(*, request, elections_queryset):
    election_type_filter = request.GET.get("election_type", "").strip()
    organizational_unit_filter = request.GET.get("organizational_unit_id", "").strip()
    within_hours_filter = request.GET.get("within_hours", "72").strip() or "72"
    event_date_from_filter = request.GET.get("event_date_from", "").strip()
    event_date_to_filter = request.GET.get("event_date_to", "").strip()

    filtered_elections = elections_queryset
    if election_type_filter:
        filtered_elections = filtered_elections.filter(election_type__code=election_type_filter)

    calendar_error = None
    if organizational_unit_filter:
        try:
            organizational_unit_id = int(organizational_unit_filter)
            filtered_elections = filtered_elections.filter(organizational_unit_id=organizational_unit_id)
        except ValueError:
            calendar_error = "Jednostka organizacyjna musi być liczbą całkowitą."

    within_hours = 72
    try:
        within_hours = int(within_hours_filter)
        if within_hours < 1 or within_hours > 720:
            raise ValueError
    except ValueError:
        within_hours = 72
        calendar_error = "Zakres przypomnień musi być liczbą od 1 do 720 godzin."

    now = timezone.now()
    horizon = now + timezone.timedelta(hours=within_hours)
    calendar_events = []
    reminders = []
    event_date_from = None
    event_date_to = None
    if not calendar_error:
        try:
            if event_date_from_filter:
                event_date_from = timezone.datetime.strptime(event_date_from_filter, "%Y-%m-%d").date()
            if event_date_to_filter:
                event_date_to = timezone.datetime.strptime(event_date_to_filter, "%Y-%m-%d").date()
            if event_date_from and event_date_to and event_date_from > event_date_to:
                calendar_error = "Data początkowa filtra nie może być późniejsza niż data końcowa."
        except ValueError:
            calendar_error = "Nieprawidłowy format daty filtra. Użyj formatu RRRR-MM-DD."

    if not calendar_error:
        for election in filtered_elections:
            if not hasattr(election, "schedule"):
                continue
            schedule = election.schedule
            events_for_election = [
                ("ELECTION_START", schedule.start_at),
                ("ELECTION_END", schedule.end_at),
            ]
            if schedule.results_publish_at:
                events_for_election.append(("RESULTS_PUBLISH", schedule.results_publish_at))

            for event_type, event_at in events_for_election:
                event_date = event_at.date()
                if event_date_from and event_date < event_date_from:
                    continue
                if event_date_to and event_date > event_date_to:
                    continue
                event = {
                    "event_type": event_type,
                    "event_at": event_at,
                    "election_name": election.name,
                    "election_type": election.election_type.code,
                    "organizational_unit_name": (
                        election.organizational_unit.name if election.organizational_unit else "Brak"
                    ),
                }
                calendar_events.append(event)
                if now < event_at <= horizon:
                    reminders.append(
                        {
                            **event,
                            "hours_until_event": round((event_at - now).total_seconds() / 3600, 2),
                        }
                    )

        calendar_events.sort(key=lambda item: (item["event_at"], item["election_name"], item["event_type"]))
        reminders.sort(key=lambda item: (item["event_at"], item["election_name"], item["event_type"]))

    return {
        "calendar_events": calendar_events,
        "reminders": reminders,
        "calendar_error": calendar_error,
        "election_type_filter": election_type_filter,
        "organizational_unit_filter": organizational_unit_filter,
        "within_hours_filter": within_hours,
        "event_date_from_filter": event_date_from_filter,
        "event_date_to_filter": event_date_to_filter,
        "election_types": ElectionType.objects.order_by("name"),
        "organizational_units": OrganizationalUnit.objects.filter(is_active=True).order_by("name"),
    }


def elections_list_view(request):
    ElectionLifecycleService.close_overdue_elections()
    elections = Election.objects.select_related(
        "election_type",
        "election_status",
        "organizational_unit",
        "schedule",
    ).all()

    context = {
        "elections": elections,
    }
    return render(request, "elections/elections/list.html", context)


def calendar_view(request):
    elections = Election.objects.select_related(
        "election_type",
        "election_status",
        "organizational_unit",
        "schedule",
    ).all()
    context = _build_calendar_context(request=request, elections_queryset=elections)
    return render(request, "elections/calendar/index.html", context)

def results_list_view(request):
    ElectionLifecycleService.close_overdue_elections()
    results = ElectionResult.objects.select_related(
        "election",
        "generated_by_user",
    ).all()

    context = {
        "results": results,
    }
    return render(request, "elections/results/list.html", context)


@require_permission(PermissionCodes.VOTING_CAST)
def vote_cast_view(request):
    person = None
    profile_error_message = None
    if request.user.is_authenticated:
        try:
            person = request.user.person_profile
        except Exception:
            profile_error_message = "Twoje konto nie ma przypisanego profilu osoby i nie może oddać głosu."
    success_message = None
    if request.method == "POST":
        form = CastVoteForm(request.POST, person=person)
        if form.is_valid():
            if person is None:
                form.add_error(None, profile_error_message)
            else:
                election = form.cleaned_data["election"]
                candidate_ids = [int(candidate_id) for candidate_id in form.cleaned_data.get("candidate_ids", [])]
                try:
                    token = VotingToken.objects.filter(election=election, person=person).first()
                    if token and token.is_used and not election.voting_rule.allow_vote_change:
                        raise VotingError("Głos został już oddany i nie można go zmienić.")
                    raw_token = secrets.token_urlsafe(24)
                    VotingService.issue_token(
                        election=election,
                        person=person,
                        raw_token=raw_token,
                    )
                    result = VotingService.cast_vote(
                        election=election,
                        person=person,
                        raw_token=raw_token,
                        candidate_ids=candidate_ids,
                        anonymous_key=form.cleaned_data.get("anonymous_key") or None,
                    )
                    success_message = (
                        f"Głos został zapisany poprawnie (ID karty: {result.ballot_id})."
                    )
                    form = CastVoteForm(initial={"election": election.id}, person=person)
                except VotingError as exc:
                    form.add_error(None, str(exc))
    else:
        form = CastVoteForm(person=person)

    context = {
        "form": form,
        "success_message": success_message,
        "profile_error_message": profile_error_message if person is None else None,
    }
    return render(request, "elections/voting/cast.html", context)


@require_permission(PermissionCodes.ELECTION_CREATE)
def election_create_view(request):
    if request.method == "POST":
        form = ElectionCreateForm(request.POST)
        if form.is_valid():
            try:
                election = form.save(
                    created_by_user=request.user if request.user.is_authenticated else None,
                )
                form = ElectionCreateForm()
                context = {
                    "form": form,
                    "success_message": f'Dodano wybory: "{election.name}".',
                }
                return render(request, "elections/elections/create.html", context)
            except ElectionLifecycleError as exc:
                form.add_error(None, str(exc))
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
