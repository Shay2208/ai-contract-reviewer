"""合同审查记录（SQLite）。

只存「这次审了哪份合同、结论是什么、报告在哪、谁点的按钮」，
原文不落库（合同正文本身留在 uploads/ 目录，方便复跑）。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contract_reviews (
    id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    overall TEXT NOT NULL,
    high INTEGER NOT NULL DEFAULT 0,
    medium INTEGER NOT NULL DEFAULT 0,
    low INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}',
    findings_json TEXT NOT NULL DEFAULT '[]',
    questions_json TEXT NOT NULL DEFAULT '[]',
    report_md TEXT NOT NULL DEFAULT '',
    report_json TEXT NOT NULL DEFAULT '',
    report_docx TEXT NOT NULL DEFAULT '',
    chat_id TEXT NOT NULL DEFAULT '',
    operator_open_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'reviewed',
    task_url TEXT NOT NULL DEFAULT '',
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL
)
"""

_UPDATABLE = {
    "status",
    "task_url",
    "report_md",
    "report_json",
    "report_docx",
    "operator_open_id",
    "chat_id",
    "message_id",
}


def _row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("summary_json", "findings_json", "questions_json"):
        try:
            data[key] = json.loads(data.get(key) or "{}")
        except json.JSONDecodeError:
            data[key] = {} if key == "summary_json" else []
    # 友好别名，方便卡片/报告直接取 record["summary"] / record["findings"]
    data["summary"] = data["summary_json"] or {}
    data["findings"] = data["findings_json"] or []
    data["questions"] = data["questions_json"] or []
    return data


class ContractStore:
    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def save(
        self,
        *,
        file_name: str,
        file_path: str,
        overall: str,
        high: int,
        medium: int,
        low: int,
        summary: dict[str, Any],
        findings: list[dict[str, Any]],
        questions: list[str],
        report_md: str = "",
        report_json: str = "",
        report_docx: str = "",
        chat_id: str = "",
        operator_open_id: str = "",
        message_id: str = "",
    ) -> str:
        review_id = uuid.uuid4().hex[:12]
        now = time.time()
        self.conn.execute(
            """
            INSERT INTO contract_reviews (
                id, file_name, file_path, overall, high, medium, low,
                summary_json, findings_json, questions_json,
                report_md, report_json, report_docx,
                chat_id, operator_open_id, message_id, status, created_ts, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                review_id,
                file_name,
                str(file_path),
                overall,
                high,
                medium,
                low,
                json.dumps(summary or {}, ensure_ascii=False),
                json.dumps(findings or [], ensure_ascii=False),
                json.dumps(questions or [], ensure_ascii=False),
                str(report_md or ""),
                str(report_json or ""),
                str(report_docx or ""),
                chat_id,
                operator_open_id,
                message_id,
                "reviewed",
                now,
                now,
            ),
        )
        self.conn.commit()
        return review_id

    def get(self, review_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM contract_reviews WHERE id = ?", (review_id,)
        ).fetchone()
        return _row(row) if row else None

    def update(self, review_id: str, **fields: Any) -> None:
        clean = {k: v for k, v in fields.items() if k in _UPDATABLE}
        if not clean:
            return
        clean["updated_ts"] = time.time()
        assignments = ", ".join(f"{key} = ?" for key in clean)
        self.conn.execute(
            f"UPDATE contract_reviews SET {assignments} WHERE id = ?",
            (*clean.values(), review_id),
        )
        self.conn.commit()

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM contract_reviews ORDER BY created_ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row(r) for r in rows]

    def stats(self) -> dict[str, int]:
        total = self.conn.execute("SELECT COUNT(*) AS n FROM contract_reviews").fetchone()["n"]
        high = self.conn.execute(
            "SELECT COUNT(*) AS n FROM contract_reviews WHERE overall = '高'"
        ).fetchone()["n"]
        return {"total": int(total), "high_risk": int(high)}

    def close(self) -> None:
        self.conn.close()


__all__ = ["ContractStore"]
