from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Election,
    ElectionResult,
    ElectionStatus,
    ElectionType,
    OrganizationalUnit,
    Person,
)
from .rbac import PermissionCodes, RBACPermission
from .services import ElectionLifecycleError, ElectionLifecycleService, VotingError, VotingService


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


class ElectionResultsApiView(APIView):
    permission_classes = [RBACPermission]
    required_permission_code = PermissionCodes.RESULT_READ
    def get(self, request, election_id: int):
        election = get_object_or_404(Election, id=election_id)
        result = get_object_or_404(ElectionResult.objects.prefetch_related("items"), election=election)
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
            ElectionLifecycleService.close_election(election, force=serializer.validated_data["force"])
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
