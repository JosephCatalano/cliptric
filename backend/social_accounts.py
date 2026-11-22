"""Account-specific encrypted credential storage and legacy connection migration."""

import re


class Accounts:
    def __init__(self, store, table, config_column, error):
        if (table, config_column) not in {
            ("tiktok_account", "client_key"),
            ("youtube_account", "client_id"),
            ("instagram_account", "config"),
            ("x_account", "config"),
            ("facebook_account", "config"),
        }:
            raise ValueError("Unsupported account store")
        self.store, self.table, self.column, self.error = store, table, config_column, error

    def initialize(self):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (self.table,)
            ).fetchone()
            # The old id=1 constraint is removed transactionally, retaining its encrypted
            # blob and stable account fingerprint. Publishing history does not change.
            legacy = bool(existing and "CHECK" in existing[0].upper())
            if legacy:
                db.execute(f"ALTER TABLE {self.table} RENAME TO {self.table}_legacy")
            db.execute(
                f"CREATE TABLE IF NOT EXISTS {self.table} (id INTEGER PRIMARY KEY, "
                f"{self.column} TEXT NOT NULL, account_id TEXT NOT NULL UNIQUE, "
                "credentials BLOB NOT NULL, label TEXT NOT NULL DEFAULT '')"
            )
            if legacy:
                db.execute(
                    f"INSERT INTO {self.table}(id,{self.column},account_id,credentials) "
                    f"SELECT id,{self.column},account_id,credentials FROM {self.table}_legacy"
                )
                db.execute(f"DROP TABLE {self.table}_legacy")

    def list(self, config):
        with self.store.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    f"SELECT account_id,label FROM {self.table} WHERE {self.column}=? ORDER BY id",
                    (config,),
                )
            ]

    def get(self, config, account_id=None):
        if account_id is not None and not re.fullmatch(r"[0-9a-f]{64}", account_id):
            raise self.error("Invalid account selection.", 422)
        with self.store.connection() as db:
            rows = db.execute(
                f"SELECT * FROM {self.table} WHERE {self.column}=? "
                "AND (? IS NULL OR account_id=?) LIMIT 2",
                (config, account_id, account_id),
            ).fetchall()
        if not rows:
            raise self.error("Connect the selected account before publishing.", 409)
        if len(rows) != 1:
            raise self.error("Choose the destination account first.", 409)
        return rows[0]

    def save(self, config, account_id, encrypted, label=""):
        with self.store.connection() as db:
            db.execute(
                f"INSERT INTO {self.table}({self.column},account_id,credentials,label) VALUES (?,?,?,?) "
                f"ON CONFLICT(account_id) DO UPDATE SET {self.column}=excluded.{self.column}, "
                "credentials=excluded.credentials,label=CASE WHEN excluded.label='' THEN label ELSE excluded.label END",
                (config, account_id, encrypted, label[:300]),
            )

    def label(self, account_id, label):
        with self.store.connection() as db:
            db.execute(
                f"UPDATE {self.table} SET label=? WHERE account_id=?", (label[:300], account_id)
            )

    def delete(self, config, account_id):
        selected = self.get(config, account_id)["account_id"]
        with self.store.connection() as db:
            db.execute(f"DELETE FROM {self.table} WHERE account_id=?", (selected,))
        return selected
