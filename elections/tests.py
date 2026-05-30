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
    ElectionResult,
    GeneratedDocument,
    ElectionSchedule,
    ElectionStatus,
    ElectionType,
    Notification,
    OrganizationalUnit,
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
        self.client.force_login(self.normal_user)
        response = self.client.get(
            reverse("admin_overview"),
            HTTP_X_USER_ROLE=UserRole.Role.USER,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["current_role"], UserRole.Role.USER)

    def test_admin_route_is_accessible_for_admin(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin_overview"),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("summary", response.json())
    def test_admin_route_requires_authentication(self):
        response = self.client.get(
            reverse("admin_users_roles"),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 302)

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
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin_overview"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Panel administracyjny")

class AuthenticationFlowTests(TestCase):
    def test_register_creates_user_with_default_role_and_logs_in(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "new_auth_user",
                "email": "new_auth_user@example.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        user_model = get_user_model()
        user = user_model.objects.get(username="new_auth_user")
        self.assertTrue(UserRole.objects.filter(user=user, role=UserRole.Role.USER).exists())
        self.assertTrue(Person.objects.filter(user=user).exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.id)

    def test_login_creates_missing_default_role_profile(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="existing_no_role",
            email="existing_no_role@example.com",
            password="StrongPass123!",
        )
        self.assertFalse(UserRole.objects.filter(user=user).exists())
        self.assertFalse(Person.objects.filter(user=user).exists())

        response = self.client.post(
            reverse("login"),
            {
                "username": "existing_no_role",
                "password": "StrongPass123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(UserRole.objects.filter(user=user, role=UserRole.Role.USER).exists())
        self.assertTrue(Person.objects.filter(user=user).exists())

    def test_logout_post_clears_authenticated_session(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="logout_user",
            email="logout_user@example.com",
            password="StrongPass123!",
        )
        self.client.force_login(user)
        response = self.client.post(reverse("logout"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

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
    def test_anonymous_user_sees_only_login_and_register_in_navigation(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zaloguj")
        self.assertContains(response, "Zarejestruj")
        self.assertNotContains(response, "Kandydaci")
        self.assertNotContains(response, "Wybory")
        self.assertNotContains(response, "Panel administracyjny")

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
    def test_anonymous_user_on_home_does_not_see_available_sections_container(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Konto użytkownika")
        self.assertContains(response, "Zaloguj")
        self.assertContains(response, "Zarejestruj")
        self.assertNotContains(response, "Dostępne sekcje")

class RBACNavigationVisibilityTests(TestCase):
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
    def test_authenticated_user_sees_only_tabs_allowed_for_user_role(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="regular_nav_user",
            email="regular_nav_user@example.com",
            password="StrongPass123!",
        )
        UserRole.objects.create(user=user, role=UserRole.Role.USER)
        role_user, _ = Role.objects.get_or_create(code=UserRole.Role.USER, defaults={"name": "User"})
        role_user.role_permissions.all().delete()
        for permission_code in ("candidate.read", "committee.read", "election.read", "result.read", "voting.cast"):
            permission, _ = Permission.objects.get_or_create(code=permission_code, defaults={"name": permission_code})
            RolePermission.objects.get_or_create(role=role_user, permission=permission)

        self.client.force_login(user)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kandydaci")
        self.assertContains(response, "Wybory")
        self.assertContains(response, "Kalendarz")
        self.assertContains(response, "Wyniki głosowania")
        self.assertContains(response, "Oddaj głos")
        self.assertNotContains(response, "Dodaj kandydata")
        self.assertNotContains(response, "Dodaj wybory")
        self.assertNotContains(response, "Panel administracyjny")

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
    def test_authenticated_admin_sees_admin_and_create_tabs(self):
        user_model = get_user_model()
        admin = user_model.objects.create_user(
            username="admin_nav_user",
            email="admin_nav_user@example.com",
            password="StrongPass123!",
        )
        UserRole.objects.create(user=admin, role=UserRole.Role.ADMIN)
        role_admin, _ = Role.objects.get_or_create(code=UserRole.Role.ADMIN, defaults={"name": "Admin"})
        role_admin.role_permissions.all().delete()
        for permission_code in (
            "candidate.read",
            "candidate.create",
            "committee.read",
            "election.read",
            "election.create",
            "result.read",
            "voting.cast",
            "admin.panel.view",
        ):
            permission, _ = Permission.objects.get_or_create(code=permission_code, defaults={"name": permission_code})
            RolePermission.objects.get_or_create(role=role_admin, permission=permission)

        self.client.force_login(admin)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dodaj kandydata")
        self.assertContains(response, "Dodaj wybory")
        self.assertContains(response, "Panel administracyjny")

    def test_authenticated_user_without_role_cannot_escalate_via_role_header(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="header_escalation_user",
            email="header_escalation_user@example.com",
            password="StrongPass123!",
        )
        self.assertFalse(UserRole.objects.filter(user=user).exists())

        self.client.force_login(user)
        response = self.client.get(
            reverse("admin_users_roles"),
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["current_role"], UserRole.Role.USER)

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
    def test_staff_user_without_role_profile_gets_admin_navigation(self):
        user_model = get_user_model()
        staff_user = user_model.objects.create_user(
            username="staff_without_role",
            email="staff_without_role@example.com",
            password="StrongPass123!",
            is_staff=True,
        )
        self.assertFalse(UserRole.objects.filter(user=staff_user).exists())

        self.client.force_login(staff_user)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dodaj wybory")
        self.assertContains(response, "Panel administracyjny")

class ElectionCreationAndAutoCloseFlowTests(TestCase):
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
    def test_admin_can_create_election_with_selected_eligible_voters(self):
        user_model = get_user_model()
        admin = user_model.objects.create_user(
            username="flow_admin",
            email="flow_admin@example.com",
            password="StrongPass123!",
        )
        UserRole.objects.create(user=admin, role=UserRole.Role.ADMIN)
        voter1_user = user_model.objects.create_user(
            username="flow_voter_1",
            email="flow_voter_1@example.com",
            password="StrongPass123!",
        )
        voter2_user = user_model.objects.create_user(
            username="flow_voter_2",
            email="flow_voter_2@example.com",
            password="StrongPass123!",
        )
        voter1 = Person.objects.create(
            user=voter1_user,
            first_name="Ala",
            last_name="Nowak",
            student_or_employee_no="FLOW-001",
        )
        voter2 = Person.objects.create(
            user=voter2_user,
            first_name="Olek",
            last_name="Kowal",
            student_or_employee_no="FLOW-002",
        )
        election_type = ElectionType.objects.create(code="FLOW_WEB", name="Flow web")
        election_status = ElectionStatus.objects.create(code="DRAFT", name="Draft")
        start_at = timezone.now() + timedelta(days=1)
        end_at = start_at + timedelta(days=1)

        self.client.force_login(admin)
        response = self.client.post(
            reverse("election_create"),
            {
                "name": "Nowe wybory z uprawnionymi",
                "description": "Test przypisania uprawnionych",
                "election_type": election_type.id,
                "election_status": election_status.id,
                "is_secret": "on",
                "start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
                "end_at": end_at.strftime("%Y-%m-%dT%H:%M"),
                "min_choices": 1,
                "max_choices": 1,
                "eligible_people": [voter1.id, voter2.id],
            },
        )
        self.assertEqual(response.status_code, 200)
        election = Election.objects.get(name="Nowe wybory z uprawnionymi")
        eligible_ids = set(
            VotingEligibility.objects.filter(
                election=election,
                eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
            ).values_list("person_id", flat=True)
        )
        self.assertEqual(eligible_ids, {voter1.id, voter2.id})

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
    def test_user_can_cast_vote_from_ui(self):
        user_model = get_user_model()
        voter_user = user_model.objects.create_user(
            username="ui_voter",
            email="ui_voter@example.com",
            password="StrongPass123!",
        )
        candidate_user = user_model.objects.create_user(
            username="ui_candidate",
            email="ui_candidate@example.com",
            password="StrongPass123!",
        )
        UserRole.objects.create(user=voter_user, role=UserRole.Role.USER)
        voter_person = Person.objects.create(
            user=voter_user,
            first_name="Ula",
            last_name="Głosująca",
            student_or_employee_no="UI-VOTER-001",
        )
        candidate_person = Person.objects.create(
            user=candidate_user,
            first_name="Karol",
            last_name="Kandydat",
            student_or_employee_no="UI-CAND-001",
        )
        election_type = ElectionType.objects.create(code="UI_VOTE", name="UI vote")
        status_in_progress = ElectionStatus.objects.create(code="IN_PROGRESS", name="In progress")
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=election_type,
            name="UI Voting Election",
            election_status=status_in_progress,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            created_by_user=voter_user,
        )
        candidate = ElectionCandidate.objects.create(
            election=election,
            person=candidate_person,
            candidate_number=1,
            is_approved=True,
        )
        VotingEligibility.objects.create(
            election=election,
            person=voter_person,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingService.issue_token(
            election=election,
            person=voter_person,
            raw_token="ui-vote-token",
        )

        self.client.force_login(voter_user)
        response = self.client.post(
            reverse("vote_cast"),
            {
                "election": election.id,
                "raw_token": "ui-vote-token",
                "candidate_ids": [str(candidate.id)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Głos został zapisany poprawnie")
        participation = VotingParticipation.objects.get(election=election, person=voter_person)
        self.assertTrue(participation.has_voted)

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
    def test_vote_form_lists_only_currently_available_elections_for_user(self):
        user_model = get_user_model()
        voter_user = user_model.objects.create_user(
            username="ui_scope_voter",
            email="ui_scope_voter@example.com",
            password="StrongPass123!",
        )
        UserRole.objects.create(user=voter_user, role=UserRole.Role.USER)
        voter_person = Person.objects.create(
            user=voter_user,
            first_name="Iga",
            last_name="Zakres",
            student_or_employee_no="UI-SCOPE-001",
        )
        election_type = ElectionType.objects.create(code="UI_SCOPE", name="UI scope")
        status_in_progress = ElectionStatus.objects.create(code="IN_PROGRESS", name="In progress")
        now = timezone.now()

        visible_election = ElectionLifecycleService.create_election_with_config(
            election_type=election_type,
            name="Wybory dostępne",
            election_status=status_in_progress,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=2),
            created_by_user=voter_user,
        )
        hidden_election = ElectionLifecycleService.create_election_with_config(
            election_type=election_type,
            name="Wybory bez dostępu",
            election_status=status_in_progress,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=2),
            created_by_user=voter_user,
        )

        VotingEligibility.objects.create(
            election=visible_election,
            person=voter_person,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )

        self.client.force_login(voter_user)
        response = self.client.get(reverse("vote_cast"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wybory dostępne")
        self.assertNotContains(response, "Wybory bez dostępu")


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
        self.client.force_login(self.admin_user)
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
        self.client.force_login(self.normal_user)
        response = self.client.post(
            reverse("admin_user_role_assign"),
            {"user_id": self.normal_user.id, "role": UserRole.Role.ADMIN},
            HTTP_X_USER_ROLE=UserRole.Role.USER,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_grant_and_revoke_role_permission(self):
        self.client.force_login(self.admin_user)
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
        self.client.force_login(self.normal_user)
        response = self.client.post(
            reverse("admin_role_permission_assign"),
            {"role_id": self.role_user.id, "permission_id": self.permission_assign_role.id, "grant": "on"},
            HTTP_X_USER_ROLE=UserRole.Role.USER,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_execute_election_lifecycle_actions(self):
        self.client.force_login(self.admin_user)
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
        self.client.force_login(self.normal_user)
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

    def test_create_election_with_config_blocks_overlapping_schedule_in_same_unit(self):
        unit = OrganizationalUnit.objects.create(name="Collision Unit", unit_type="FACULTY")
        now = timezone.now()
        ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Collision Base",
            election_status=self.status_draft,
            start_at=now + timedelta(hours=2),
            end_at=now + timedelta(hours=4),
            created_by_user=self.user,
            organizational_unit=unit,
        )

        with self.assertRaises(ElectionLifecycleError):
            ElectionLifecycleService.create_election_with_config(
                election_type=self.election_type,
                name="Collision Overlap",
                election_status=self.status_draft,
                start_at=now + timedelta(hours=3),
                end_at=now + timedelta(hours=5),
                created_by_user=self.user,
                organizational_unit=unit,
            )

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

    def test_create_election_api_assigns_eligible_voters(self):
        now = timezone.now()
        payload = {
            "election_type_id": self.election_type.id,
            "election_status_code": "DRAFT",
            "name": "API Election With Eligible Voters",
            "description": "Created via API with eligibility assignment",
            "is_secret": True,
            "start_at": (now + timedelta(hours=1)).isoformat(),
            "end_at": (now + timedelta(hours=2)).isoformat(),
            "min_choices": 1,
            "max_choices": 1,
            "allow_blank_vote": False,
            "allow_vote_change": False,
            "eligible_person_ids": [self.person1.id, self.person2.id],
        }
        response = self.client.post(
            reverse("api_election_create"),
            payload,
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 201)
        election = Election.objects.get(id=response.data["id"])
        eligible_ids = set(
            VotingEligibility.objects.filter(
                election=election,
                eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
            ).values_list("person_id", flat=True)
        )
        self.assertEqual(eligible_ids, {self.person1.id, self.person2.id})

    def test_create_election_api_rejects_schedule_collision_for_same_unit(self):
        unit = OrganizationalUnit.objects.create(name="API Collision Unit", unit_type="FACULTY")
        now = timezone.now()
        ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Existing Unit Election",
            election_status=self.status_draft,
            start_at=now + timedelta(hours=2),
            end_at=now + timedelta(hours=6),
            created_by_user=self.user,
            organizational_unit=unit,
        )

        payload = {
            "election_type_id": self.election_type.id,
            "election_status_code": "DRAFT",
            "organizational_unit_id": unit.id,
            "name": "Overlapping Unit Election",
            "description": "Should fail due to collision",
            "is_secret": True,
            "start_at": (now + timedelta(hours=3)).isoformat(),
            "end_at": (now + timedelta(hours=7)).isoformat(),
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
        self.assertEqual(response.status_code, 400)

    def test_results_api_auto_closes_overdue_election_and_returns_results(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Overdue Auto Close Election",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=3),
            end_at=now - timedelta(hours=1),
            created_by_user=self.user,
        )
        self.assertEqual(election.election_status.code, "IN_PROGRESS")

        response = self.client.get(
            reverse("api_election_results", kwargs={"election_id": election.id}),
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        election.refresh_from_db()
        self.assertEqual(election.election_status.code, "CLOSED")
        self.assertTrue(ElectionResult.objects.filter(election=election).exists())

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

    def test_election_calendar_events_api_returns_start_end_and_publish_events(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Calendar Election",
            election_status=self.status_published,
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(hours=5),
            created_by_user=self.user,
        )
        election.schedule.results_publish_at = now + timedelta(hours=8)
        election.schedule.save(update_fields=["results_publish_at"])

        response = self.client.get(
            reverse("api_election_calendar_events"),
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        election_events = [row for row in response.data["events"] if row["election_id"] == election.id]
        self.assertEqual(len(election_events), 3)
        self.assertEqual(
            [row["event_type"] for row in election_events],
            ["ELECTION_START", "ELECTION_END", "RESULTS_PUBLISH"],
        )

    def test_election_calendar_events_api_orders_events_by_datetime(self):
        now = timezone.now()
        election_a = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Calendar A",
            election_status=self.status_published,
            start_at=now + timedelta(hours=4),
            end_at=now + timedelta(hours=6),
            created_by_user=self.user,
        )
        election_b = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Calendar B",
            election_status=self.status_published,
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(hours=2),
            created_by_user=self.user,
        )

        response = self.client.get(
            reverse("api_election_calendar_events"),
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        ids_and_types = [
            (row["election_id"], row["event_type"])
            for row in response.data["events"]
            if row["election_id"] in {election_a.id, election_b.id}
        ]
        self.assertEqual(
            ids_and_types[:4],
            [
                (election_b.id, "ELECTION_START"),
                (election_b.id, "ELECTION_END"),
                (election_a.id, "ELECTION_START"),
                (election_a.id, "ELECTION_END"),
            ],
        )

    def test_election_calendar_events_api_filters_by_election_type_and_unit(self):
        now = timezone.now()
        extra_type = ElectionType.objects.create(code="API_ALT", name="Alt type")
        target_unit = OrganizationalUnit.objects.create(name="Calendar Target Unit", unit_type="FACULTY")
        other_unit = OrganizationalUnit.objects.create(name="Calendar Other Unit", unit_type="FACULTY")

        target = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Calendar Target Election",
            election_status=self.status_published,
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(hours=2),
            created_by_user=self.user,
            organizational_unit=target_unit,
        )
        ElectionLifecycleService.create_election_with_config(
            election_type=extra_type,
            name="Calendar Wrong Type",
            election_status=self.status_published,
            start_at=now + timedelta(hours=3),
            end_at=now + timedelta(hours=4),
            created_by_user=self.user,
            organizational_unit=target_unit,
        )
        ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Calendar Wrong Unit",
            election_status=self.status_published,
            start_at=now + timedelta(hours=5),
            end_at=now + timedelta(hours=6),
            created_by_user=self.user,
            organizational_unit=other_unit,
        )

        response = self.client.get(
            reverse("api_election_calendar_events"),
            {"election_type": self.election_type.code, "organizational_unit_id": target_unit.id},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        returned_ids = {row["election_id"] for row in response.data["events"]}
        self.assertEqual(returned_ids, {target.id})

    def test_election_calendar_events_api_rejects_invalid_organizational_unit_id(self):
        response = self.client.get(
            reverse("api_election_calendar_events"),
            {"organizational_unit_id": "invalid"},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 400)

    def test_election_calendar_reminders_api_returns_upcoming_events_within_horizon(self):
        now = timezone.now()
        upcoming = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Reminder Upcoming",
            election_status=self.status_published,
            start_at=now + timedelta(hours=6),
            end_at=now + timedelta(hours=12),
            created_by_user=self.user,
        )
        ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Reminder Far Future",
            election_status=self.status_published,
            start_at=now + timedelta(hours=96),
            end_at=now + timedelta(hours=120),
            created_by_user=self.user,
        )
        ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Reminder In Past",
            election_status=self.status_published,
            start_at=now - timedelta(hours=12),
            end_at=now - timedelta(hours=1),
            created_by_user=self.user,
        )

        response = self.client.get(
            reverse("api_election_calendar_reminders"),
            {"within_hours": 24},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["within_hours"], 24)
        returned_ids = {row["election_id"] for row in response.data["reminders"]}
        self.assertEqual(returned_ids, {upcoming.id})
        self.assertGreaterEqual(response.data["reminders"][0]["hours_until_event"], 0)

    def test_election_calendar_reminders_api_supports_type_and_unit_filters(self):
        now = timezone.now()
        extra_type = ElectionType.objects.create(code="API_REM_ALT", name="Reminder Alt")
        target_unit = OrganizationalUnit.objects.create(name="Reminder Unit Target", unit_type="FACULTY")
        other_unit = OrganizationalUnit.objects.create(name="Reminder Unit Other", unit_type="FACULTY")

        target = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Reminder Filter Target",
            election_status=self.status_published,
            start_at=now + timedelta(hours=4),
            end_at=now + timedelta(hours=8),
            created_by_user=self.user,
            organizational_unit=target_unit,
        )
        ElectionLifecycleService.create_election_with_config(
            election_type=extra_type,
            name="Reminder Wrong Type",
            election_status=self.status_published,
            start_at=now + timedelta(hours=10),
            end_at=now + timedelta(hours=12),
            created_by_user=self.user,
            organizational_unit=target_unit,
        )
        ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Reminder Wrong Unit",
            election_status=self.status_published,
            start_at=now + timedelta(hours=4),
            end_at=now + timedelta(hours=8),
            created_by_user=self.user,
            organizational_unit=other_unit,
        )

        response = self.client.get(
            reverse("api_election_calendar_reminders"),
            {
                "within_hours": 24,
                "election_type": self.election_type.code,
                "organizational_unit_id": target_unit.id,
            },
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        returned_ids = {row["election_id"] for row in response.data["reminders"]}
        self.assertEqual(returned_ids, {target.id})

    def test_election_calendar_reminders_api_rejects_invalid_within_hours(self):
        response = self.client.get(
            reverse("api_election_calendar_reminders"),
            {"within_hours": "invalid"},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 400)

    def test_top_turnout_elections_api_returns_ranked_data(self):
        now = timezone.now()

        election_high = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Turnout High",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=3),
            end_at=now + timedelta(hours=2),
            created_by_user=self.user,
        )
        election_mid = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Turnout Mid",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=3),
            end_at=now + timedelta(hours=2),
            created_by_user=self.user,
        )
        election_hidden = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Turnout Hidden",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=3),
            end_at=now + timedelta(hours=2),
            created_by_user=self.user,
        )
        election_hidden.schedule.results_publish_at = now + timedelta(hours=1)
        election_hidden.schedule.save(update_fields=["results_publish_at"])

        high_candidate = ElectionCandidate.objects.create(
            election=election_high,
            person=self.person1,
            candidate_number=1,
            is_approved=True,
        )
        mid_candidate = ElectionCandidate.objects.create(
            election=election_mid,
            person=self.person1,
            candidate_number=1,
            is_approved=True,
        )
        hidden_candidate = ElectionCandidate.objects.create(
            election=election_hidden,
            person=self.person1,
            candidate_number=1,
            is_approved=True,
        )

        VotingEligibility.objects.create(
            election=election_high,
            person=self.person1,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingEligibility.objects.create(
            election=election_mid,
            person=self.person1,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingEligibility.objects.create(
            election=election_mid,
            person=self.person2,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingEligibility.objects.create(
            election=election_hidden,
            person=self.person1,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )

        VotingService.issue_token(
            election=election_high,
            person=self.person1,
            raw_token="top-high-token",
        )
        VotingService.cast_vote(
            election=election_high,
            person=self.person1,
            raw_token="top-high-token",
            candidate_ids=[high_candidate.id],
            anonymous_key="top-high-anon",
        )

        VotingService.issue_token(
            election=election_mid,
            person=self.person1,
            raw_token="top-mid-token",
        )
        VotingService.cast_vote(
            election=election_mid,
            person=self.person1,
            raw_token="top-mid-token",
            candidate_ids=[mid_candidate.id],
            anonymous_key="top-mid-anon",
        )

        VotingService.issue_token(
            election=election_hidden,
            person=self.person1,
            raw_token="top-hidden-token",
        )
        VotingService.cast_vote(
            election=election_hidden,
            person=self.person1,
            raw_token="top-hidden-token",
            candidate_ids=[hidden_candidate.id],
            anonymous_key="top-hidden-anon",
        )

        ElectionLifecycleService.close_election(election_high, force=True)
        ElectionLifecycleService.close_election(election_mid, force=True)
        ElectionLifecycleService.close_election(election_hidden, force=True)

        response = self.client.get(
            reverse("api_top_turnout_elections"),
            {"top_n": 2},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["top_n"], 2)
        self.assertEqual(len(response.data["elections"]), 2)
        self.assertEqual(response.data["elections"][0]["election_id"], election_high.id)
        self.assertEqual(response.data["elections"][0]["turnout_percent"], "100.00")
        self.assertEqual(response.data["elections"][1]["election_id"], election_mid.id)
        self.assertEqual(response.data["elections"][1]["turnout_percent"], "50.00")

    def test_top_turnout_elections_api_rejects_invalid_top_n(self):
        response = self.client.get(
            reverse("api_top_turnout_elections"),
            {"top_n": "invalid"},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 400)

    def test_historical_trends_api_returns_ordered_points_with_summary(self):
        now = timezone.now()
        election_old = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Trend Old",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=3),
            end_at=now + timedelta(hours=1),
            created_by_user=self.user,
        )
        election_mid = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Trend Mid",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=3),
            end_at=now + timedelta(hours=2),
            created_by_user=self.user,
        )
        election_new = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Trend New",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=3),
            end_at=now + timedelta(hours=3),
            created_by_user=self.user,
        )
        election_hidden = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Trend Hidden",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=3),
            end_at=now + timedelta(hours=4),
            created_by_user=self.user,
        )
        election_hidden.schedule.results_publish_at = now + timedelta(days=1)
        election_hidden.schedule.save(update_fields=["results_publish_at"])

        old_candidate = ElectionCandidate.objects.create(
            election=election_old,
            person=self.person1,
            candidate_number=1,
            is_approved=True,
        )
        mid_candidate = ElectionCandidate.objects.create(
            election=election_mid,
            person=self.person1,
            candidate_number=1,
            is_approved=True,
        )
        hidden_candidate = ElectionCandidate.objects.create(
            election=election_hidden,
            person=self.person1,
            candidate_number=1,
            is_approved=True,
        )

        VotingEligibility.objects.create(
            election=election_old,
            person=self.person1,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingEligibility.objects.create(
            election=election_mid,
            person=self.person1,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingEligibility.objects.create(
            election=election_mid,
            person=self.person2,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingEligibility.objects.create(
            election=election_new,
            person=self.person1,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingEligibility.objects.create(
            election=election_hidden,
            person=self.person1,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )

        VotingService.issue_token(
            election=election_old,
            person=self.person1,
            raw_token="trend-old-token",
        )
        VotingService.cast_vote(
            election=election_old,
            person=self.person1,
            raw_token="trend-old-token",
            candidate_ids=[old_candidate.id],
            anonymous_key="trend-old-anon",
        )
        VotingService.issue_token(
            election=election_mid,
            person=self.person1,
            raw_token="trend-mid-token",
        )
        VotingService.cast_vote(
            election=election_mid,
            person=self.person1,
            raw_token="trend-mid-token",
            candidate_ids=[mid_candidate.id],
            anonymous_key="trend-mid-anon",
        )
        VotingService.issue_token(
            election=election_hidden,
            person=self.person1,
            raw_token="trend-hidden-token",
        )
        VotingService.cast_vote(
            election=election_hidden,
            person=self.person1,
            raw_token="trend-hidden-token",
            candidate_ids=[hidden_candidate.id],
            anonymous_key="trend-hidden-anon",
        )

        ElectionLifecycleService.close_election(election_old, force=True)
        ElectionLifecycleService.close_election(election_mid, force=True)
        ElectionLifecycleService.close_election(election_new, force=True)
        ElectionLifecycleService.close_election(election_hidden, force=True)

        response = self.client.get(
            reverse("api_historical_trends"),
            {"window": 2},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["window"], 2)
        self.assertEqual(response.data["summary"]["points_count"], 2)
        self.assertEqual(len(response.data["points"]), 2)
        self.assertEqual(response.data["points"][0]["election_id"], election_mid.id)
        self.assertEqual(response.data["points"][1]["election_id"], election_new.id)
        self.assertEqual(response.data["summary"]["max_turnout_percent"], "50.00")
        self.assertEqual(response.data["summary"]["min_turnout_percent"], "0.00")
        self.assertEqual(response.data["summary"]["average_turnout_percent"], "25.00")
        returned_ids = [point["election_id"] for point in response.data["points"]]
        self.assertNotIn(election_hidden.id, returned_ids)

    def test_historical_trends_api_rejects_invalid_window(self):
        response = self.client.get(
            reverse("api_historical_trends"),
            {"window": 0},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 400)

    def test_top_turnout_elections_api_hides_sensitive_fields_for_user_role(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Public Top Turnout",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=2),
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
            raw_token="public-top-token",
        )
        VotingService.cast_vote(
            election=election,
            person=self.person1,
            raw_token="public-top-token",
            candidate_ids=[candidate.id],
            anonymous_key="public-top-anon",
        )
        ElectionLifecycleService.close_election(election, force=True)

        response = self.client.get(
            reverse("api_top_turnout_elections"),
            {"top_n": 1},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.USER,
        )
        self.assertEqual(response.status_code, 200)
        row = response.data["elections"][0]
        self.assertIn("rank", row)
        self.assertIn("turnout_percent", row)
        self.assertNotIn("election_id", row)
        self.assertNotIn("election_name", row)
        self.assertNotIn("eligible_voters_count", row)
        self.assertNotIn("voters_count", row)

    def test_historical_trends_api_hides_sensitive_fields_for_user_role(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Public Trend Election",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=2),
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
            raw_token="public-trend-token",
        )
        VotingService.cast_vote(
            election=election,
            person=self.person1,
            raw_token="public-trend-token",
            candidate_ids=[candidate.id],
            anonymous_key="public-trend-anon",
        )
        ElectionLifecycleService.close_election(election, force=True)

        response = self.client.get(
            reverse("api_historical_trends"),
            {"window": 1},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.USER,
        )
        self.assertEqual(response.status_code, 200)
        point = response.data["points"][0]
        self.assertIn("sequence", point)
        self.assertIn("turnout_percent", point)
        self.assertNotIn("election_id", point)
        self.assertNotIn("election_name", point)
        self.assertNotIn("eligible_voters_count", point)
        self.assertNotIn("voters_count", point)

    def test_election_analytics_api_hides_sensitive_fields_for_user_role(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Public Analytics Election",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=2),
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
            raw_token="public-analytics-token",
        )
        VotingService.cast_vote(
            election=election,
            person=self.person1,
            raw_token="public-analytics-token",
            candidate_ids=[candidate.id],
            anonymous_key="public-analytics-anon",
        )
        ElectionLifecycleService.close_election(election, force=True)

        response = self.client.get(
            reverse("api_election_analytics", kwargs={"election_id": election.id}),
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.USER,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data["kpi"].keys()), {"turnout_percent"})
        self.assertIn("candidate_number", response.data["candidate_support"][0])
        self.assertIn("votes_percent", response.data["candidate_support"][0])
        self.assertNotIn("candidate_name", response.data["candidate_support"][0])
        self.assertNotIn("votes_count", response.data["candidate_support"][0])
        self.assertIn("unit_label", response.data["turnout_by_unit"][0])
        self.assertNotIn("eligible_count", response.data["turnout_by_unit"][0])

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

    def test_result_pdf_generation_and_download(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="PDF Report Election",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=2),
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
            raw_token="pdf-report-token",
        )
        VotingService.cast_vote(
            election=election,
            person=self.person1,
            raw_token="pdf-report-token",
            candidate_ids=[candidate.id],
            anonymous_key="pdf-report-anon",
        )
        ElectionLifecycleService.close_election(election, force=True)

        generate_response = self.client.post(
            reverse("api_election_result_pdf_generate", kwargs={"election_id": election.id}),
            {},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(generate_response.status_code, 201)
        document_id = generate_response.data["document_id"]
        self.assertTrue(
            GeneratedDocument.objects.filter(
                id=document_id,
                document_type=GeneratedDocument.DocumentType.RESULT_PDF,
                election=election,
            ).exists()
        )

        download_response = self.client.get(
            reverse("api_generated_document_download", kwargs={"document_id": document_id}),
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(download_response.status_code, 200)
        pdf_bytes = b"".join(download_response.streaming_content)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-1.4"))

    def test_analytics_api_returns_kpi_and_candidate_support(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Analytics Election",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=3),
            end_at=now + timedelta(hours=2),
            created_by_user=self.user,
        )
        unit = OrganizationalUnit.objects.create(name="Faculty A", unit_type="FACULTY")
        self.person1.organizational_unit = unit
        self.person1.save(update_fields=["organizational_unit"])

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
        VotingEligibility.objects.create(
            election=election,
            person=self.person2,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingService.issue_token(
            election=election,
            person=self.person1,
            raw_token="analytics-token",
        )
        VotingService.cast_vote(
            election=election,
            person=self.person1,
            raw_token="analytics-token",
            candidate_ids=[candidate1.id],
            anonymous_key="analytics-anon",
        )
        ElectionLifecycleService.close_election(election, force=True)

        response = self.client.get(
            reverse("api_election_analytics", kwargs={"election_id": election.id}),
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["kpi"]["eligible_voters_count"], 2)
        self.assertEqual(response.data["kpi"]["voters_count"], 1)
        self.assertEqual(response.data["kpi"]["turnout_percent"], "50.00")
        self.assertEqual(len(response.data["candidate_support"]), 2)
        self.assertEqual(response.data["candidate_support"][0]["candidate_id"], candidate1.id)
        self.assertEqual(response.data["candidate_support"][0]["votes_count"], 1)
        self.assertEqual(response.data["candidate_support"][1]["candidate_id"], candidate2.id)
        self.assertEqual(response.data["candidate_support"][1]["votes_count"], 0)
    def test_analytics_api_aggregation_is_consistent_across_sections(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Aggregation Consistency Election",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=3),
            end_at=now + timedelta(hours=2),
            created_by_user=self.user,
        )
        unit_a = OrganizationalUnit.objects.create(name="Faculty Stats A", unit_type="FACULTY")
        unit_b = OrganizationalUnit.objects.create(name="Faculty Stats B", unit_type="FACULTY")

        user_model = get_user_model()
        user3 = user_model.objects.create_user(
            username="api_user_3",
            email="api_user_3@example.com",
            password="secret123",
        )
        user4 = user_model.objects.create_user(
            username="api_user_4",
            email="api_user_4@example.com",
            password="secret123",
        )
        person3 = Person.objects.create(
            user=user3,
            first_name="Marek",
            last_name="Stat",
            student_or_employee_no="API-003",
            organizational_unit=unit_b,
        )
        person4 = Person.objects.create(
            user=user4,
            first_name="Olga",
            last_name="Stat",
            student_or_employee_no="API-004",
            organizational_unit=unit_a,
        )
        self.person1.organizational_unit = unit_a
        self.person1.save(update_fields=["organizational_unit"])
        self.person2.organizational_unit = unit_b
        self.person2.save(update_fields=["organizational_unit"])

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
        VotingEligibility.objects.create(
            election=election,
            person=self.person2,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingEligibility.objects.create(
            election=election,
            person=person3,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )
        VotingEligibility.objects.create(
            election=election,
            person=person4,
            eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
        )

        VotingService.issue_token(
            election=election,
            person=self.person1,
            raw_token="agg-token-1",
        )
        VotingService.cast_vote(
            election=election,
            person=self.person1,
            raw_token="agg-token-1",
            candidate_ids=[candidate1.id],
            anonymous_key="agg-anon-1",
        )
        VotingService.issue_token(
            election=election,
            person=self.person2,
            raw_token="agg-token-2",
        )
        VotingService.cast_vote(
            election=election,
            person=self.person2,
            raw_token="agg-token-2",
            candidate_ids=[candidate2.id],
            anonymous_key="agg-anon-2",
        )
        ElectionLifecycleService.close_election(election, force=True)

        response = self.client.get(
            reverse("api_election_analytics", kwargs={"election_id": election.id}),
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        kpi = response.data["kpi"]
        candidate_vote_sum = sum(item["votes_count"] for item in response.data["candidate_support"])
        unit_eligible_sum = sum(item["eligible_count"] for item in response.data["turnout_by_unit"])
        unit_voted_sum = sum(item["voted_count"] for item in response.data["turnout_by_unit"])

        self.assertEqual(kpi["total_votes_cast"], candidate_vote_sum)
        self.assertEqual(kpi["eligible_voters_count"], unit_eligible_sum)
        self.assertEqual(kpi["voters_count"], unit_voted_sum)
        self.assertEqual(kpi["eligible_voters_count"], 4)
        self.assertEqual(kpi["voters_count"], 2)
        self.assertEqual(kpi["turnout_percent"], "50.00")

    def test_top_turnout_elections_api_uses_voters_count_as_tie_breaker(self):
        now = timezone.now()
        election_a = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Tie Turnout A",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=2),
            end_at=now + timedelta(hours=1),
            created_by_user=self.user,
        )
        election_b = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Tie Turnout B",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=2),
            end_at=now + timedelta(hours=1),
            created_by_user=self.user,
        )
        user_model = get_user_model()
        extra_users = []
        for idx in range(5, 9):
            extra_user = user_model.objects.create_user(
                username=f"api_user_{idx}",
                email=f"api_user_{idx}@example.com",
                password="secret123",
            )
            extra_person = Person.objects.create(
                user=extra_user,
                first_name=f"Extra{idx}",
                last_name="Tie",
                student_or_employee_no=f"API-00{idx}",
            )
            extra_users.append(extra_person)

        candidate_a = ElectionCandidate.objects.create(
            election=election_a,
            person=self.person1,
            candidate_number=1,
            is_approved=True,
        )
        candidate_b = ElectionCandidate.objects.create(
            election=election_b,
            person=self.person1,
            candidate_number=1,
            is_approved=True,
        )

        # Election A: 1/2 voters (50%)
        for person in [self.person1, self.person2]:
            VotingEligibility.objects.create(
                election=election_a,
                person=person,
                eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
            )
        VotingService.issue_token(election=election_a, person=self.person1, raw_token="tie-a-token")
        VotingService.cast_vote(
            election=election_a,
            person=self.person1,
            raw_token="tie-a-token",
            candidate_ids=[candidate_a.id],
            anonymous_key="tie-a-anon",
        )

        # Election B: 2/4 voters (also 50%, but higher voters_count)
        for person in [self.person1, self.person2, extra_users[0], extra_users[1]]:
            VotingEligibility.objects.create(
                election=election_b,
                person=person,
                eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
            )
        VotingService.issue_token(election=election_b, person=self.person1, raw_token="tie-b-token-1")
        VotingService.cast_vote(
            election=election_b,
            person=self.person1,
            raw_token="tie-b-token-1",
            candidate_ids=[candidate_b.id],
            anonymous_key="tie-b-anon-1",
        )
        VotingService.issue_token(election=election_b, person=self.person2, raw_token="tie-b-token-2")
        VotingService.cast_vote(
            election=election_b,
            person=self.person2,
            raw_token="tie-b-token-2",
            candidate_ids=[candidate_b.id],
            anonymous_key="tie-b-anon-2",
        )

        ElectionLifecycleService.close_election(election_a, force=True)
        ElectionLifecycleService.close_election(election_b, force=True)

        response = self.client.get(
            reverse("api_top_turnout_elections"),
            {"top_n": 2},
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["elections"][0]["election_id"], election_b.id)
        self.assertEqual(response.data["elections"][1]["election_id"], election_a.id)
        self.assertEqual(response.data["elections"][0]["turnout_percent"], "50.00")
        self.assertEqual(response.data["elections"][1]["turnout_percent"], "50.00")

    def test_analytics_api_rejects_non_closed_election(self):
        now = timezone.now()
        election = ElectionLifecycleService.create_election_with_config(
            election_type=self.election_type,
            name="Analytics Open Election",
            election_status=self.status_in_progress,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            created_by_user=self.user,
        )
        response = self.client.get(
            reverse("api_election_analytics", kwargs={"election_id": election.id}),
            format="json",
            HTTP_X_USER_ROLE=UserRole.Role.ADMIN,
        )
        self.assertEqual(response.status_code, 400)
