from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("elections", "0004_hash_existing_token_values"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE TRIGGER elections_ballotselection_same_election_insert
                BEFORE INSERT ON elections_ballotselection
                FOR EACH ROW
                BEGIN
                    SELECT CASE
                        WHEN (
                            SELECT election_id
                            FROM elections_ballot
                            WHERE id = NEW.ballot_id
                        ) != (
                            SELECT election_id
                            FROM elections_electioncandidate
                            WHERE id = NEW.election_candidate_id
                        )
                        THEN RAISE(ABORT, 'Ballot and candidate must belong to same election')
                    END;
                END;
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS elections_ballotselection_same_election_insert;
            """,
        ),
        migrations.RunSQL(
            sql="""
                CREATE TRIGGER elections_ballotselection_same_election_update
                BEFORE UPDATE OF ballot_id, election_candidate_id ON elections_ballotselection
                FOR EACH ROW
                BEGIN
                    SELECT CASE
                        WHEN (
                            SELECT election_id
                            FROM elections_ballot
                            WHERE id = NEW.ballot_id
                        ) != (
                            SELECT election_id
                            FROM elections_electioncandidate
                            WHERE id = NEW.election_candidate_id
                        )
                        THEN RAISE(ABORT, 'Ballot and candidate must belong to same election')
                    END;
                END;
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS elections_ballotselection_same_election_update;
            """,
        ),
        migrations.RunSQL(
            sql="""
                CREATE TRIGGER elections_votingtoken_set_used_at
                AFTER UPDATE OF is_used ON elections_votingtoken
                FOR EACH ROW
                WHEN NEW.is_used = 1 AND NEW.used_at IS NULL
                BEGIN
                    UPDATE elections_votingtoken
                    SET used_at = CURRENT_TIMESTAMP
                    WHERE id = NEW.id;
                END;
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS elections_votingtoken_set_used_at;
            """,
        ),
        migrations.RunSQL(
            sql="""
                CREATE TRIGGER elections_votingtoken_clear_used_at
                AFTER UPDATE OF is_used ON elections_votingtoken
                FOR EACH ROW
                WHEN NEW.is_used = 0 AND NEW.used_at IS NOT NULL
                BEGIN
                    UPDATE elections_votingtoken
                    SET used_at = NULL
                    WHERE id = NEW.id;
                END;
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS elections_votingtoken_clear_used_at;
            """,
        ),
        migrations.RunSQL(
            sql="""
                CREATE TRIGGER elections_ballot_set_submitted_at
                AFTER UPDATE OF ballot_status ON elections_ballot
                FOR EACH ROW
                WHEN NEW.ballot_status = 'SUBMITTED' AND NEW.submitted_at IS NULL
                BEGIN
                    UPDATE elections_ballot
                    SET submitted_at = CURRENT_TIMESTAMP
                    WHERE id = NEW.id;
                END;
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS elections_ballot_set_submitted_at;
            """,
        ),
        migrations.RunSQL(
            sql="""
                CREATE TRIGGER elections_notification_set_sent_at
                AFTER UPDATE OF is_sent ON elections_notification
                FOR EACH ROW
                WHEN NEW.is_sent = 1 AND NEW.sent_at IS NULL
                BEGIN
                    UPDATE elections_notification
                    SET sent_at = CURRENT_TIMESTAMP
                    WHERE id = NEW.id;
                END;
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS elections_notification_set_sent_at;
            """,
        ),
    ]
