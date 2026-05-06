from datetime import timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from .models import (
    Ballot,
    BallotSelection,
    Election,
    ElectionCandidate,
    ElectionEvent,
    ElectionSchedule,
    ElectionStatus,
    ElectionType,
    Notification,
    Person,
    VotingRule,
    VotingToken,
)


class ElectionIntegrityAndSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="tester",
            email="tester@example.com",
            password="secret123",
        )
        cls.user2 = user_model.objects.create_user(
            username="tester2",
            email="tester2@example.com",
            password="secret123",
        )
        cls.election_type = ElectionType.objects.create(code="STUDENT", name="Student")
        cls.election_status = ElectionStatus.objects.create(code="DRAFT", name="Draft")
        cls.person1 = Person.objects.create(
            user=cls.user,
            first_name="Jan",
            last_name="Kowalski",
            student_or_employee_no="S1001",
        )
        cls.person2 = Person.objects.create(
            user=cls.user2,
            first_name="Anna",
            last_name="Nowak",
            student_or_employee_no="S1002",
        )
        cls.election1 = Election.objects.create(
            election_type=cls.election_type,
            election_status=cls.election_status,
            name="Election One",
            description="Test election",
            created_by_user=cls.user,
        )
        cls.election2 = Election.objects.create(
            election_type=cls.election_type,
            election_status=cls.election_status,
            name="Election Two",
            description="Test election 2",
            created_by_user=cls.user,
        )

    def test_election_schedule_requires_start_before_end(self):
        now = timezone.now()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ElectionSchedule.objects.create(
                    election=self.election1,
                    start_at=now,
                    end_at=now - timedelta(hours=1),
                )

    def test_election_event_requires_start_before_end(self):
        now = timezone.now()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ElectionEvent.objects.create(
                    election=self.election1,
                    title="Invalid event",
                    start_at=now,
                    end_at=now - timedelta(minutes=5),
                    event_type=ElectionEvent.EventType.MEETING,
                )

    def test_voting_rule_requires_consistent_choice_bounds(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                VotingRule.objects.create(
                    election=self.election1,
                    min_choices=3,
                    max_choices=2,
                    requires_turnout_threshold=False,
                )

    def test_voting_rule_requires_threshold_or_null(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                VotingRule.objects.create(
                    election=self.election1,
                    min_choices=1,
                    max_choices=3,
                    requires_turnout_threshold=False,
                    turnout_threshold_percent=50,
                )

    def test_ballot_selection_rejects_candidate_from_other_election(self):
        candidate_other_election = ElectionCandidate.objects.create(
            election=self.election2,
            person=self.person2,
            candidate_number=7,
            is_approved=True,
        )
        token = VotingToken.objects.create(
            election=self.election1,
            person=self.person1,
            token_value="raw-token-a",
        )
        ballot = Ballot.objects.create(
            election=self.election1,
            voting_token=token,
            anonymous_key="anonymous-key-a",
        )

        with self.assertRaises(ValidationError):
            BallotSelection.objects.create(
                ballot=ballot,
                election_candidate=candidate_other_election,
                selection_order=1,
            )

    def test_voting_token_is_hashed_and_verifiable(self):
        token = VotingToken.objects.create(
            election=self.election1,
            person=self.person1,
            token_value="plain-token-123",
        )
        self.assertTrue(token.token_value.startswith("sha256$"))
        self.assertNotEqual(token.token_value, "plain-token-123")
        self.assertTrue(token.verify_token_value("plain-token-123"))
        self.assertFalse(token.verify_token_value("wrong-token"))

    def test_ballot_anonymous_key_is_hashed_and_verifiable(self):
        token = VotingToken.objects.create(
            election=self.election1,
            person=self.person1,
            token_value="plain-token-xyz",
        )
        ballot = Ballot.objects.create(
            election=self.election1,
            voting_token=token,
            anonymous_key="anonymous-key-123",
        )
        self.assertTrue(ballot.anonymous_key.startswith("sha256$"))
        self.assertNotEqual(ballot.anonymous_key, "anonymous-key-123")
        self.assertTrue(ballot.verify_anonymous_key("anonymous-key-123"))
        self.assertFalse(ballot.verify_anonymous_key("bad-key"))

    def test_database_trigger_rejects_cross_election_ballot_selection(self):
        candidate_other_election = ElectionCandidate.objects.create(
            election=self.election2,
            person=self.person2,
            candidate_number=8,
            is_approved=True,
        )
        token = VotingToken.objects.create(
            election=self.election1,
            person=self.person1,
            token_value="raw-token-trigger",
        )
        ballot = Ballot.objects.create(
            election=self.election1,
            voting_token=token,
            anonymous_key="anonymous-trigger-key",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO elections_ballotselection (ballot_id, election_candidate_id, selection_order, created_at)
                        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                        """,
                        [ballot.id, candidate_other_election.id, 1],
                    )

    def test_database_trigger_sets_used_at_when_token_marked_used(self):
        token = VotingToken.objects.create(
            election=self.election1,
            person=self.person1,
            token_value="token-set-used-at",
        )
        token.used_at = None
        token.is_used = True
        token.save(update_fields=["is_used", "used_at"])
        token.refresh_from_db()
        self.assertIsNotNone(token.used_at)

    def test_database_trigger_sets_submitted_at_when_ballot_submitted(self):
        token = VotingToken.objects.create(
            election=self.election1,
            person=self.person1,
            token_value="token-ballot-submitted",
        )
        ballot = Ballot.objects.create(
            election=self.election1,
            voting_token=token,
            anonymous_key="anonymous-submitted-key",
        )
        ballot.submitted_at = None
        ballot.ballot_status = Ballot.BallotStatus.SUBMITTED
        ballot.save(update_fields=["ballot_status", "submitted_at"])
        ballot.refresh_from_db()
        self.assertIsNotNone(ballot.submitted_at)

    def test_database_trigger_sets_sent_at_for_notifications(self):
        notification = Notification.objects.create(
            election=self.election1,
            recipient_person=self.person1,
            notification_type=Notification.NotificationType.SYSTEM,
            subject="Test",
            content="Test notification content",
            is_sent=False,
        )
        notification.sent_at = None
        notification.is_sent = True
        notification.save(update_fields=["is_sent", "sent_at"])
        notification.refresh_from_db()
        self.assertIsNotNone(notification.sent_at)
