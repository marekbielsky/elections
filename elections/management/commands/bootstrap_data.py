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
    help = "Seeduje dane referencyjne oraz opcjonalne dane demonstracyjne dla środowiska lokalnego lub produkcyjnego."

    def add_arguments(self, parser):
        parser.add_argument(
            "--env",
            choices=["local", "prod"],
            default="local",
            help="Docelowe środowisko seeda. Użyj local dla danych demo i prod dla bezpiecznych danych referencyjnych.",
        )
        parser.add_argument(
            "--with-demo",
            action="store_true",
            help="W połączeniu z --env local tworzy użytkowników demo i przykładowe głosowania.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        target_env = options["env"]
        with_demo = options["with_demo"]

        self.stdout.write(self.style.NOTICE(f"Seedowanie danych dla środowiska: {target_env}"))
        self._seed_reference_data()

        if target_env == "local" and with_demo:
            self._seed_local_demo_data()
            self.stdout.write(self.style.SUCCESS("Dane demonstracyjne zostały zseedowane dla środowiska lokalnego."))
        elif target_env == "local":
            self.stdout.write(self.style.WARNING("Tryb local bez --with-demo: utworzono tylko dane referencyjne."))
        else:
            self.stdout.write(self.style.SUCCESS("Zseedowano bezpieczne dane referencyjne dla produkcji."))

    def _seed_reference_data(self):
        election_types = [
            ("STUDENT_COUNCIL", "Samorząd studencki", "Wybory przedstawicieli samorządu studenckiego."),
            ("DEAN", "Dziekan", "Wybory na stanowisko dziekana."),
            ("CLASS_REP", "Starosta roku", "Wybory starosty roku."),
            ("EMPLOYEE_MONTH", "Pracownik miesiąca", "Głosowanie wyróżniające pracowników w organizacji."),
            ("OTHER", "Inne", "Inny proces głosowania organizacyjnego."),
            (
                "PRESIDENTIAL_RP",
                "Wybory prezydenckie (Polska)",
                "Powszechne wybory Prezydenta Rzeczypospolitej Polskiej.",
            ),
            (
                "PARLIAMENTARY_RP",
                "Wybory parlamentarne (Polska)",
                "Powszechne wybory do Sejmu i Senatu Rzeczypospolitej Polskiej.",
            ),
            (
                "EUROPEAN_PARLIAMENT_RP",
                "Wybory do Parlamentu Europejskiego (Polska)",
                "Powszechne wybory w Polsce do Parlamentu Europejskiego.",
            ),
        ]
        election_statuses = [
            ("DRAFT", "Szkic", "Szkic głosowania przygotowany przez administratorów."),
            ("PUBLISHED", "Opublikowane", "Głosowanie zostało opublikowane i jest widoczne dla wyborców."),
            ("IN_PROGRESS", "W trakcie", "Głosowanie jest aktywne i trwa oddawanie głosów."),
            ("CLOSED", "Zamknięte", "Głosowanie zakończone; wyniki mogą być jeszcze przetwarzane."),
            ("RESULTS_PUBLISHED", "Wyniki opublikowane", "Wyniki zostały zatwierdzone i opublikowane."),
            ("ARCHIVED", "Zarchiwizowane", "Historyczne głosowanie zarchiwizowane do wglądu."),
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

        self.stdout.write(self.style.SUCCESS("Słowniki referencyjne zostały zseedowane."))

    def _seed_local_demo_data(self):
        user_model = get_user_model()
        admin_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "admin12345")
        demo_password = os.getenv("BOOTSTRAP_DEMO_PASSWORD", "demo12345")

        demo_unit, _ = OrganizationalUnit.objects.update_or_create(
            name="Rzeczpospolita Polska",
            defaults={"unit_type": "KRAJ", "is_active": True},
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

        voter_user, voter_created = user_model.objects.get_or_create(
            username="voter_demo",
            defaults={"email": "voter_demo@example.com", "is_active": True},
        )
        if voter_created:
            voter_user.set_password(demo_password)
            voter_user.save(update_fields=["password"])
        voter_person, _ = Person.objects.update_or_create(
            user=voter_user,
            defaults={
                "first_name": "Jan",
                "last_name": "Wyborca",
                "student_or_employee_no": "DEMO-VOTER-001",
                "organizational_unit": demo_unit,
            },
        )

        now = timezone.now()
        politicians = [
            ("donald_tusk", "Donald", "Tusk", "politician_donald_tusk@example.com", "PL-POL-001"),
            (
                "jaroslaw_kaczynski",
                "Jarosław",
                "Kaczyński",
                "politician_jaroslaw_kaczynski@example.com",
                "PL-POL-002",
            ),
            (
                "rafal_trzaskowski",
                "Rafał",
                "Trzaskowski",
                "politician_rafal_trzaskowski@example.com",
                "PL-POL-003",
            ),
            ("szymon_holownia", "Szymon", "Hołownia", "politician_szymon_holownia@example.com", "PL-POL-004"),
            (
                "wl_kosiniak_kamysz",
                "Władysław",
                "Kosiniak-Kamysz",
                "politician_wladyslaw_kosiniak_kamysz@example.com",
                "PL-POL-005",
            ),
            ("robert_biedron", "Robert", "Biedroń", "politician_robert_biedron@example.com", "PL-POL-006"),
            ("krzysztof_bosak", "Krzysztof", "Bosak", "politician_krzysztof_bosak@example.com", "PL-POL-007"),
            ("slawomir_mentzen", "Sławomir", "Mentzen", "politician_slawomir_mentzen@example.com", "PL-POL-008"),
            (
                "mateusz_morawiecki",
                "Mateusz",
                "Morawiecki",
                "politician_mateusz_morawiecki@example.com",
                "PL-POL-009",
            ),
            ("adrian_zandberg", "Adrian", "Zandberg", "politician_adrian_zandberg@example.com", "PL-POL-010"),
        ]
        candidate_people = []
        for username, first_name, last_name, email, identifier in politicians:
            politician_user, politician_created = user_model.objects.get_or_create(
                username=username,
                defaults={"email": email, "is_active": True},
            )
            if politician_created:
                politician_user.set_password(demo_password)
                politician_user.save(update_fields=["password"])

            person, _ = Person.objects.update_or_create(
                user=politician_user,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "student_or_employee_no": identifier,
                    "organizational_unit": demo_unit,
                },
            )
            candidate_people.append(person)

        election_status = ElectionStatus.objects.get(code="PUBLISHED")
        elections_to_seed = [
            {
                "type_code": "PRESIDENTIAL_RP",
                "name": "Wybory Prezydenta RP 2025 (demo)",
                "description": "Demonstracyjne głosowanie oparte na realnej nazwie wyborów prezydenckich w Polsce.",
                "start_days_offset": 0,
            },
            {
                "type_code": "PARLIAMENTARY_RP",
                "name": "Wybory Parlamentarne RP 2023 (demo)",
                "description": "Demonstracyjne głosowanie oparte na realnej nazwie wyborów parlamentarnych w Polsce.",
                "start_days_offset": 21,
            },
            {
                "type_code": "EUROPEAN_PARLIAMENT_RP",
                "name": "Wybory do Parlamentu Europejskiego 2024 (demo)",
                "description": "Demonstracyjne głosowanie oparte na realnej nazwie wyborów europejskich w Polsce.",
                "start_days_offset": 42,
            },
        ]

        for election_index, election_seed in enumerate(elections_to_seed):
            election_type = ElectionType.objects.get(code=election_seed["type_code"])
            election, _ = Election.objects.update_or_create(
                name=election_seed["name"],
                defaults={
                    "election_type": election_type,
                    "election_status": election_status,
                    "organizational_unit": demo_unit,
                    "description": election_seed["description"],
                    "is_secret": True,
                    "created_by_user": admin_user,
                },
            )

            start_at = now + timedelta(days=election_seed["start_days_offset"])
            end_at = start_at + timedelta(days=14)
            ElectionSchedule.objects.update_or_create(
                election=election,
                defaults={
                    "start_at": start_at,
                    "end_at": end_at,
                    "results_publish_at": end_at + timedelta(days=1),
                },
            )
            VotingRule.objects.update_or_create(
                election=election,
                defaults={
                    "min_choices": 1,
                    "max_choices": 1,
                    "allow_blank_vote": False,
                    "allow_vote_change": False,
                    "requires_turnout_threshold": False,
                    "turnout_threshold_percent": None,
                },
            )

            for candidate_number, candidate_person in enumerate(candidate_people, start=1):
                ElectionCandidate.objects.update_or_create(
                    election=election,
                    person=candidate_person,
                    defaults={
                        "candidate_number": candidate_number,
                        "campaign_description": (
                            f"{candidate_person.first_name} {candidate_person.last_name} "
                            f"- kandydat demonstracyjny w głosowaniu nr {election_index + 1}."
                        ),
                        "is_approved": True,
                        "approved_at": now,
                    },
                )

            VotingEligibility.objects.update_or_create(
                election=election,
                person=voter_person,
                defaults={"eligibility_status": VotingEligibility.EligibilityStatus.GRANTED},
            )

