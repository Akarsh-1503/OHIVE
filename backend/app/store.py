"""Persistence: SQLite for lead/batch state, plain files on disk for the card images.

SQLite is the right size of tool here — a batch is a few dozen rows, the reviewer wants the
data to survive a restart, and an embedded DB keeps `docker compose up` to a single service.
The connection is shared across threads (`check_same_thread=False`) and guarded by one lock,
because every statement in this module is a sub-millisecond point read or write.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from app.models import Batch, BatchStatus, Lead

_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id    TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    started_ms  INTEGER NOT NULL,
    elapsed_ms  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cards (
    card_id      TEXT PRIMARY KEY,
    batch_id     TEXT NOT NULL,
    position     INTEGER NOT NULL,
    status       TEXT NOT NULL,
    filename     TEXT NOT NULL,
    image_path   TEXT NOT NULL,
    content_type TEXT NOT NULL,
    lead_json    TEXT NOT NULL,
    raw_json     TEXT,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES batches (batch_id)
);

CREATE INDEX IF NOT EXISTS idx_cards_batch ON cards (batch_id, position);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


class Store:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.images_dir = self.data_dir / "images"
        self.db_path = self.data_dir / "leadforge.db"
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ---- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        self.images_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Store.connect() has not been called")
        return self._conn

    # ---- images ------------------------------------------------------------

    def save_image(self, batch_id: str, card_id: str, suffix: str, payload: bytes) -> Path:
        directory = self.images_dir / batch_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{card_id}{suffix}"
        path.write_bytes(payload)
        return path

    def thumb_path(self, batch_id: str, card_id: str) -> Path:
        return self.images_dir / batch_id / f"{card_id}.thumb.webp"

    # ---- batches -----------------------------------------------------------

    def create_batch(self, batch: Batch) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO batches (batch_id, status, created_at, finished_at, started_ms, "
                "elapsed_ms) VALUES (?, ?, ?, ?, ?, ?)",
                (batch.batch_id, batch.status, batch.created_at, None, now_ms(), 0),
            )
            self.conn.commit()

    def set_batch_status(
        self, batch_id: str, status: BatchStatus, finished_at: str | None = None
    ) -> None:
        with self._lock:
            row = self.conn.execute(
                "SELECT started_ms FROM batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if row is None:
                return
            elapsed = now_ms() - int(row["started_ms"])
            if finished_at is None:
                self.conn.execute(
                    "UPDATE batches SET status = ?, elapsed_ms = ? WHERE batch_id = ?",
                    (status, elapsed, batch_id),
                )
            else:
                self.conn.execute(
                    "UPDATE batches SET status = ?, finished_at = ?, elapsed_ms = ? "
                    "WHERE batch_id = ?",
                    (status, finished_at, elapsed, batch_id),
                )
            self.conn.commit()

    def reopen_batch(self, batch_id: str) -> None:
        """Clear the terminal marker so a retry re-enters the `processing` lifecycle."""
        with self._lock:
            self.conn.execute(
                "UPDATE batches SET status = 'processing', finished_at = NULL, started_ms = ? "
                "WHERE batch_id = ?",
                (now_ms(), batch_id),
            )
            self.conn.commit()

    def delete_batch(self, batch_id: str) -> None:
        """Roll back a rejected upload, rows and files, so a half-batch is never visible."""
        with self._lock:
            self.conn.execute("DELETE FROM cards WHERE batch_id = ?", (batch_id,))
            self.conn.execute("DELETE FROM batches WHERE batch_id = ?", (batch_id,))
            self.conn.commit()
        directory = self.images_dir / batch_id
        if directory.exists():
            for child in directory.iterdir():
                child.unlink(missing_ok=True)
            directory.rmdir()

    def get_batch(self, batch_id: str) -> Batch | None:
        with self._lock:
            brow = self.conn.execute(
                "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if brow is None:
                return None
            crows = self.conn.execute(
                "SELECT lead_json FROM cards WHERE batch_id = ? ORDER BY position", (batch_id,)
            ).fetchall()

        leads = [Lead.model_validate_json(r["lead_json"]) for r in crows]
        completed = sum(1 for lead in leads if lead.status in ("completed", "needs_review"))
        failed = sum(1 for lead in leads if lead.status == "failed")
        pending = len(leads) - completed - failed
        finished_at = brow["finished_at"]
        elapsed = (
            int(brow["elapsed_ms"]) if finished_at else max(0, now_ms() - int(brow["started_ms"]))
        )
        return Batch(
            batch_id=brow["batch_id"],
            status=brow["status"],
            total=len(leads),
            completed=completed,
            failed=failed,
            pending=pending,
            created_at=brow["created_at"],
            finished_at=finished_at,
            elapsed_ms=elapsed,
            leads=leads,
        )

    # ---- cards -------------------------------------------------------------

    def insert_card(
        self, lead: Lead, position: int, image_path: Path, content_type: str
    ) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO cards (card_id, batch_id, position, status, filename, image_path, "
                "content_type, lead_json, raw_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?)",
                (
                    lead.card_id,
                    lead.batch_id,
                    position,
                    lead.status,
                    lead.filename,
                    str(image_path),
                    content_type,
                    lead.model_dump_json(),
                    None,
                    lead.created_at,
                ),
            )
            self.conn.commit()

    def update_lead(self, lead: Lead, raw_json: dict[str, object] | None = None) -> None:
        with self._lock:
            if raw_json is None:
                self.conn.execute(
                    "UPDATE cards SET status = ?, lead_json = ? WHERE card_id = ?",
                    (lead.status, lead.model_dump_json(), lead.card_id),
                )
            else:
                self.conn.execute(
                    "UPDATE cards SET status = ?, lead_json = ?, raw_json = ? WHERE card_id = ?",
                    (
                        lead.status,
                        lead.model_dump_json(),
                        json.dumps(raw_json, ensure_ascii=False),
                        lead.card_id,
                    ),
                )
            self.conn.commit()

    def get_lead(self, card_id: str) -> Lead | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT lead_json FROM cards WHERE card_id = ?", (card_id,)
            ).fetchone()
        return Lead.model_validate_json(row["lead_json"]) if row else None

    def get_card_source(self, card_id: str) -> tuple[Path, str, str] | None:
        """`(image_path, content_type, batch_id)` for the stored original upload."""
        with self._lock:
            row = self.conn.execute(
                "SELECT image_path, content_type, batch_id FROM cards WHERE card_id = ?",
                (card_id,),
            ).fetchone()
        if row is None:
            return None
        return Path(row["image_path"]), row["content_type"], row["batch_id"]

    def list_leads(self, batch_id: str) -> list[Lead]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT lead_json FROM cards WHERE batch_id = ? ORDER BY position", (batch_id,)
            ).fetchall()
        return [Lead.model_validate_json(r["lead_json"]) for r in rows]

    def list_raw(self, batch_id: str) -> dict[str, dict[str, object]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT card_id, raw_json FROM cards WHERE batch_id = ? ORDER BY position",
                (batch_id,),
            ).fetchall()
        out: dict[str, dict[str, object]] = {}
        for row in rows:
            if row["raw_json"]:
                out[row["card_id"]] = json.loads(row["raw_json"])
        return out

    def failed_card_ids(self, batch_id: str) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT card_id FROM cards WHERE batch_id = ? AND status = 'failed' "
                "ORDER BY position",
                (batch_id,),
            ).fetchall()
        return [r["card_id"] for r in rows]
