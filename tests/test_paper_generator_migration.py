from pathlib import Path

from db.migrate import MIGRATIONS, split_statements


DB_DIR = Path(__file__).resolve().parents[1] / "db"


def test_paper_generator_instance_migration_is_registered_after_existing_schema():
    versions = [migration.version for migration in MIGRATIONS]

    assert versions[-1] == "024_paper_generator_instances"
    assert MIGRATIONS[-1].filename == "migrate_paper_generator_instances.sql"


def test_generator_migration_preserves_legacy_identity_and_adds_instance_identity():
    migration = next(
        item for item in MIGRATIONS
        if item.version == "024_paper_generator_instances"
    )
    statements = split_statements(
        (DB_DIR / migration.filename).read_text(encoding="utf-8")
    )
    sql = "\n".join(" ".join(statement.split()).lower() for statement in statements)

    assert "ml_generator_model_id varchar(100) null" in sql
    assert "coalesce(ml_model_id, '')" in sql
    assert "coalesce(ml_generator_model_id, '')" in sql
    assert "uq_paper_cfg_instance" in sql
    assert "set p.config_id = c.id" in sql
    assert "coalesce(config_id, 0)" in sql
    assert "uq_paper_open_position" in sql
    assert "set t.config_id = p.config_id" in sql
    assert "paper_orders" in sql


def test_every_migration_statement_is_nonempty():
    migration = next(
        item for item in MIGRATIONS
        if item.version == "024_paper_generator_instances"
    )
    statements = split_statements(
        (DB_DIR / migration.filename).read_text(encoding="utf-8")
    )

    assert len(statements) >= 10
    assert all(statement.strip() for statement in statements)
