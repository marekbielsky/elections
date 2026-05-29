import secrets
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass
from typing import Iterable

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import (
    Ballot,
    BallotSelection,
    ElectionCandidate,
    ElectionResult,
    ElectionResultItem,
    Election,
    ElectionSchedule,
    ElectionStatus,
    VotingEligibility,
    VotingParticipation,
    VotingRule,
    VotingToken,
)


class ElectionServiceError(Exception):
    pass


class ElectionLifecycleError(ElectionServiceError):
    pass


class VotingError(ElectionServiceError):
    pass


@dataclass(frozen=True)
class CastVoteResult:
    ballot_id: int
    submitted_at: object
    selected_candidate_ids: list[int]


class ElectionLifecycleService:
    @staticmethod
    def create_election_with_config(
        *,
        election_type,
        name: str,
        election_status,
        start_at,
        end_at,
        created_by_user=None,
        organizational_unit=None,
        description: str = "",
        is_secret: bool = True,
        min_choices: int = 1,
        max_choices: int = 1,
        allow_blank_vote: bool = False,
        allow_vote_change: bool = False,
    ) -> Election:
        with transaction.atomic():
            election = Election.objects.create(
                election_type=election_type,
                election_status=election_status,
                organizational_unit=organizational_unit,
                name=name,
                description=description,
                is_secret=is_secret,
                created_by_user=created_by_user,
            )
            ElectionSchedule.objects.create(
                election=election,
                start_at=start_at,
                end_at=end_at,
            )
            VotingRule.objects.create(
                election=election,
                min_choices=min_choices,
                max_choices=max_choices,
                allow_blank_vote=allow_blank_vote,
                allow_vote_change=allow_vote_change,
            )
            return election

    @staticmethod
    def publish_election(election: Election) -> Election:
        ElectionLifecycleService._ensure_status(election, allowed={"DRAFT"})
        election.election_status = ElectionLifecycleService._status_by_code("PUBLISHED")
        election.save(update_fields=["election_status", "updated_at"])
        return election

    @staticmethod
    def start_election(election: Election, *, at_time=None) -> Election:
        ElectionLifecycleService._ensure_status(election, allowed={"PUBLISHED"})
        now = at_time or timezone.now()
        schedule = election.schedule
        if now < schedule.start_at:
            raise ElectionLifecycleError("Election cannot be started before configured start time.")
        if now >= schedule.end_at:
            raise ElectionLifecycleError("Election cannot be started after configured end time.")
        election.election_status = ElectionLifecycleService._status_by_code("IN_PROGRESS")
        election.save(update_fields=["election_status", "updated_at"])
        return election

    @staticmethod
    def close_election(
        election: Election,
        *,
        at_time=None,
        force: bool = False,
        generated_by_user=None,
    ) -> Election:
        ElectionLifecycleService._ensure_status(election, allowed={"IN_PROGRESS", "PUBLISHED"})
        now = at_time or timezone.now()
        if not force and now < election.schedule.end_at:
            raise ElectionLifecycleError("Election cannot be closed before configured end time.")
        election.election_status = ElectionLifecycleService._status_by_code("CLOSED")
        election.save(update_fields=["election_status", "updated_at"])
        ElectionResultService.generate_results(
            election=election,
            generated_by_user=generated_by_user,
            is_final=True,
        )
        return election

    @staticmethod
    def _status_by_code(code: str) -> ElectionStatus:
        try:
            return ElectionStatus.objects.get(code=code)
        except ElectionStatus.DoesNotExist as exc:
            raise ElectionLifecycleError(f"Required election status '{code}' is not configured.") from exc

    @staticmethod
    def _ensure_status(election: Election, *, allowed: set[str]) -> None:
        current_status = election.election_status.code
        if current_status not in allowed:
            raise ElectionLifecycleError(
                f"Invalid election status transition from '{current_status}'. Allowed: {sorted(allowed)}."
            )


