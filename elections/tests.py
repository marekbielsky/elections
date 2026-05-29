from datetime import timedelta
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

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
    Permission,
    Person,
    Role,
    RolePermission,
    UserRole,
    VotingEligibility,
    VotingParticipation,
    VotingRule,
    VotingToken,
)
from .services import ElectionLifecycleError, ElectionLifecycleService, VotingError, VotingService


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


class AdminRoleMvpRoutesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin_user = user_model.objects.create_user(
            username="mvp_admin",
            email="mvp_admin@example.com",
            password="secret123",
        )
        cls.normal_user = user_model.objects.create_user(
            username="mvp_user",
            email="mvp_user@example.com",
            password="secret123",
        )
        UserRole.objects.create(user=cls.admin_user, role=UserRole.Role.ADMIN)
        UserRole.objects.create(user=cls.normal_user, role=UserRole.Role.USER)

    def test_admin_route_is_forbidden_for_non_admin(self):
        response = self.client.get(
            reverse("admin_overview"),
            HTTP_X_USER_ROLE=UserRole.Role.USER,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["current_role"], UserRole.Role.USER)

    def test_admin_route_is_accessible_for_admin(self):
        response = self.client.get(
            reverse("admin_overview"),
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("summary", response.json())

    def test_admin_route_accepts_role_header_for_mvp_without_auth(self):
        response = self.client.get(
            reverse("admin_users_roles"),
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("users", response.json())

    @override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
            },
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        }
    )
    def test_admin_route_renders_html_for_browser_requests(self):
        response = self.client.get(
            reverse("admin_overview"),
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Panel administracyjny")


class AdminWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin_user = user_model.objects.create_user(
            username="workflow_admin",
            email="workflow_admin@example.com",
            password="secret123",
        )
        cls.normal_user = user_model.objects.create_user(
            username="workflow_user",
            email="workflow_user@example.com",
            password="secret123",
        )
        UserRole.objects.create(user=cls.admin_user, role=UserRole.Role.ADMIN)
        UserRole.objects.create(user=cls.normal_user, role=UserRole.Role.USER)

        cls.role_admin = Role.objects.create(code=UserRole.Role.ADMIN, name="Admin")
        cls.role_user = Role.objects.create(code=UserRole.Role.USER, name="User")

        cls.permission_assign_role = Permission.objects.create(
            code="admin.role.assign",
            name="Assign role",
        )
        cls.permission_manage_lifecycle = Permission.objects.create(
            code="admin.election.lifecycle",
            name="Manage election lifecycle",
        )

        cls.election_type = ElectionType.objects.create(code="WFLOW", name="Workflow Election")
        cls.status_draft = ElectionStatus.objects.create(code="DRAFT", name="Draft")
        cls.status_published = ElectionStatus.objects.create(code="PUBLISHED", name="Published")
        cls.status_in_progress = ElectionStatus.objects.create(code="IN_PROGRESS", name="In progress")
        cls.status_closed = ElectionStatus.objects.create(code="CLOSED", name="Closed")

        now = timezone.now()
        cls.election = Election.objects.create(
            election_type=cls.election_type,
            election_status=cls.status_draft,
            name="Workflow Election 1",
            created_by_user=cls.admin_user,
        )
        ElectionSchedule.objects.create(
            election=cls.election,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        VotingRule.objects.create(
            election=cls.election,
            min_choices=1,
            max_choices=1,
            allow_blank_vote=False,
            allow_vote_change=False,
        )

    def test_admin_can_assign_user_role(self):
        response = self.client.post(
            reverse("admin_user_role_assign"),
            {"user_id": self.normal_user.id, "role": UserRole.Role.AUDITOR},
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.normal_user.role_profile.refresh_from_db()
        self.assertEqual(self.normal_user.role_profile.role, UserRole.Role.AUDITOR)

    def test_user_cannot_assign_user_role(self):
        response = self.client.post(
            reverse("admin_user_role_assign"),
            {"user_id": self.normal_user.id, "role": UserRole.Role.ADMIN},
            HTTP_X_USER_ROLE=UserRole.Role.USER,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_grant_and_revoke_role_permission(self):
        grant_response = self.client.post(
            reverse("admin_role_permission_assign"),
            {"role_id": self.role_user.id, "permission_id": self.permission_assign_role.id, "grant": "on"},
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(grant_response.status_code, 200)
        self.assertTrue(
            RolePermission.objects.filter(
                role=self.role_user,
                permission=self.permission_assign_role,
            ).exists()
        )

        revoke_response = self.client.post(
            reverse("admin_role_permission_assign"),
            {"role_id": self.role_user.id, "permission_id": self.permission_assign_role.id},
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(revoke_response.status_code, 200)
        self.assertFalse(
            RolePermission.objects.filter(
                role=self.role_user,
                permission=self.permission_assign_role,
            ).exists()
        )

    def test_user_cannot_assign_role_permission(self):
        response = self.client.post(
            reverse("admin_role_permission_assign"),
            {"role_id": self.role_user.id, "permission_id": self.permission_assign_role.id, "grant": "on"},
            HTTP_X_USER_ROLE=UserRole.Role.USER,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_execute_election_lifecycle_actions(self):
        publish_response = self.client.post(
            reverse("admin_election_lifecycle_action"),
            {"election_id": self.election.id, "action": "publish"},
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(publish_response.status_code, 200)
        self.election.refresh_from_db()
        self.assertEqual(self.election.election_status.code, "PUBLISHED")

        start_response = self.client.post(
            reverse("admin_election_lifecycle_action"),
            {"election_id": self.election.id, "action": "start"},
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(start_response.status_code, 200)
        self.election.refresh_from_db()
        self.assertEqual(self.election.election_status.code, "IN_PROGRESS")

        close_response = self.client.post(
            reverse("admin_election_lifecycle_action"),
            {"election_id": self.election.id, "action": "close", "force_close": "on"},
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(close_response.status_code, 200)
        self.election.refresh_from_db()
        self.assertEqual(self.election.election_status.code, "CLOSED")

    def test_user_cannot_execute_election_lifecycle_actions(self):
        response = self.client.post(
            reverse("admin_election_lifecycle_action"),
            {"election_id": self.election.id, "action": "publish"},
            HTTP_X_USER_ROLE=UserRole.Role.USER,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)


class ElectionServicesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="service_user",
            email="service_user@example.com",
            password="secret123",
        )
        cls.user2 = user_model.objects.create_user(
            username="service_user_2",
            email="service_user_2@example.com",
            password="secret123",
        )
        cls.person = Person.objects.create(
            user=cls.user,
            first_name="Marek",
            last_name="Nowicki",
            student_or_employee_no="SV-001",
        )
        cls.person2 = Person.objects.create(
            user=cls.user2,
            first_name="Kasia",
            last_name="Witkowska",
            student_or_employee_no="SV-002",
        )

        cls.election_type = ElectionType.objects.create(code="SERVICE_TEST", name="Service test")
        cls.status_draft = ElectionStatus.objects.create(code="DRAFT", name="Draft")
        cls.status_published = ElectionStatus.objects.create(code="PUBLISHED", name="Published")
        cls.status_in_progress = ElectionStatus.objects.create(code="IN_PROGRESS", name="In progress")
        cls.status_closed = ElectionStatus.objects.create(code="CLOSED", name="Closed")

        now = timezone.now()
        cls.election = Election.objects.create(
            election_type=cls.election_type,
            election_status=cls.status_draft,
            name="Service election",
            created_by_user=cls.user,
        )
        ElectionSchedule.objects.create(
            election=cls.election,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        VotingRule.objects.create(
            election=cls.election,
            min_choices=1,
            max_choices=2,
            allow_blank_vote=False,
            allow_vote_change=False,
        )
        cls.candidate1 = ElectionCandidate.objects.create(
            election=cls.election,
            person=cls.person,
            candidate_number=1,
            is_approved=True,
        )
        cls.candidate2 = ElectionCandidate.objects.create(
            election=cls.election,
            person=cls.person2,
            candidate_number=2,
            is_approved=True,
        )

    def test_lifecycle_publish_start_close(self):
        ElectionLifecycleService.publish_election(self.election)
        self.election.refresh_from_db()
        self.assertEqual(self.election.election_status.code, "PUBLISHED")

        ElectionLifecycleService.start_election(self.election)
        self.election.refresh_from_db()
        self.assertEqual(self.election.election_status.code, "IN_PROGRESS")

        with self.assertRaises(ElectionLifecycleError):
            ElectionLifecycleService.close_election(self.election)

        ElectionLifecycleService.close_election(self.election, force=True)
        self.election.refresh_from_db()
        self.assertEqual(self.election.election_status.code, "CLOSED")

    def test_issue_token_requires_eligibility(self):
        with self.assertRaises(VotingError):
            VotingService.issue_token(
                election=self.election,
                person=self.person,
                raw_token="token-no-eligibility",
            )

    def test_cast_vote_happy_path_marks_participation_and_token(self):
        self.election.election_status = self.status_in_progress
        self.election.save(update_fields=["election_status"])
        VotingEligibility.objects.create(
            election=self.election,
            person=self.person,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        token = VotingService.issue_token(
            election=self.election,
            person=self.person,
            raw_token="service-raw-token",
        )

        result = VotingService.cast_vote(
            election=self.election,
            person=self.person,
            raw_token="service-raw-token",
            candidate_ids=[self.candidate1.id, self.candidate2.id],
            anonymous_key="service-anon-key",
            ip_address="127.0.0.1",
        )

        token.refresh_from_db()
        self.assertTrue(token.is_used)
        self.assertEqual(result.selected_candidate_ids, [self.candidate1.id, self.candidate2.id])

        ballot = Ballot.objects.get(id=result.ballot_id)
        self.assertEqual(ballot.ballot_status, Ballot.BallotStatus.SUBMITTED)
        self.assertEqual(ballot.selections.count(), 2)

        participation = VotingParticipation.objects.get(election=self.election, person=self.person)
        self.assertTrue(participation.has_voted)
        self.assertEqual(participation.ip_address, "127.0.0.1")

    def test_cast_vote_rejects_token_reuse_when_vote_change_disabled(self):
        self.election.election_status = self.status_in_progress
        self.election.save(update_fields=["election_status"])
        VotingEligibility.objects.create(
            election=self.election,
            person=self.person,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingService.issue_token(
            election=self.election,
            person=self.person,
            raw_token="one-time-token",
        )

        VotingService.cast_vote(
            election=self.election,
            person=self.person,
            raw_token="one-time-token",
            candidate_ids=[self.candidate1.id],
            anonymous_key="anon-once",
        )

        with self.assertRaises(VotingError):
            VotingService.cast_vote(
                election=self.election,
                person=self.person,
                raw_token="one-time-token",
                candidate_ids=[self.candidate2.id],
                anonymous_key="anon-twice",
            )

    def test_cast_vote_rejects_invalid_choice_count(self):
        self.election.election_status = self.status_in_progress
        self.election.save(update_fields=["election_status"])
        VotingEligibility.objects.create(
            election=self.election,
            person=self.person,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingService.issue_token(
            election=self.election,
            person=self.person,
            raw_token="token-invalid-choice",
        )

        with self.assertRaises(VotingError):
            VotingService.cast_vote(
                election=self.election,
                person=self.person,
                raw_token="token-invalid-choice",
                candidate_ids=[],
                anonymous_key="anon-invalid-choice",
            )

    def test_close_election_generates_final_result_with_rankings(self):
        self.election.election_status = self.status_in_progress
        self.election.save(update_fields=["election_status"])
        VotingEligibility.objects.create(
            election=self.election,
            person=self.person,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingEligibility.objects.create(
            election=self.election,
            person=self.person2,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingService.issue_token(
            election=self.election,
            person=self.person,
            raw_token="result-token-1",
        )
        VotingService.issue_token(
            election=self.election,
            person=self.person2,
            raw_token="result-token-2",
        )
        VotingService.cast_vote(
            election=self.election,
            person=self.person,
            raw_token="result-token-1",
            candidate_ids=[self.candidate1.id],
            anonymous_key="result-anon-1",
        )
        VotingService.cast_vote(
            election=self.election,
            person=self.person2,
            raw_token="result-token-2",
            candidate_ids=[self.candidate1.id],
            anonymous_key="result-anon-2",
        )

        ElectionLifecycleService.close_election(self.election, force=True)
        self.election.refresh_from_db()
        self.assertEqual(self.election.election_status.code, "CLOSED")

        result = self.election.result
        self.assertTrue(result.is_final)
        self.assertEqual(result.eligible_voters_count, 2)
        self.assertEqual(result.voters_count, 2)
        self.assertEqual(str(result.turnout_percent), "100.00")

        items = list(result.items.order_by("ranking_position"))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].election_candidate_id, self.candidate1.id)
        self.assertEqual(items[0].votes_count, 2)
        self.assertEqual(str(items[0].votes_percent), "100.00")
        self.assertEqual(items[1].election_candidate_id, self.candidate2.id)
        self.assertEqual(items[1].votes_count, 0)
        self.assertEqual(str(items[1].votes_percent), "0.00")


class ElectionApiEndpointsTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="api_user",
            email="api_user@example.com",
            password="secret123",
        )
        cls.user2 = user_model.objects.create_user(
            username="api_user_2",
            email="api_user_2@example.com",
            password="secret123",
        )
        cls.person1 = Person.objects.create(
            user=cls.user,
            first_name="Ola",
            last_name="Krawczyk",
            student_or_employee_no="API-001",
        )
        cls.person2 = Person.objects.create(
            user=cls.user2,
            first_name="Piotr",
            last_name="Mazur",
            student_or_employee_no="API-002",
        )
        cls.election_type = ElectionType.objects.create(code="API_TYPE", name="API type")
        cls.status_draft = ElectionStatus.objects.create(code="DRAFT", name="Draft")
        cls.status_published = ElectionStatus.objects.create(code="PUBLISHED", name="Published")
        cls.status_in_progress = ElectionStatus.objects.create(code="IN_PROGRESS", name="In progress")
        cls.status_closed = ElectionStatus.objects.create(code="CLOSED", name="Closed")

    def test_create_election_api(self):
        now = timezone.now()
        payload = {
            "election_type_id": self.election_type.id,
            "election_status_code": "DRAFT",
            "name": "API Created Election",
            "description": "Created via API",
            "is_secret": True,
            "start_at": (now + timedelta(hours=1)).isoformat(),
            "end_at": (now + timedelta(hours=2)).isoformat(),
            "min_choices": 1,
            "max_choices": 1,
            "allow_blank_vote": False,
            "allow_vote_change": False,
        }
        response = self.client.post(
            reverse("api_election_create"),
            payload,
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "DRAFT")
        self.assertEqual(response.data["name"], "API Created Election")

    def test_lifecycle_endpoints(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Lifecycle API Election",
            election_status=self.status_draft,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            created_by_user=self.user,
        )

        publish = self.client.post(
            reverse("api_election_publish", kwargs={"election_id": election.id}),
            {},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(publish.status_code, 200)
        self.assertEqual(publish.data["status"], "PUBLISHED")

        start = self.client.post(
            reverse("api_election_start", kwargs={"election_id": election.id}),
            {},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(start.status_code, 200)
        self.assertEqual(start.data["status"], "IN_PROGRESS")

        close = self.client.post(
            reverse("api_election_close", kwargs={"election_id": election.id}),
            {"force": True},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(close.status_code, 200)
        self.assertEqual(close.data["status"], "CLOSED")

    def test_issue_token_and_cast_vote_api(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Vote API Election",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            created_by_user=self.user,
            min_choices=1,
            max_choices=2,
        )
        candidate1 = ElectionCandidate.objects.create(
            election=election,
            person=self.person1,
            candidate_number=1,
            is_approved=True,
        )
        candidate2 = ElectionCandidate.objects.create(
            election=election,
            person=self.person2,
            candidate_number=2,
            is_approved=True,
        )
        VotingEligibility.objects.create(
            election=election,
            person=self.person1,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )

        issue = self.client.post(
            reverse("api_issue_voting_token", kwargs={"election_id": election.id}),
            {"person_id": self.person1.id, "raw_token": "api-vote-token"},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(issue.status_code, 201)

        vote = self.client.post(
            reverse("api_cast_vote", kwargs={"election_id": election.id}),
            {
                "person_id": self.person1.id,
                "raw_token": "api-vote-token",
                "candidate_ids": [candidate1.id, candidate2.id],
                "anonymous_key": "api-anon-key",
            },
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.USER,
        )
        self.assertEqual(vote.status_code, 201)
        self.assertEqual(vote.data["selected_candidate_ids"], [candidate1.id, candidate2.id])

    def test_cast_vote_api_rejects_invalid_token(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Vote Invalid Token",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            created_by_user=self.user,
        )
        candidate = ElectionCandidate.objects.create(
            election=election,
            person=self.person1,
            candidate_number=1,
            is_approved=True,
        )
        VotingEligibility.objects.create(
            election=election,
            person=self.person1,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingService.issue_token(
            election=election,
            person=self.person1,
            raw_token="valid-token",
        )

        vote = self.client.post(
            reverse("api_cast_vote", kwargs={"election_id": election.id}),
            {
                "person_id": self.person1.id,
                "raw_token": "invalid-token",
                "candidate_ids": [candidate.id],
                "anonymous_key": "api-anon-key-2",
            },
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.USER,
        )
        self.assertEqual(vote.status_code, 400)

    def test_create_election_api_forbidden_for_user_role(self):
        now = timezone.now()
        payload = {
            "election_type_id": self.election_type.id,
            "election_status_code": "DRAFT",
            "name": "Forbidden Election Create",
            "description": "Should fail",
            "is_secret": True,
            "start_at": (now + timedelta(hours=1)).isoformat(),
            "end_at": (now + timedelta(hours=2)).isoformat(),
            "min_choices": 1,
            "max_choices": 1,
            "allow_blank_vote": False,
            "allow_vote_change": False,
        }
        response = self.client.post(
            reverse("api_election_create"),
            payload,
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.USER,
        )
        self.assertEqual(response.status_code, 403)

    def test_results_api_rejects_non_closed_election(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Open Election Results",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            created_by_user=self.user,
        )
        response = self.client.get(
            reverse("api_election_results", kwargs={"election_id": election.id}),
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 400)

    def test_results_api_respects_results_publish_at(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Publish Gate Election",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=2),
            end_at=now - timedelta(hours=1),
            created_by_user=self.user,
        )
        election.schedule.results_publish_at = now + timedelta(hours=2)
        election.schedule.save(update_fields=["results_publish_at"])
        ElectionLifecycleService.close_election(election, force=True)

        response = self.client.get(
            reverse("api_election_results", kwargs={"election_id": election.id}),
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 403)
