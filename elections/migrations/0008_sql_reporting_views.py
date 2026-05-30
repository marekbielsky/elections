from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("elections", "0007_role_permission_rolepermission_adminactionlog_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE VIEW elections_view_turnout_by_type AS
                SELECT
                    et.code AS election_type_code,
                    et.name AS election_type_name,
                    COUNT(e.id) AS elections_count,
                    ROUND(COALESCE(AVG(er.turnout_percent), 0), 2) AS avg_turnout_percent
                FROM elections_electiontype et
                LEFT JOIN elections_election e ON e.election_type_id = et.id
                LEFT JOIN elections_electionresult er ON er.election_id = e.id
                GROUP BY et.id, et.code, et.name;
            """,
            reverse_sql="""
                DROP VIEW IF EXISTS elections_view_turnout_by_type;
            """,
        ),
        migrations.RunSQL(
            sql="""
                CREATE VIEW elections_view_election_winners AS
                SELECT
                    e.id AS election_id,
                    e.name AS election_name,
                    COALESCE(p.first_name || ' ' || p.last_name, '-') AS winner_name,
                    COALESCE(eri.votes_count, 0) AS winner_votes
                FROM elections_election e
                LEFT JOIN elections_electionresult er ON er.election_id = e.id
                LEFT JOIN elections_electionresultitem eri
                    ON eri.election_result_id = er.id
                    AND eri.ranking_position = 1
                LEFT JOIN elections_electioncandidate ec ON ec.id = eri.election_candidate_id
                LEFT JOIN elections_person p ON p.id = ec.person_id;
            """,
            reverse_sql="""
                DROP VIEW IF EXISTS elections_view_election_winners;
            """,
        ),
    ]
