import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from elections.models import (
    Election,
    ElectionCandidate,
    ElectionSchedule,
    ElectionStatus,
    ElectionType,
    OrganizationalUnit,
    Person,
    VotingEligibility,
    VotingRule,
)


class Command(BaseCommand):
    help = "Bootstrap reference and optional demo data for local or production environments."

    def add_arguments(self, parser):
        parser.add_argument(
            "--env",
            choices=["local", "prod"],
            default="local",
            help="Target bootstrap environment. Use local for demo data and prod for safe reference data only.",
        )
        parser.add_argument(
            "--with-demo",
            action="store_true",
            help="When used with --env local, creates demo users and election records.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        target_env = options["env"]
        with_demo = options["with_demo"]

        self.stdout.write(self.style.NOTICE(f"Bootstrapping data for env: {target_env}"))
        self._seed_reference_data()

        if target_env == "local" and with_demo:
            self._seed_local_demo_data()
            self.stdout.write(self.style.SUCCESS("Demo data seeded for local environment."))
        elif target_env == "local":
            self.stdout.write(self.style.WARNING("Local mode without --with-demo: only reference data created."))
        else:
            self.stdout.write(self.style.SUCCESS("Production-safe reference data seeded."))

    def _seed_reference_data(self):
        election_types = [
            ("STUDENT_COUNCIL", "Student Council", "Elections for student council representatives."),
            ("DEAN", "Dean", "Elections for dean position."),
            ("CLASS_REP", "Class Representative", "Elections for class representative."),
            ("EMPLOYEE_MONTH", "Employee of the Month", "Recognition voting in organizations."),
            ("OTHER", "Other", "Other organizational voting process."),
        ]
        election_statuses = [
            ("DRAFT", "Draft", "Draft election prepared by administrators."),
            ("PUBLISHED", "Published", "Election is published and visible to voters."),
            ("IN_PROGRESS", "In progress", "Election is active and voting is open."),
            ("CLOSED", "Closed", "Voting ended; results may still be processed."),
            ("RESULTS_PUBLISHED", "Results published", "Results were finalized and published."),
            ("ARCHIVED", "Archived", "Historical election archived for reference."),
        ]

        for code, name, description in election_types:
            ElectionType.objects.update_or_create(
                code=code,
                defaults={"name": name, "description": description},
            )
        for code, name, description in election_statuses:
            ElectionStatus.objects.update_or_create(
                code=code,
                defaults={"name": name, "description": description},
            )

        self.stdout.write(self.style.SUCCESS("Reference dictionaries seeded."))

    def _seed_local_demo_data(self):
        user_model = get_user_model()
        admin_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "admin12345")
        demo_password = os.getenv("BOOTSTRAP_DEMO_PASSWORD", "demo12345")

        demo_unit, _ = OrganizationalUnit.objects.update_or_create(
            name="Faculty of Computer Science",
            defaults={"unit_type": "FACULTY", "is_active": True},
        )

        admin_user, admin_created = user_model.objects.get_or_create(
            username="admin_demo",
            defaults={
                "email": "admin_demo@example.com",
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        if admin_created:
            admin_user.set_password(admin_password)
            admin_user.save(update_fields=["password"])

        candidate_user, candidate_created = user_model.objects.get_or_create(
            username="candidate_demo",
            defaults={"email": "candidate_demo@example.com", "is_active": True},
        )
        if candidate_created:
            candidate_user.set_password(demo_password)
            candidate_user.save(update_fields=["password"])

        voter_user, voter_created = user_model.objects.get_or_create(
            username="voter_demo",
            defaults={"email": "voter_demo@example.com", "is_active": True},
        )
        if voter_created:
            voter_user.set_password(demo_password)
            voter_user.save(update_fields=["password"])

        candidate_person, _ = Person.objects.update_or_create(
            user=candidate_user,
            defaults={
                "first_name": "Anna",
                "last_name": "Kandydat",
                "student_or_employee_no": "DEMO-CAND-001",
                "organizational_unit": demo_unit,
            },
        )
        voter_person, _ = Person.objects.update_or_create(
            user=voter_user,
            defaults={
                "first_name": "Jan",
                "last_name": "Wyborca",
                "student_or_employee_no": "DEMO-VOTER-001",
                "organizational_unit": demo_unit,
            },
        )

        election_type = ElectionType.objects.get(code="STUDENT_COUNCIL")
        election_status = ElectionStatus.objects.get(code="PUBLISHED")

        demo_election, _ = Election.objects.update_or_create(
            name="Demo Student Council Election",
            defaults={
                "election_type": election_type,
                "election_status": election_status,
                "organizational_unit": demo_unit,
                "description": "Locally bootstrapped demo election.",
                "is_secret": True,
                "created_by_user": admin_user,
            },
        )

        now = timezone.now()
        ElectionSchedule.objects.update_or_create(
            election=demo_election,
            defaults={
                "start_at": now,
                "end_at": now + timedelta(days=7),
                "results_publish_at": now + timedelta(days=8),
            },
        )
        VotingRule.objects.update_or_create(
            election=demo_election,
            defaults={
                "min_choices": 1,
                "max_choices": 1,
                "allow_blank_vote": False,
                "allow_vote_change": False,
                "requires_turnout_threshold": False,
                "turnout_threshold_percent": None,
            },
        )
        ElectionCandidate.objects.update_or_create(
            election=demo_election,
            person=candidate_person,
            defaults={
                "candidate_number": 1,
                "campaign_description": "Demo candidate profile.",
                "is_approved": True,
                "approved_at": now,
            },
        )
        VotingEligibility.objects.update_or_create(
            election=demo_election,
            person=voter_person,
            defaults={"eligibility_status": VotingEligibility.EligibilityStatus.GRANTED},
        )

