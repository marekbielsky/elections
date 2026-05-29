from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.http import FileResponse
from django.db.models import Count, Q
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Election,
    ElectionCandidate,
    ElectionResult,
    ElectionStatus,
    ElectionType,
    GeneratedDocument,
    OrganizationalUnit,
    Person,
    UserRole,
    VotingEligibility,
    VotingParticipation,
)
from .rbac import PermissionCodes, RBACPermission
from .services import (
    ElectionLifecycleError,
    ElectionLifecycleService,
    ElectionResultDocumentService,
    ElectionResultService,
    VotingError,
    VotingService,
)

PRIVILEGED_DASHBOARD_ROLES = {UserRole.Role.ADMIN, UserRole.Role.AUDITOR}


def _is_public_dashboard_role(request) -> bool:
    return getattr(request, "mvp_role", UserRole.Role.USER) not in PRIVILEGED_DASHBOARD_ROLES

def _calendar_events_from_elections(elections) -> list[dict]:
    events = []
    for election in elections:
        schedule = getattr(election, "schedule", None)
        if schedule is None:
            continue
        events.append(
            {
                "event_type": "ELECTION_START",
                "event_at": schedule.start_at,
                "election_id": election.id,
                "election_name": election.name,
                "election_type": election.election_type.code,
                "election_status": election.election_status.code,
                "organizational_unit_id": election.organizational_unit_id,
            }
        )
        events.append(
            {
                "event_type": "ELECTION_END",
                "event_at": schedule.end_at,
                "election_id": election.id,
                "election_name": election.name,
                "election_type": election.election_type.code,
                "election_status": election.election_status.code,
                "organizational_unit_id": election.organizational_unit_id,
            }
        )
        if schedule.results_publish_at:
            events.append(
                {
                    "event_type": "RESULTS_PUBLISH",
                    "event_at": schedule.results_publish_at,
                    "election_id": election.id,
                    "election_name": election.name,
                    "election_type": election.election_type.code,
                    "election_status": election.election_status.code,
                    "organizational_unit_id": election.organizational_unit_id,
                }
            )
    events.sort(key=lambda item: (item["event_at"], item["election_id"], item["event_type"]))
    return events


class ElectionCreateRequestSerializer(serializers.Serializer):
    election_type_id = serializers.IntegerField()
    election_status_code = serializers.CharField(default="DRAFT")
    organizational_unit_id = serializers.IntegerField(required=False, allow_null=True)
    created_by_user_id = serializers.IntegerField(required=False, allow_null=True)
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    is_secret = serializers.BooleanField(default=True)
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    results_publish_at = serializers.DateTimeField(required=False, allow_null=True)
    min_choices = serializers.IntegerField(default=1, min_value=0)
    max_choices = serializers.IntegerField(default=1, min_value=1)
    allow_blank_vote = serializers.BooleanField(default=False)
    allow_vote_change = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if attrs["start_at"] >= attrs["end_at"]:
            raise serializers.ValidationError("start_at must be earlier than end_at.")
        if attrs["min_choices"] > attrs["max_choices"]:
            raise serializers.ValidationError("min_choices cannot be greater than max_choices.")
        return attrs


class IssueTokenRequestSerializer(serializers.Serializer):
    person_id = serializers.IntegerField()
    raw_token = serializers.CharField(max_length=120)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class CastVoteRequestSerializer(serializers.Serializer):
    person_id = serializers.IntegerField()
    raw_token = serializers.CharField(max_length=120)
    candidate_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True,
    )
    anonymous_key = serializers.CharField(max_length=120, required=False, allow_blank=True)
    ip_address = serializers.IPAddressField(required=False, allow_null=True)


class CloseElectionRequestSerializer(serializers.Serializer):
    force = serializers.BooleanField(default=False)


def _serialize_election(election: Election) -> dict:
    election = Election.objects.select_related(
        "election_type",
        "election_status",
        "organizational_unit",
        "schedule",
        "voting_rule",
    ).get(id=election.id)
    return {
        "id": election.id,
        "name": election.name,
        "description": election.description,
        "is_secret": election.is_secret,
        "status": election.election_status.code,
        "type": election.election_type.code,
        "organizational_unit_id": election.organizational_unit_id,
        "start_at": election.schedule.start_at,
        "end_at": election.schedule.end_at,
        "results_publish_at": election.schedule.results_publish_at,
        "voting_rule": {
            "min_choices": election.voting_rule.min_choices,
            "max_choices": election.voting_rule.max_choices,
            "allow_blank_vote": election.voting_rule.allow_blank_vote,
            "allow_vote_change": election.voting_rule.allow_vote_change,
        },
    }


class ElectionCreateApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.ELECTION_CREATE
    def post(self, request):
        serializer = ElectionCreateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        election_type = get_object_or_404(ElectionType, id=data["election_type_id"])
        election_status = get_object_or_404(ElectionStatus, code=data["election_status_code"])
        organizational_unit = None
        if data.get("organizational_unit_id") is not None:
            organizational_unit = get_object_or_404(OrganizationalUnit, id=data["organizational_unit_id"])
        created_by_user = None
        if data.get("created_by_user_id") is not None:
            created_by_user = get_object_or_404(get_user_model(), id=data["created_by_user_id"])

        try:
            election = ElectionLifecycleService.create_election_with_config(
                election_type=election_type,
                name=data["name"],
                election_status=election_status,
                start_at=data["start_at"],
                end_at=data["end_at"],
                created_by_user=created_by_user,
                organizational_unit=organizational_unit,
                description=data["description"],
                is_secret=data["is_secret"],
                min_choices=data["min_choices"],
                max_choices=data["max_choices"],
                allow_blank_vote=data["allow_blank_vote"],
                allow_vote_change=data["allow_vote_change"],
            )
        except ElectionLifecycleError as exc:
            raise serializers.ValidationError({"detail": str(exc)})
        if data.get("results_publish_at") is not None:
            election.schedule.results_publish_at = data["results_publish_at"]
            election.schedule.save(update_fields=["results_publish_at"])

        return Response(_serialize_election(election), status=status.HTTP_201_CREATED)


class ElectionDetailApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.ELECTION_READ
    def get(self, request, election_id: int):
        election = get_object_or_404(Election, id=election_id)
        return Response(_serialize_election(election), status=status.HTTP_200_OK)


class ElectionCalendarEventsApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.ELECTION_READ
    @staticmethod
    def _filtered_elections(request):
        elections = Election.objects.select_related(
            "schedule",
            "election_type",
            "election_status",
            "organizational_unit",
        ).all()
        election_type_code = request.query_params.get("election_type", "").strip()
        organizational_unit_id = request.query_params.get("organizational_unit_id", "").strip()
        if election_type_code:
            elections = elections.filter(election_type__code=election_type_code)
        if organizational_unit_id:
            try:
                organizational_unit_id_int = int(organizational_unit_id)
            except ValueError:
                return None, Response(
                    {"detail": "organizational_unit_id must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            elections = elections.filter(organizational_unit_id=organizational_unit_id_int)
        return elections, None

    def get(self, request):
        elections, error_response = self._filtered_elections(request)
        if error_response:
            return error_response
        events = _calendar_events_from_elections(elections)
        return Response({"events": events}, status=status.HTTP_200_OK)

class ElectionUpcomingRemindersApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.ELECTION_READ

    def get(self, request):
        within_hours = request.query_params.get("within_hours", "72")
        try:
            within_hours_int = int(within_hours)
        except (TypeError, ValueError):
            return Response(
                {"detail": "within_hours must be an integer between 1 and 720."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if within_hours_int < 1 or within_hours_int > 720:
            return Response(
                {"detail": "within_hours must be an integer between 1 and 720."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        elections, error_response = ElectionCalendarEventsApiView._filtered_elections(request)
        if error_response:
            return error_response

        now = timezone.now()
        horizon = now + timezone.timedelta(hours=within_hours_int)
        reminders = []
        for event in _calendar_events_from_elections(elections):
            if event["event_at"] <= now or event["event_at"] > horizon:
                continue
            remaining_seconds = (event["event_at"] - now).total_seconds()
            reminders.append(
                {
                    **event,
                    "hours_until_event": round(remaining_seconds / 3600, 2),
                }
            )
        return Response(
            {
                "generated_at": now,
                "within_hours": within_hours_int,
                "reminders": reminders,
            },
            status=status.HTTP_200_OK,
        )


class ElectionResultsApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.RESULT_READ
    def get(self, request, election_id: int):
        election = get_object_or_404(Election, id=election_id)
        if election.election_status.code != "CLOSED":
            return Response(
                {"detail": "Results are available only for closed elections."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if election.schedule.results_publish_at and timezone.now() < election.schedule.results_publish_at:
            return Response(
                {"detail": "Results are not published yet."},
                status=status.HTTP_403_FORBIDDEN,
            )
        result = ElectionResult.objects.filter(election=election).prefetch_related("items").first()
        if result is None:
            result = ElectionResultService.generate_results(election=election, is_final=True)
        payload = {
            "election_id": election.id,
            "calculated_at": result.calculated_at,
            "eligible_voters_count": result.eligible_voters_count,
            "voters_count": result.voters_count,
            "turnout_percent": str(result.turnout_percent),
            "is_final": result.is_final,
            "items": [
                {
                    "candidate_id": item.election_candidate_id,
                    "votes_count": item.votes_count,
                    "votes_percent": str(item.votes_percent),
                    "ranking_position": item.ranking_position,
                }
                for item in result.items.all().order_by("ranking_position")
            ],
        }
        return Response(payload, status=status.HTTP_200_OK)


class HistoricalTrendsApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.RESULT_READ

    def get(self, request):
        window = request.query_params.get("window", "12")
        try:
            window_int = int(window)
        except (TypeError, ValueError):
            return Response(
                {"detail": "window must be an integer between 1 and 120."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if window_int < 1 or window_int > 120:
            return Response(
                {"detail": "window must be an integer between 1 and 120."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        closed_published_elections = Election.objects.select_related("schedule").filter(
            election_status__code="CLOSED"
        ).filter(
            Q(schedule__results_publish_at__isnull=True) | Q(schedule__results_publish_at__lte=now)
        )
        for election in closed_published_elections.filter(result__isnull=True):
            ElectionResultService.generate_results(election=election, is_final=True)

        trend_results_desc = list(
            ElectionResult.objects.select_related(
                "election",
                "election__election_type",
                "election__schedule",
            )
            .filter(election__election_status__code="CLOSED")
            .filter(
                Q(election__schedule__results_publish_at__isnull=True)
                | Q(election__schedule__results_publish_at__lte=now)
            )
            .order_by("-election__schedule__end_at", "-election_id")[:window_int]
        )
        trend_results = list(reversed(trend_results_desc))

        turnout_values = [float(result.turnout_percent) for result in trend_results]
        average_turnout = sum(turnout_values) / len(turnout_values) if turnout_values else 0.0
        max_turnout = max(turnout_values) if turnout_values else 0.0
        min_turnout = min(turnout_values) if turnout_values else 0.0

        payload = {
            "window": window_int,
            "points": [
                {
                    "election_id": result.election_id,
                    "election_name": result.election.name,
                    "election_type": result.election.election_type.code,
                    "ended_at": result.election.schedule.end_at,
                    "turnout_percent": str(result.turnout_percent),
                    "eligible_voters_count": result.eligible_voters_count,
                    "voters_count": result.voters_count,
                }
                for result in trend_results
            ],
            "summary": {
                "points_count": len(trend_results),
                "average_turnout_percent": f"{average_turnout:.2f}",
                "max_turnout_percent": f"{max_turnout:.2f}",
                "min_turnout_percent": f"{min_turnout:.2f}",
            },
        }
        if _is_public_dashboard_role(request):
            payload = {
                "window": payload["window"],
                "points": [
                    {
                        "sequence": index,
                        "election_type": point["election_type"],
                        "turnout_percent": point["turnout_percent"],
                    }
                    for index, point in enumerate(payload["points"], start=1)
                ],
                "summary": payload["summary"],
            }
        return Response(payload, status=status.HTTP_200_OK)


class ElectionPublishApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.ELECTION_MANAGE_STATE
    def post(self, request, election_id: int):
        election = get_object_or_404(Election, id=election_id)
        try:
            ElectionLifecycleService.publish_election(election)
        except ElectionLifecycleError as exc:
            raise serializers.ValidationError({"detail": str(exc)})
        return Response(_serialize_election(election), status=status.HTTP_200_OK)


class ElectionStartApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.ELECTION_MANAGE_STATE
    def post(self, request, election_id: int):
        election = get_object_or_404(Election, id=election_id)
        try:
            ElectionLifecycleService.start_election(election)
        except ElectionLifecycleError as exc:
            raise serializers.ValidationError({"detail": str(exc)})
        return Response(_serialize_election(election), status=status.HTTP_200_OK)


class ElectionCloseApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.ELECTION_MANAGE_STATE
    def post(self, request, election_id: int):
        election = get_object_or_404(Election, id=election_id)
        serializer = CloseElectionRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            ElectionLifecycleService.close_election(
                election,
                force=serializer.validated_data["force"],
                generated_by_user=request.user if request.user.is_authenticated else None,
            )
        except ElectionLifecycleError as exc:
            raise serializers.ValidationError({"detail": str(exc)})
        return Response(_serialize_election(election), status=status.HTTP_200_OK)


class IssueVotingTokenApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.VOTING_TOKEN_ISSUE
    def post(self, request, election_id: int):
        election = get_object_or_404(Election, id=election_id)
        serializer = IssueTokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        person = get_object_or_404(Person, id=data["person_id"])
        try:
            token = VotingService.issue_token(
                election=election,
                person=person,
                raw_token=data["raw_token"],
                expires_at=data.get("expires_at"),
            )
        except VotingError as exc:
            raise serializers.ValidationError({"detail": str(exc)})
        return Response(
            {
                "token_id": token.id,
                "election_id": election.id,
                "person_id": person.id,
                "issued_at": token.issued_at,
                "expires_at": token.expires_at,
            },
            status=status.HTTP_201_CREATED,
        )


class CastVoteApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.VOTING_CAST
    def post(self, request, election_id: int):
        election = get_object_or_404(Election, id=election_id)
        serializer = CastVoteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        person = get_object_or_404(Person, id=data["person_id"])
        try:
            result = VotingService.cast_vote(
                election=election,
                person=person,
                raw_token=data["raw_token"],
                candidate_ids=data["candidate_ids"],
                anonymous_key=data.get("anonymous_key") or None,
                ip_address=data.get("ip_address"),
            )
        except VotingError as exc:
            raise serializers.ValidationError({"detail": str(exc)})
        return Response(
            {
                "ballot_id": result.ballot_id,
                "submitted_at": result.submitted_at,
                "selected_candidate_ids": result.selected_candidate_ids,
            },
            status=status.HTTP_201_CREATED,
        )


class ElectionResultPdfGenerateApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.RESULT_READ

    def post(self, request, election_id: int):
        election = get_object_or_404(Election.objects.select_related("schedule", "election_status"), id=election_id)
        if election.election_status.code != "CLOSED":
            return Response(
                {"detail": "Result PDF can be generated only for closed elections."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if election.schedule.results_publish_at and timezone.now() < election.schedule.results_publish_at:
            return Response(
                {"detail": "Results are not published yet."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = ElectionResultDocumentService.generate_result_pdf(
            election=election,
            generated_by_user=request.user if request.user.is_authenticated else None,
        )
        return Response(
            {
                "document_id": document.id,
                "document_type": document.document_type,
                "election_id": document.election_id,
                "file_name": document.stored_file.original_file_name,
                "file_size_bytes": document.stored_file.file_size_bytes,
            },
            status=status.HTTP_201_CREATED,
        )


class GeneratedDocumentDownloadApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.RESULT_READ

    def get(self, request, document_id: int):
        document = get_object_or_404(
            GeneratedDocument.objects.select_related("stored_file"),
            id=document_id,
        )
        file_handle = document.stored_file.file.open("rb")
        return FileResponse(
            file_handle,
            as_attachment=True,
            filename=document.stored_file.original_file_name,
            content_type=document.stored_file.mime_type or "application/octet-stream",
        )


class ElectionAnalyticsApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.RESULT_READ

    def get(self, request, election_id: int):
        election = get_object_or_404(Election.objects.select_related("schedule", "election_status"), id=election_id)
        if election.election_status.code != "CLOSED":
            return Response(
                {"detail": "Analytics are available only for closed elections."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if election.schedule.results_publish_at and timezone.now() < election.schedule.results_publish_at:
            return Response(
                {"detail": "Results are not published yet."},
                status=status.HTTP_403_FORBIDDEN,
            )

        eligible_total = (
            VotingEligibility.objects.filter(
                election=election,
                eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
            )
            .values("person_id")
            .distinct()
            .count()
        )
        voters_total = (
            VotingParticipation.objects.filter(election=election, has_voted=True)
            .values("person_id")
            .distinct()
            .count()
        )

        candidate_rows = list(
            ElectionCandidate.objects.filter(election=election, is_approved=True)
            .annotate(
                votes_count=Count(
                    "ballot_selections",
                    filter=Q(
                        ballot_selections__ballot__election=election,
                        ballot_selections__ballot__ballot_status="SUBMITTED",
                    ),
                )
            )
            .order_by("-votes_count", "candidate_number", "id")
        )
        total_votes_cast = sum(row.votes_count for row in candidate_rows)
        candidate_support = [
            {
                "candidate_id": row.id,
                "candidate_number": row.candidate_number,
                "candidate_name": f"{row.person.first_name} {row.person.last_name}",
                "votes_count": row.votes_count,
                "votes_percent": self._percent(row.votes_count, total_votes_cast),
            }
            for row in candidate_rows
        ]

        eligible_by_unit_rows = (
            VotingEligibility.objects.filter(
                election=election,
                eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
            )
            .values("person__organizational_unit_id", "person__organizational_unit__name")
            .annotate(eligible_count=Count("person_id", distinct=True))
        )
        voted_by_unit_rows = (
            VotingParticipation.objects.filter(election=election, has_voted=True)
            .values("person__organizational_unit_id", "person__organizational_unit__name")
            .annotate(voted_count=Count("person_id", distinct=True))
        )

        unit_metrics: dict[int | None, dict] = {}
        for row in eligible_by_unit_rows:
            unit_id = row["person__organizational_unit_id"]
            unit_metrics[unit_id] = {
                "unit_id": unit_id,
                "unit_name": row["person__organizational_unit__name"] or "Unassigned",
                "eligible_count": row["eligible_count"],
                "voted_count": 0,
            }
        for row in voted_by_unit_rows:
            unit_id = row["person__organizational_unit_id"]
            metric = unit_metrics.setdefault(
                unit_id,
                {
                    "unit_id": unit_id,
                    "unit_name": row["person__organizational_unit__name"] or "Unassigned",
                    "eligible_count": 0,
                    "voted_count": 0,
                },
            )
            metric["voted_count"] = row["voted_count"]

        turnout_by_unit = []
        for metric in sorted(unit_metrics.values(), key=lambda row: (row["unit_name"], row["unit_id"] or 0)):
            turnout_by_unit.append(
                {
                    **metric,
                    "turnout_percent": self._percent(metric["voted_count"], metric["eligible_count"]),
                }
            )

        payload = {
            "election_id": election.id,
            "kpi": {
                "eligible_voters_count": eligible_total,
                "voters_count": voters_total,
                "turnout_percent": self._percent(voters_total, eligible_total),
                "total_votes_cast": total_votes_cast,
            },
            "candidate_support": candidate_support,
            "turnout_by_unit": turnout_by_unit,
        }
        if _is_public_dashboard_role(request):
            payload = {
                "election_id": payload["election_id"],
                "kpi": {
                    "turnout_percent": payload["kpi"]["turnout_percent"],
                },
                "candidate_support": [
                    {
                        "candidate_number": row["candidate_number"],
                        "votes_percent": row["votes_percent"],
                    }
                    for row in payload["candidate_support"]
                ],
                "turnout_by_unit": [
                    {
                        "unit_label": f"Unit {index}",
                        "turnout_percent": row["turnout_percent"],
                    }
                    for index, row in enumerate(payload["turnout_by_unit"], start=1)
                ],
            }
        return Response(payload, status=status.HTTP_200_OK)

    @staticmethod
    def _percent(numerator: int, denominator: int) -> str:
        if denominator <= 0:
            return "0.00"
        return f"{(numerator * 100.0 / denominator):.2f}"


class TopTurnoutElectionsApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.RESULT_READ

    def get(self, request):
        top_n = request.query_params.get("top_n", "5")
        try:
            top_n_int = int(top_n)
        except (TypeError, ValueError):
            return Response(
                {"detail": "top_n must be an integer between 1 and 100."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if top_n_int < 1 or top_n_int > 100:
            return Response(
                {"detail": "top_n must be an integer between 1 and 100."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        closed_published_elections = Election.objects.select_related("schedule").filter(
            election_status__code="CLOSED"
        ).filter(
            Q(schedule__results_publish_at__isnull=True) | Q(schedule__results_publish_at__lte=now)
        )
        for election in closed_published_elections.filter(result__isnull=True):
            ElectionResultService.generate_results(election=election, is_final=True)

        ranked_results = ElectionResult.objects.select_related(
            "election",
            "election__election_type",
            "election__schedule",
        ).filter(
            election__election_status__code="CLOSED"
        ).filter(
            Q(election__schedule__results_publish_at__isnull=True)
            | Q(election__schedule__results_publish_at__lte=now)
        ).order_by(
            "-turnout_percent",
            "-voters_count",
            "election__name",
        )[:top_n_int]

        payload = {
            "top_n": top_n_int,
            "elections": [
                {
                    "rank": index,
                    "election_id": result.election_id,
                    "election_name": result.election.name,
                    "election_type": result.election.election_type.code,
                    "eligible_voters_count": result.eligible_voters_count,
                    "voters_count": result.voters_count,
                    "turnout_percent": str(result.turnout_percent),
                    "calculated_at": result.calculated_at,
                }
                for index, result in enumerate(ranked_results, start=1)
            ],
        }
        if _is_public_dashboard_role(request):
            payload = {
                "top_n": payload["top_n"],
                "elections": [
                    {
                        "rank": row["rank"],
                        "election_type": row["election_type"],
                        "turnout_percent": row["turnout_percent"],
                    }
                    for row in payload["elections"]
                ],
            }
        return Response(payload, status=status.HTTP_200_OK)