class VotingService:
    @staticmethod
    def issue_token(
        *,
        election: Election,
        person,
        raw_token: str | None = None,
        expires_at=None,
    ) -> VotingToken:
        eligibility = VotingEligibility.objects.filter(
            election=election,
            person=person,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        ).exists()
        if not eligibility:
            raise VotingError("Person is not eligible to vote in this election.")

        token_value = raw_token or secrets.token_urlsafe(24)
        token, _ = VotingToken.objects.update_or_create(
            election=election,
            person=person,
            defaults={
                "token_value": token_value,
                "expires_at": expires_at,
                "is_used": False,
                "used_at": None,
            },
        )
        return token

    @staticmethod
    def cast_vote(
        *,
        election: Election,
        person,
        raw_token: str,
        candidate_ids: Iterable[int],
        anonymous_key: str | None = None,
        ip_address: str | None = None,
        cast_at=None,
    ) -> CastVoteResult:
        now = cast_at or timezone.now()
        selected_ids = list(dict.fromkeys(candidate_ids))

        with transaction.atomic():
            election = (
                Election.objects.select_related("election_status", "schedule", "voting_rule")
                .select_for_update()
                .get(id=election.id)
            )
            VotingService._ensure_election_open_for_voting(election=election, at_time=now)
            VotingService._ensure_eligibility(election=election, person=person)
            voting_rule = election.voting_rule
            VotingService._validate_choice_count(selected_ids=selected_ids, voting_rule=voting_rule)

            token = (
                VotingToken.objects.select_for_update()
                .filter(election=election, person=person)
                .first()
            )
            if token is None:
                raise VotingError("Voting token not found for this voter.")
            if not token.verify_token_value(raw_token):
                raise VotingError("Provided voting token is invalid.")
            if token.expires_at and token.expires_at <= now:
                raise VotingError("Voting token has expired.")

            ballot = Ballot.objects.filter(voting_token=token).first()
            if token.is_used and not voting_rule.allow_vote_change:
                raise VotingError("Voting token has already been used.")
            if token.is_used and voting_rule.allow_vote_change and ballot is None:
                raise VotingError("Used token cannot be changed without an existing ballot.")

            candidate_ids_validated = VotingService._validate_candidates(
                election=election,
                selected_ids=selected_ids,
            )

            if ballot is None:
                ballot = Ballot.objects.create(
                    election=election,
                    voting_token=token,
                    anonymous_key=anonymous_key or secrets.token_urlsafe(24),
                )
            elif voting_rule.allow_vote_change:
                ballot.selections.all().delete()
                ballot.ballot_status = Ballot.BallotStatus.CREATED
                ballot.submitted_at = None
                ballot.save(update_fields=["ballot_status", "submitted_at"])
            else:
                raise VotingError("Vote has already been cast and vote change is disabled.")

            BallotSelection.objects.bulk_create(
                [
                    BallotSelection(
                        ballot=ballot,
                        election_candidate_id=candidate_id,
                        selection_order=index + 1,
                    )
                    for index, candidate_id in enumerate(candidate_ids_validated)
                ]
            )

            ballot.ballot_status = Ballot.BallotStatus.SUBMITTED
            ballot.submitted_at = None
            ballot.save(update_fields=["ballot_status", "submitted_at"])

            token.is_used = True
            token.save(update_fields=["is_used"])

            participation, created = VotingParticipation.objects.get_or_create(
                election=election,
                person=person,
                defaults={
                    "voting_token": token,
                    "has_voted": True,
                    "voted_at": now,
                    "ip_address": ip_address,
                },
            )
            if not created:
                participation.voting_token = token
                participation.has_voted = True
                participation.voted_at = now
                participation.ip_address = ip_address
                participation.save(update_fields=["voting_token", "has_voted", "voted_at", "ip_address"])

            ballot.refresh_from_db()
            return CastVoteResult(
                ballot_id=ballot.id,
                submitted_at=ballot.submitted_at,
                selected_candidate_ids=candidate_ids_validated,
            )

    @staticmethod
    def _ensure_election_open_for_voting(*, election: Election, at_time) -> None:
        if election.election_status.code != "IN_PROGRESS":
            raise VotingError("Election is not in progress.")
        if at_time < election.schedule.start_at or at_time >= election.schedule.end_at:
            raise VotingError("Election is outside allowed voting time window.")

    @staticmethod
    def _ensure_eligibility(*, election: Election, person) -> None:
        is_eligible = VotingEligibility.objects.filter(
            election=election,
            person=person,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        ).exists()
        if not is_eligible:
            raise VotingError("Person is not eligible to vote in this election.")

    @staticmethod
    def _validate_choice_count(*, selected_ids: list[int], voting_rule: VotingRule) -> None:
        selected_count = len(selected_ids)
        if selected_count == 0 and not voting_rule.allow_blank_vote:
            raise VotingError("Blank vote is not allowed for this election.")
        if selected_count < voting_rule.min_choices:
            raise VotingError(f"At least {voting_rule.min_choices} candidate(s) must be selected.")
        if selected_count > voting_rule.max_choices:
            raise VotingError(f"At most {voting_rule.max_choices} candidate(s) can be selected.")

    @staticmethod
    def _validate_candidates(*, election: Election, selected_ids: list[int]) -> list[int]:
        if not selected_ids:
            return []
        valid_ids = set(
            election.candidates.filter(id__in=selected_ids, is_approved=True).values_list("id", flat=True)
        )
        if len(valid_ids) != len(selected_ids):
            raise VotingError("One or more selected candidates are invalid for this election.")
        return selected_ids


