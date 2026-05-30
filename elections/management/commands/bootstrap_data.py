import os

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
    Permission,
    Person,
    Role,
    RolePermission,
    UserRole,
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
            help="W połączeniu z --env local tworzy użytkowników demo, komitety i polityków (bez wyborów).",
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
            self._seed_local_demo_data()
            self._seed_prod_elections_and_candidates()
            self.stdout.write(
                self.style.SUCCESS(
                    "Zseedowano dane dla produkcji: słowniki referencyjne, użytkowników/komitety/polityków oraz kandydatury."
                )
            )

    def _seed_reference_data(self):
        role_definitions = [
            (UserRole.Role.ADMIN, "Administrator", "Rola administracyjna z pełnym dostępem."),
            (UserRole.Role.USER, "Użytkownik", "Rola użytkownika końcowego."),
            (UserRole.Role.AUDITOR, "Audytor", "Rola audytora z dostępem tylko do odczytu."),
        ]
        permission_definitions = [
            ("election.read", "Podgląd wyborów", "elections"),
            ("election.create", "Tworzenie wyborów", "elections"),
            ("election.manage_state", "Zarządzanie statusem wyborów", "elections"),
            ("election.draft.view", "Podgląd roboczych wyborów", "elections"),
            ("candidate.read", "Podgląd kandydatów", "candidates"),
            ("candidate.create", "Tworzenie kandydatów", "candidates"),
            ("committee.read", "Podgląd komitetów", "committees"),
            ("result.read", "Podgląd wyników", "results"),
            ("voting.token.issue", "Wydawanie tokenów głosowania", "voting"),
            ("voting.cast", "Oddawanie głosu", "voting"),
            ("admin.panel.view", "Dostęp do panelu administracyjnego", "admin"),
            ("admin.users.view", "Podgląd użytkowników", "admin"),
            ("admin.role.assign", "Przypisywanie ról", "admin"),
            ("admin.permission.assign", "Przypisywanie uprawnień", "admin"),
            ("admin.election.lifecycle", "Zarządzanie cyklem życia wyborów", "admin"),
        ]
        role_permissions = {
            UserRole.Role.ADMIN: {
                "election.read",
                "election.create",
                "election.manage_state",
                "election.draft.view",
                "candidate.read",
                "candidate.create",
                "committee.read",
                "result.read",
                "voting.token.issue",
                "voting.cast",
                "admin.panel.view",
                "admin.users.view",
                "admin.role.assign",
                "admin.permission.assign",
                "admin.election.lifecycle",
            },
            UserRole.Role.USER: {
                "election.read",
                "candidate.read",
                "committee.read",
                "result.read",
                "voting.cast",
            },
            UserRole.Role.AUDITOR: {
                "election.read",
                "candidate.read",
                "committee.read",
                "result.read",
                "election.draft.view",
                "admin.panel.view",
            },
        }
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
        for code, name, description in role_definitions:
            Role.objects.update_or_create(
                code=code,
                defaults={"name": name, "description": description, "is_system": True},
            )
        for code, name, module in permission_definitions:
            Permission.objects.update_or_create(
                code=code,
                defaults={"name": name, "module": module},
            )

        for role_code, permission_codes in role_permissions.items():
            role = Role.objects.get(code=role_code)
            role.role_permissions.exclude(permission__code__in=permission_codes).delete()
            for permission_code in permission_codes:
                permission = Permission.objects.get(code=permission_code)
                RolePermission.objects.get_or_create(
                    role=role,
                    permission=permission,
                )

        self.stdout.write(self.style.SUCCESS("Słowniki referencyjne i RBAC minimum zostały zseedowane."))

    def _seed_local_demo_data(self):
        user_model = get_user_model()
        admin_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "admin12345")
        demo_password = os.getenv("BOOTSTRAP_DEMO_PASSWORD", "demo12345")
        sample_user_password = "Admin123!"

        country_unit, _ = OrganizationalUnit.objects.update_or_create(
            name="Rzeczpospolita Polska",
            defaults={"unit_type": "KRAJ", "is_active": True},
        )
        committee_units = [
            ("Komitet Obywatelski Rozwój", "KOMITET"),
            ("Komitet Samorządność i Przyszłość", "KOMITET"),
            ("Komitet Wspólna Odpowiedzialność", "KOMITET"),
            ("Komitet Nowa Energia", "KOMITET"),
        ]
        seeded_committees = []
        for committee_name, committee_type in committee_units:
            committee, _ = OrganizationalUnit.objects.update_or_create(
                name=committee_name,
                defaults={
                    "unit_type": committee_type,
                    "is_active": True,
                    "parent_unit": country_unit,
                },
            )
            seeded_committees.append(committee)

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
        else:
            admin_updates = []
            if not admin_user.is_staff:
                admin_user.is_staff = True
                admin_updates.append("is_staff")
            if not admin_user.is_superuser:
                admin_user.is_superuser = True
                admin_updates.append("is_superuser")
            if not admin_user.is_active:
                admin_user.is_active = True
                admin_updates.append("is_active")
            if admin_updates:
                admin_user.save(update_fields=admin_updates)
        UserRole.objects.update_or_create(
            user=admin_user,
            defaults={"role": UserRole.Role.ADMIN},
        )

        voter_user, voter_created = user_model.objects.get_or_create(
            username="voter_demo",
            defaults={"email": "voter_demo@example.com", "is_active": True},
        )
        if voter_created:
            voter_user.set_password(demo_password)
            voter_user.save(update_fields=["password"])
        UserRole.objects.update_or_create(
            user=voter_user,
            defaults={"role": UserRole.Role.USER},
        )
        self._upsert_person_for_user(
            user=voter_user,
            first_name="Jan",
            last_name="Wyborca",
            identifier="DEMO-VOTER-001",
            organizational_unit=seeded_committees[0],
        )
        auditor_user, auditor_created = user_model.objects.get_or_create(
            username="auditor_demo",
            defaults={"email": "auditor_demo@example.com", "is_active": True},
        )
        if auditor_created:
            auditor_user.set_password(demo_password)
            auditor_user.save(update_fields=["password"])
        UserRole.objects.update_or_create(
            user=auditor_user,
            defaults={"role": UserRole.Role.AUDITOR},
        )
        self._upsert_person_for_user(
            user=auditor_user,
            first_name="Ada",
            last_name="Audytor",
            identifier="DEMO-AUDITOR-001",
            organizational_unit=seeded_committees[1],
        )

        sample_users = [
            ("anna_kowalska", "Anna", "Kowalska", "anna.kowalska@example.com", "DEMO-USER-001"),
            ("piotr_nowak", "Piotr", "Nowak", "piotr.nowak@example.com", "DEMO-USER-002"),
            ("katarzyna_wisniewska", "Katarzyna", "Wiśniewska", "katarzyna.wisniewska@example.com", "DEMO-USER-003"),
            ("marek_wojcik", "Marek", "Wójcik", "marek.wojcik@example.com", "DEMO-USER-004"),
            ("aleksandra_kaminska", "Aleksandra", "Kamińska", "aleksandra.kaminska@example.com", "DEMO-USER-005"),
            ("lukasz_lewandowski", "Łukasz", "Lewandowski", "lukasz.lewandowski@example.com", "DEMO-USER-006"),
            ("magdalena_zielinska", "Magdalena", "Zielińska", "magdalena.zielinska@example.com", "DEMO-USER-007"),
            ("tomasz_szymanski", "Tomasz", "Szymański", "tomasz.szymanski@example.com", "DEMO-USER-008"),
            ("joanna_dabrowska", "Joanna", "Dąbrowska", "joanna.dabrowska@example.com", "DEMO-USER-009"),
            ("pawel_kozlowski", "Paweł", "Kozłowski", "pawel.kozlowski@example.com", "DEMO-USER-010"),
            ("jan_kowalski_2", "Jan", "Kowalski", "jan.kowalski2@example.com", "DEMO-USER-011"),
            ("adam_wisniewski", "Adam", "Wiśniewski", "adam.wisniewski@example.com", "DEMO-USER-012"),
            ("ewa_nowicka", "Ewa", "Nowicka", "ewa.nowicka@example.com", "DEMO-USER-013"),
            ("pawel_zawadzki", "Paweł", "Zawadzki", "pawel.zawadzki@example.com", "DEMO-USER-014"),
            ("agnieszka_krupa", "Agnieszka", "Krupa", "agnieszka.krupa@example.com", "DEMO-USER-015"),
            ("michal_kaczmarek", "Michał", "Kaczmarek", "michal.kaczmarek@example.com", "DEMO-USER-016"),
            ("karolina_sikora", "Karolina", "Sikora", "karolina.sikora@example.com", "DEMO-USER-017"),
            ("krzysztof_mazur", "Krzysztof", "Mazur", "krzysztof.mazur@example.com", "DEMO-USER-018"),
            ("monika_baran", "Monika", "Baran", "monika.baran@example.com", "DEMO-USER-019"),
            ("lukasz_jablonski", "Łukasz", "Jabłoński", "lukasz.jablonski@example.com", "DEMO-USER-020"),
        ]
        for index, (username, first_name, last_name, email, identifier) in enumerate(sample_users):
            demo_user, _ = user_model.objects.get_or_create(
                username=username,
                defaults={"email": email, "is_active": True},
            )
            demo_updates = []
            if demo_user.email != email:
                demo_user.email = email
                demo_updates.append("email")
            if not demo_user.is_active:
                demo_user.is_active = True
                demo_updates.append("is_active")
            demo_user.set_password(sample_user_password)
            demo_updates.append("password")
            demo_user.save(update_fields=demo_updates)
            UserRole.objects.update_or_create(
                user=demo_user,
                defaults={"role": UserRole.Role.USER},
            )
            self._upsert_person_for_user(
                user=demo_user,
                first_name=first_name,
                last_name=last_name,
                identifier=identifier,
                organizational_unit=seeded_committees[index % len(seeded_committees)],
            )

        politicians = [
            ("donald_tusk", "Donald", "Tusk", "politician_donald_tusk@example.com", "PL-POL-001"),
            ("jaroslaw_kaczynski", "Jarosław", "Kaczyński", "politician_jaroslaw_kaczynski@example.com", "PL-POL-002"),
            ("rafal_trzaskowski", "Rafał", "Trzaskowski", "politician_rafal_trzaskowski@example.com", "PL-POL-003"),
            ("szymon_holownia", "Szymon", "Hołownia", "politician_szymon_holownia@example.com", "PL-POL-004"),
            ("wl_kosiniak_kamysz", "Władysław", "Kosiniak-Kamysz", "politician_wladyslaw_kosiniak_kamysz@example.com", "PL-POL-005"),
            ("robert_biedron", "Robert", "Biedroń", "politician_robert_biedron@example.com", "PL-POL-006"),
            ("krzysztof_bosak", "Krzysztof", "Bosak", "politician_krzysztof_bosak@example.com", "PL-POL-007"),
            ("slawomir_mentzen", "Sławomir", "Mentzen", "politician_slawomir_mentzen@example.com", "PL-POL-008"),
            ("mateusz_morawiecki", "Mateusz", "Morawiecki", "politician_mateusz_morawiecki@example.com", "PL-POL-009"),
            ("adrian_zandberg", "Adrian", "Zandberg", "politician_adrian_zandberg@example.com", "PL-POL-010"),
        ]
        for index, (username, first_name, last_name, email, identifier) in enumerate(politicians):
            politician_user, politician_created = user_model.objects.get_or_create(
                username=username,
                defaults={"email": email, "is_active": True},
            )
            if politician_created:
                politician_user.set_password(demo_password)
                politician_user.save(update_fields=["password"])
            UserRole.objects.update_or_create(
                user=politician_user,
                defaults={"role": UserRole.Role.USER},
            )
            self._upsert_person_for_user(
                user=politician_user,
                first_name=first_name,
                last_name=last_name,
                identifier=identifier,
                organizational_unit=seeded_committees[index % len(seeded_committees)],
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Utworzono lokalne dane demo: 20 przykładowych użytkowników, komitety i polityków (bez wyborów)."
            )
        )

    def _upsert_person_for_user(self, *, user, first_name, last_name, identifier, organizational_unit):
        existing_for_user = Person.objects.filter(user=user).first()
        existing_for_identifier = Person.objects.filter(student_or_employee_no=identifier).first()

        if existing_for_identifier and existing_for_identifier.user_id != user.id:
            if existing_for_user and existing_for_user.pk != existing_for_identifier.pk:
                existing_for_user.delete()
            person = existing_for_identifier
        elif existing_for_user:
            person = existing_for_user
        elif existing_for_identifier:
            person = existing_for_identifier
        else:
            person = Person(user=user)

        person.user = user
        person.first_name = first_name
        person.last_name = last_name
        person.student_or_employee_no = identifier
        person.organizational_unit = organizational_unit
        person.save()

    def _seed_prod_elections_and_candidates(self):
        draft_status = ElectionStatus.objects.get(code="DRAFT")
        country_unit = OrganizationalUnit.objects.filter(name="Rzeczpospolita Polska").first()
        now = timezone.now()
        politicians = list(
            Person.objects.filter(student_or_employee_no__startswith="PL-POL-").order_by("student_or_employee_no")
        )
        if not politicians:
            return

        election_definitions = [
            (
                "Wybory prezydenckie RP (demo)",
                "PRESIDENTIAL_RP",
                "Pokazowa elekcja prezydencka do prezentacji listy kandydatów.",
            ),
            (
                "Wybory parlamentarne RP (demo)",
                "PARLIAMENTARY_RP",
                "Pokazowa elekcja parlamentarna do prezentacji listy kandydatów.",
            ),
            (
                "Wybory do PE w Polsce (demo)",
                "EUROPEAN_PARLIAMENT_RP",
                "Pokazowa elekcja do Parlamentu Europejskiego do prezentacji listy kandydatów.",
            ),
        ]
        for election_name, election_type_code, election_description in election_definitions:
            election_type = ElectionType.objects.get(code=election_type_code)
            election, _ = Election.objects.update_or_create(
                name=election_name,
                defaults={
                    "election_type": election_type,
                    "election_status": draft_status,
                    "organizational_unit": country_unit,
                    "description": election_description,
                    "is_secret": False,
                },
            )
            ElectionSchedule.objects.update_or_create(
                election=election,
                defaults={
                    "start_at": now + timezone.timedelta(days=7),
                    "end_at": now + timezone.timedelta(days=14),
                    "results_publish_at": now + timezone.timedelta(days=15),
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
            for candidate_number, person in enumerate(politicians, start=1):
                ElectionCandidate.objects.update_or_create(
                    election=election,
                    person=person,
                    defaults={
                        "candidate_number": candidate_number,
                        "campaign_description": "Kandydat demonstracyjny do prezentacji frontendu.",
                        "is_approved": True,
                        "approved_at": now,
                    },
                )
                VotingEligibility.objects.update_or_create(
                    election=election,
                    person=person,
                    defaults={
                        "eligibility_status": VotingEligibility.EligibilityStatus.GRANTED,
                        "revoked_at": None,
                        "revocation_reason": "",
                    },
                )
