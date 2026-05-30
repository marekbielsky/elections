import secrets
import uuid
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass
from typing import Iterable
from django.core.files.base import ContentFile

from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import (
    Ballot,
    BallotSelection,
    ElectionCandidate,
    ElectionResult,
    ElectionResultItem,
    GeneratedDocument,
    Election,
    ElectionSchedule,
    ElectionStatus,
    StoredFile,
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
    def start_due_elections(*, at_time=None) -> int:
        now = at_time or timezone.now()
        due_elections = Election.objects.select_related("election_status", "schedule").filter(
            election_status__code="PUBLISHED",
            schedule__start_at__lte=now,
            schedule__end_at__gt=now,
        )
        started_count = 0
        for election in due_elections:
            try:
                ElectionLifecycleService.start_election(election, at_time=now)
                started_count += 1
            except ElectionLifecycleError:
                continue
        return started_count
    @staticmethod
    def close_overdue_elections(*, at_time=None, generated_by_user=None) -> int:
        now = at_time or timezone.now()
        overdue_elections = Election.objects.select_related("election_status", "schedule").filter(
            election_status__code__in=["PUBLISHED", "IN_PROGRESS"],
            schedule__end_at__lte=now,
        )
        closed_count = 0
        for election in overdue_elections:
            try:
                ElectionLifecycleService.close_election(
                    election,
                    at_time=now,
                    generated_by_user=generated_by_user,
                )
                closed_count += 1
            except ElectionLifecycleError:
                continue
        return closed_count
    @staticmethod
    def _ensure_no_schedule_collision(*, start_at, end_at, organizational_unit) -> None:
        if organizational_unit is None:
            return
        has_collision = ElectionSchedule.objects.filter(
            election__organizational_unit=organizational_unit,
            start_at__lt=end_at,
            end_at__gt=start_at,
        ).exists()
        if has_collision:
            raise ElectionLifecycleError(
                "Wykryto kolizję harmonogramu dla wybranej jednostki organizacyjnej."
            )
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
            ElectionLifecycleService._ensure_no_schedule_collision(
                start_at=start_at,
                end_at=end_at,
                organizational_unit=organizational_unit,
            )
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
            raise ElectionLifecycleError("Nie można rozpocząć wyborów przed skonfigurowanym czasem startu.")
        if now >= schedule.end_at:
            raise ElectionLifecycleError("Nie można rozpocząć wyborów po skonfigurowanym czasie zakończenia.")
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
            raise ElectionLifecycleError("Nie można zamknąć wyborów przed skonfigurowanym czasem zakończenia.")
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
            raise ElectionLifecycleError(f"Wymagany status wyborów '{code}' nie jest skonfigurowany.") from exc

    @staticmethod
    def _ensure_status(election: Election, *, allowed: set[str]) -> None:
        current_status = election.election_status.code
        if current_status not in allowed:
            raise ElectionLifecycleError(
                f"Nieprawidłowa zmiana statusu wyborów z '{current_status}'. Dozwolone: {sorted(allowed)}."
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
        VotingService._ensure_eligibility(election=election, person=person)

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
                raise VotingError("Głos został już oddany i zmiana głosu jest wyłączona.")

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
            raise VotingError("Wybory nie są obecnie w trakcie.")
        if at_time < election.schedule.start_at or at_time >= election.schedule.end_at:
            raise VotingError("Wybory są poza dozwolonym oknem czasowym głosowania.")

    @staticmethod
    def _ensure_eligibility(*, election: Election, person) -> None:
        has_any_eligibilities = VotingEligibility.objects.filter(election=election).exists()
        if not has_any_eligibilities:
            return
        is_eligible = VotingEligibility.objects.filter(
            election=election,
            person=person,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        ).exists()
        if not is_eligible:
            raise VotingError("Ta osoba nie ma uprawnień do głosowania w tych wyborach.")

    @staticmethod
    def _validate_choice_count(*, selected_ids: list[int], voting_rule: VotingRule) -> None:
        selected_count = len(selected_ids)
        if selected_count == 0 and not voting_rule.allow_blank_vote:
            raise VotingError("Pusty głos nie jest dozwolony w tych wyborach.")
        if selected_count < voting_rule.min_choices:
            raise VotingError(f"Należy wybrać co najmniej {voting_rule.min_choices} kandydat(a/ów).")
        if selected_count > voting_rule.max_choices:
            raise VotingError(f"Można wybrać maksymalnie {voting_rule.max_choices} kandydat(a/ów).")

    @staticmethod
    def _validate_candidates(*, election: Election, selected_ids: list[int]) -> list[int]:
        if not selected_ids:
            return []
        valid_ids = set(
            election.candidates.filter(id__in=selected_ids, is_approved=True).values_list("id", flat=True)
        )
        if len(valid_ids) != len(selected_ids):
            raise VotingError("Co najmniej jeden z wybranych kandydatów jest nieprawidłowy dla tych wyborów.")
        return selected_ids


class ElectionResultService:
    @staticmethod
    def generate_results(*, election: Election, generated_by_user=None, is_final: bool = True) -> ElectionResult:
        if election.election_status.code != "CLOSED":
            raise ElectionLifecycleError("Wyniki można wygenerować tylko dla zamkniętych wyborów.")

        with transaction.atomic():
            election = (
                Election.objects.select_related("election_status")
                .select_for_update()
                .get(id=election.id)
            )
            if election.election_status.code != "CLOSED":
                raise ElectionLifecycleError("Wyniki można wygenerować tylko dla zamkniętych wyborów.")

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

class DatabaseProcedureService:
    @staticmethod
    def refresh_overdue_elections_and_fetch_turnout(*, generated_by_user=None, at_time=None) -> dict:
        with transaction.atomic():
            closed_elections_count = ElectionLifecycleService.close_overdue_elections(
                at_time=at_time,
                generated_by_user=generated_by_user,
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT election_type_code, election_type_name, elections_count, avg_turnout_percent
                    FROM elections_view_turnout_by_type
                    ORDER BY election_type_code
                    """
                )
                turnout_by_type = [
                    {
                        "election_type_code": row[0],
                        "election_type_name": row[1],
                        "elections_count": row[2],
                        "avg_turnout_percent": row[3],
                    }
                    for row in cursor.fetchall()
                ]
        return {
            "closed_elections_count": closed_elections_count,
            "turnout_by_type": turnout_by_type,
        }

    @staticmethod
    def recompute_closed_results_and_fetch_winners(*, generated_by_user=None) -> dict:
        with transaction.atomic():
            closed_elections = Election.objects.select_related("election_status").filter(
                election_status__code="CLOSED"
            )
            recomputed_results_count = 0
            for election in closed_elections:
                ElectionResultService.generate_results(
                    election=election,
                    generated_by_user=generated_by_user,
                    is_final=True,
                )
                recomputed_results_count += 1
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT election_id, election_name, winner_name, winner_votes
                    FROM elections_view_election_winners
                    ORDER BY election_id
                    """
                )
                winners = [
                    {
                        "election_id": row[0],
                        "election_name": row[1],
                        "winner_name": row[2],
                        "winner_votes": row[3],
                    }
                    for row in cursor.fetchall()
                ]
        return {
            "recomputed_results_count": recomputed_results_count,
            "winners": winners,
        }


class ElectionResultDocumentService:
    @staticmethod
    def generate_result_pdf(*, election: Election, generated_by_user=None) -> GeneratedDocument:
        if election.election_status.code != "CLOSED":
            raise ElectionLifecycleError("PDF z wynikami można wygenerować tylko dla zamkniętych wyborów.")

        result = ElectionResult.objects.filter(election=election).prefetch_related("items").first()
        if result is None:
            result = ElectionResultService.generate_results(
                election=election,
                generated_by_user=generated_by_user,
                is_final=True,
            )

        lines = [
            "Election Result Report",
            f"Election ID: {election.id}",
            f"Election name: {election.name}",
            f"Calculated at: {result.calculated_at}",
            f"Eligible voters: {result.eligible_voters_count}",
            f"Voters: {result.voters_count}",
            f"Turnout: {result.turnout_percent}%",
            "",
            "Ranking:",
        ]
        for item in result.items.select_related("election_candidate__person").order_by("ranking_position"):
            person = item.election_candidate.person
            lines.append(
                f"{item.ranking_position}. {person.first_name} {person.last_name} "
                f"- votes: {item.votes_count}, share: {item.votes_percent}%"
            )

        pdf_bytes = ElectionResultDocumentService._build_simple_pdf(lines)
        stored_file_name = f"result-report-{election.id}-{uuid.uuid4().hex}.pdf"
        stored_file = StoredFile.objects.create(
            original_file_name=f"election-{election.id}-result-report.pdf",
            stored_file_name=stored_file_name,
            file=ContentFile(pdf_bytes, name=stored_file_name),
            mime_type="application/pdf",
            file_size_bytes=len(pdf_bytes),
            uploaded_by_user=generated_by_user,
            is_public=False,
        )
        return GeneratedDocument.objects.create(
            election=election,
            election_result=result,
            stored_file=stored_file,
            document_type=GeneratedDocument.DocumentType.RESULT_PDF,
            generated_by_user=generated_by_user,
        )

    @staticmethod
    def _build_simple_pdf(lines: list[str]) -> bytes:
        def _escape(line: str) -> str:
            ascii_line = line.encode("latin-1", "replace").decode("latin-1")
            return ascii_line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

        text_lines = "BT /F1 12 Tf 50 790 Td 16 TL " + " ".join(
            f"({_escape(line)}) Tj T*" for line in lines
        ) + " ET"
        stream_bytes = text_lines.encode("latin-1")

        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(stream_bytes)).encode("ascii") + b" >>\nstream\n" + stream_bytes + b"\nendstream",
        ]

        output = b"%PDF-1.4\n"
        offsets = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(len(output))
            output += f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"

        xref_pos = len(output)
        output += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
        output += b"0000000000 65535 f \n"
        for offset in offsets[1:]:
            output += f"{offset:010d} 00000 n \n".encode("ascii")
        output += (
            b"trailer\n"
            + f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
            + b"startxref\n"
            + str(xref_pos).encode("ascii")
            + b"\n%%EOF"
        )
        return output
