"""The expanding favorites migration preserves historical listening events."""
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config


def test_favorites_upgrade_preserves_old_events_and_defaults_to_track(tmp_path):
    import app.db

    db = tmp_path / "hub.sqlite3"
    root = Path(app.db.__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "f2b7c4e81a06")
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO users (id,nd_username,nd_password_enc,is_admin,created_at) "
                  "VALUES (1,'qa','unused',0,'2020-01-01')")
        c.execute("INSERT INTO play_events (user_id,event_id,ts,kind,ref,client,received_at) "
                  "VALUES (1,'old','2020-01-01','like','dz:1','pwa','2020-01-02')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT event_id,ts,kind,ref,target_type,mirror_pending,reverts_event_id "
                         "FROM play_events").fetchall() == [("old", "2020-01-01", "like", "dz:1", "track", 0, None)]
        assert c.execute("SELECT count(*) FROM favorite_subjects").fetchone()[0] == 0
    command.downgrade(cfg, "f2b7c4e81a06")
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT event_id FROM play_events").fetchall() == [("old",)]
