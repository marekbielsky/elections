from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("elections", "0006_userrole"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Permission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=100, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("module", models.CharField(blank=True, max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="Role",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=50, unique=True)),
                ("name", models.CharField(max_length=100)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("is_system", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="AdminActionLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action_type", models.CharField(max_length=50)),
                ("target_table", models.CharField(max_length=100)),
                ("target_id", models.BigIntegerField(blank=True, null=True)),
                ("action_details", models.TextField(blank=True)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "performed_by_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="admin_action_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="admin_action_logs",
                        to="elections.role",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="RolePermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("granted_at", models.DateTimeField(auto_now_add=True)),
                (
                    "granted_by_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="granted_role_permissions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "permission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_permissions",
                        to="elections.permission",
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_permissions",
                        to="elections.role",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="rolepermission",
            constraint=models.UniqueConstraint(fields=("role", "permission"), name="unique_permission_per_role"),
        ),
        migrations.RunSQL(
            sql="""
                CREATE TRIGGER elections_adminlog_election_status_update
                AFTER UPDATE OF election_status_id ON elections_election
                FOR EACH ROW
                WHEN OLD.election_status_id != NEW.election_status_id
                BEGIN
                    INSERT INTO elections_adminactionlog (
                        action_type,
                        target_table,
                        target_id,
                        action_details,
                        ip_address,
                        created_at,
                        performed_by_user_id,
                        role_id
                    )
                    VALUES (
                        'ELECTION_STATUS_CHANGED',
                        'elections_election',
                        NEW.id,
                        '{"old_status_id":' || OLD.election_status_id || ',"new_status_id":' || NEW.election_status_id || '}',
                        NULL,
                        CURRENT_TIMESTAMP,
                        NULL,
                        NULL
                    );
                END;
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS elections_adminlog_election_status_update;
            """,
        ),
        migrations.RunSQL(
            sql="""
                CREATE TRIGGER elections_adminlog_candidate_approval_update
                AFTER UPDATE OF is_approved ON elections_electioncandidate
                FOR EACH ROW
                WHEN OLD.is_approved != NEW.is_approved
                BEGIN
                    INSERT INTO elections_adminactionlog (
                        action_type,
                        target_table,
                        target_id,
                        action_details,
                        ip_address,
                        created_at,
                        performed_by_user_id,
                        role_id
                    )
                    VALUES (
                        'CANDIDATE_APPROVAL_CHANGED',
                        'elections_electioncandidate',
                        NEW.id,
                        '{"old_is_approved":' || OLD.is_approved || ',"new_is_approved":' || NEW.is_approved || '}',
                        NULL,
                        CURRENT_TIMESTAMP,
                        NULL,
                        NULL
                    );
                END;
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS elections_adminlog_candidate_approval_update;
            """,
        ),
    ]