class ElectionResultService:
    @staticmethod
    def generate_results(*, election: Election, generated_by_user=None, is_final: bool = True) -> ElectionResult:
        if election.election_status.code != "CLOSED":
            raise ElectionLifecycleError("Results can be generated only for closed elections.")

        with transaction.atomic():
            election = (
                Election.objects.select_related("election_status")
                .select_for_update()
                .get(id=election.id)
            )
            if election.election_status.code != "CLOSED":
                raise ElectionLifecycleError("Results can be generated only for closed elections.")

            eligible_voters_count = (
                VotingEligibility.objects.filter(
                    election=election,
                    eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
                )
                .values("person_id")
                .distinct()
                .count()
            )
            voters_count = (
                VotingParticipation.objects.filter(election=election, has_voted=True)
                .values("person_id")
                .distinct()
                .count()
            )
            turnout_percent = ElectionResultService._calculate_percent(
                numerator=voters_count,
                denominator=eligible_voters_count,
            )

            result, _ = ElectionResult.objects.update_or_create(
                election=election,
                defaults={
                    "calculated_at": timezone.now(),
                    "eligible_voters_count": eligible_voters_count,
                    "voters_count": voters_count,
                    "turnout_percent": turnout_percent,
                    "is_final": is_final,
                    "generated_by_user": generated_by_user,
                },
            )

            candidate_rows = list(
                ElectionCandidate.objects.filter(election=election, is_approved=True)
                .annotate(
                    votes_count=Count(
                        "ballot_selections",
                        filter=Q(
                            ballot_selections__ballot__election=election,
                            ballot_selections__ballot__ballot_status=Ballot.BallotStatus.SUBMITTED,
                        ),
                    )
                )
                .order_by("-votes_count", "candidate_number", "id")
            )
            total_votes_cast = sum(candidate.votes_count for candidate in candidate_rows)

            result.items.all().delete()
            ElectionResultItem.objects.bulk_create(
                [
                    ElectionResultItem(
                        election_result=result,
                        election_candidate=candidate,
                        votes_count=candidate.votes_count,
                        votes_percent=ElectionResultService._calculate_percent(
                            numerator=candidate.votes_count,
                            denominator=total_votes_cast,
                        ),
                        ranking_position=index + 1,
                    )
                    for index, candidate in enumerate(candidate_rows)
                ]
            )

            return result

    @staticmethod
    def _calculate_percent(*, numerator: int, denominator: int) -> Decimal:
        if denominator <= 0:
            return Decimal("0.00")
        return (Decimal(numerator) * Decimal("100") / Decimal(denominator)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
