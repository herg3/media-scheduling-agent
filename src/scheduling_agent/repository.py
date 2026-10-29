"""Short SQLite transactions enforce idempotency, authorization and capacity."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import uuid

from .contracts import (
    DomainError, SchedulingRequest, canonical_json, digest,
    display_time, fingerprint_request, parse_time, utc_time,
)
from .policy import evaluate_rules, is_originally_authorized, merge_material, validate_proposed_time


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE fixture_meta(singleton_key INTEGER PRIMARY KEY CHECK(singleton_key=1),
 manifest_json TEXT NOT NULL);
CREATE TABLE materials(material_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL,
 source_id TEXT NOT NULL);
CREATE TABLE supplements(supplement_id TEXT PRIMARY KEY,
 material_id TEXT NOT NULL REFERENCES materials(material_id), fields_json TEXT NOT NULL,
 source_id TEXT NOT NULL);
CREATE TABLE rules(rule_id TEXT PRIMARY KEY, definition_json TEXT NOT NULL);
CREATE TABLE requests(request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
 input_json TEXT NOT NULL);
CREATE TABLE drafts(
 draft_id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL REFERENCES requests(request_id),
 material_id TEXT NOT NULL REFERENCES materials(material_id),
 channel TEXT NOT NULL, account_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=1),
 proposed_at_utc TEXT NOT NULL,
 reservation_status TEXT NOT NULL CHECK(reservation_status IN ('pending','reserved')),
 proposal_hash TEXT NOT NULL, material_version TEXT NOT NULL, rules_version TEXT NOT NULL,
 confirmation_json TEXT, updated_at TEXT NOT NULL,
 UNIQUE(request_id,material_id));
CREATE UNIQUE INDEX uq_reserved_material ON drafts(channel,account_id,material_id)
 WHERE reservation_status='reserved';
CREATE TABLE slots(channel TEXT NOT NULL, account_id TEXT NOT NULL,
 slot_start_utc TEXT NOT NULL,
 owner_type TEXT NOT NULL CHECK(owner_type IN ('existing_schedule','draft')),
 owner_id TEXT NOT NULL,
 PRIMARY KEY(channel,account_id,slot_start_utc));
"""


def request_model(request):
    return request if isinstance(request, SchedulingRequest) else SchedulingRequest.model_validate(request)


class Repository:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise DomainError("INVALID_INPUT", "数据库不存在，请先执行 init-db。")
        self.connection = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        try:
            self.manifest = json.loads(self.connection.execute(
                "SELECT manifest_json FROM fixture_meta WHERE singleton_key=1").fetchone()[0])
        except (sqlite3.Error, TypeError, ValueError):
            self.close()
            raise DomainError("INVALID_INPUT", "数据库未初始化或不属于此项目。") from None
        self.now = parse_time(self.manifest["synthetic_clock"])

    @classmethod
    def initialize(cls, path, data_dir):
        path = Path(path)
        data_dir = Path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Exclusive create avoids overwriting an existing database even in a race.
            with path.open("xb"):
                pass
        except FileExistsError:
            raise DomainError("INVALID_INPUT", "数据库已存在，拒绝覆盖。") from None
        connection = sqlite3.connect(path)
        try:
            load = lambda filename: json.loads((data_dir / filename).read_text(encoding="utf-8"))
            manifest = load("fixture-manifest.json")
            materials, supplements, rules = load("materials.json"), load("supplements.json"), load("rules.json")
            schedules = load("schedules.json")
            connection.executescript(SCHEMA)
            connection.execute("INSERT INTO fixture_meta VALUES(1,?)", (canonical_json(manifest),))
            for material in materials:
                connection.execute("INSERT INTO materials VALUES(?,?,?)", (
                    material["material_id"], canonical_json(material["metadata"]), material["source_id"]))
            for supplement in supplements:
                connection.execute("INSERT INTO supplements VALUES(?,?,?,?)", (
                    supplement["supplement_id"], supplement["material_id"],
                    canonical_json(supplement["fields"]), supplement["source_id"]))
            for rule in rules:
                connection.execute("INSERT INTO rules VALUES(?,?)", (rule["rule_id"], canonical_json(rule)))
            for slot in schedules:
                connection.execute("INSERT INTO slots VALUES(?,?,?,?,?)", (
                    slot["channel"], slot["account_id"], utc_time(slot["slot_start"]),
                    "existing_schedule", slot["owner_id"]))
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            path.unlink(missing_ok=True)
            raise
        else:
            connection.close()
        return cls(path)

    def close(self):
        self.connection.close()

    def register_request(self, request):
        req = request_model(request)
        fingerprint = fingerprint_request(req)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT fingerprint FROM requests WHERE request_id=?", (req.request_id,)).fetchone()
            if existing and existing["fingerprint"] != fingerprint:
                raise DomainError("IDEMPOTENCY_CONFLICT", "相同 request_id 携带了不同业务输入。")
            if not existing:
                self.connection.execute("INSERT INTO requests VALUES(?,?,?)", (
                    req.request_id, fingerprint, canonical_json(req.model_dump(mode="json"))))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def get_request(self, request_id):
        row = self.connection.execute("SELECT input_json FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if not row:
            raise DomainError("INVALID_INPUT", "请求不存在。")
        return SchedulingRequest.model_validate(json.loads(row["input_json"]))

    def read_material(self, material_id):
        row = self.connection.execute("SELECT * FROM materials WHERE material_id=?", (material_id,)).fetchone()
        if not row:
            raise DomainError("MATERIAL_NOT_FOUND", "未找到素材 " + material_id)
        return {"metadata": json.loads(row["metadata_json"]), "source_id": row["source_id"]}

    def supplements(self, material_id):
        return [{"fields": json.loads(row["fields_json"]), "source_id": row["source_id"]}
                for row in self.connection.execute(
                    "SELECT * FROM supplements WHERE material_id=? ORDER BY supplement_id", (material_id,))]

    def material_view(self, material_id, use_supplements=False):
        primary = self.read_material(material_id)
        supplements = self.supplements(material_id) if use_supplements else []
        view = merge_material(primary, supplements)
        return view, digest({"primary": primary, "supplements": supplements}), [
            primary["source_id"], *[row["source_id"] for row in supplements]]

    def rules(self):
        return [json.loads(row[0]) for row in self.connection.execute(
            "SELECT definition_json FROM rules ORDER BY rule_id")]

    def review(self, material_id, use_supplements=False):
        view, version, sources = self.material_view(material_id, use_supplements)
        rules = self.rules()
        result = evaluate_rules(view, rules, self.now)
        return {**result, "material_version": version,
                "rules_version": digest(rules), "source_ids": sources}

    def read_draft(self, request_id, material_id):
        row = self.connection.execute("SELECT * FROM drafts WHERE request_id=? AND material_id=?",
                                      (request_id, material_id)).fetchone()
        return self._draft(row) if row else None

    def get_draft_by_id(self, draft_id):
        row = self.connection.execute("SELECT * FROM drafts WHERE draft_id=?", (draft_id,)).fetchone()
        if not row:
            raise DomainError("DRAFT_NOT_FOUND", "草稿不存在。")
        return self._draft(row)

    @staticmethod
    def _draft(row):
        draft = dict(row)
        draft["proposed_at"] = display_time(draft["proposed_at_utc"])
        draft["confirmation"] = json.loads(draft.pop("confirmation_json") or "null")
        return draft

    def query_slots(self, request, material_id, start, end):
        req = request_model(request)
        start_dt, end_dt = parse_time(start), parse_time(end)
        if end_dt <= start_dt or end_dt - start_dt > timedelta(hours=24):
            raise DomainError("INVALID_INPUT", "查询范围必须大于0且不超过24小时。")
        own = self.read_draft(req.request_id, material_id)
        occupied = [dict(row) for row in self.connection.execute(
            "SELECT * FROM slots WHERE channel=? AND account_id=? ORDER BY slot_start_utc",
            (req.channel, req.account_id))]
        other = {row["slot_start_utc"] for row in occupied
                 if not (own and row["owner_type"] == "draft" and row["owner_id"] == own["draft_id"])}
        available = []
        at = start_dt
        while at < end_dt:
            local = parse_time(display_time(at))
            if 10 <= local.hour <= 21 and local.minute == local.second == local.microsecond == 0 and at >= self.now:
                if utc_time(at) not in other:
                    available.append(display_time(at))
            at += timedelta(hours=1)
        item = next((item for item in req.items if item.material_id == material_id), None)
        original = item.requirement if item else None
        requested = original.requested_at if original and original.mode == "EXACT" else None
        return {
            "requested_slot": display_time(requested) if requested else None,
            "requested_slot_available": (utc_time(requested) not in other) if requested else None,
            "available_slots": available[:6], "has_more": len(available) > 6,
            "occupied_slots": [display_time(slot) for slot in sorted(other)],
            "schedule_version": digest(occupied),
            "existing_draft_id": own["draft_id"] if own else None,
        }

    def collect_save_evidence(self, request, material_id, proposed_at, use_supplements=False):
        """Construct observations for direct repository callers, not an exposed model tool."""
        review = self.review(material_id, use_supplements)
        start = parse_time(proposed_at)
        slots = self.query_slots(request, material_id, display_time(start), display_time(start + timedelta(hours=1)))
        return {"read": True, "review": review, "use_supplements": use_supplements,
                "queried_slots": [utc_time(slot) for slot in slots["available_slots"]]}

    def confirmation(self, draft_id, revision, proposed_at):
        draft = self.get_draft_by_id(draft_id)
        if draft["reservation_status"] == "reserved" and draft["confirmation"]:
            recorded = draft["confirmation"]
            if recorded["revision"] == revision and utc_time(proposed_at) == recorded["proposed_at_utc"]:
                return recorded
        if draft["revision"] != revision or utc_time(proposed_at) != draft["proposed_at_utc"]:
            raise DomainError("CONFIRMATION_MISMATCH", "确认的草稿版本或时间不匹配。")
        return {key: draft[key] for key in (
            "request_id", "material_id", "draft_id", "revision", "proposal_hash", "proposed_at_utc"
        )} | {"source": "cli", "confirmed_at": display_time(self.now)}

    def save_draft(self, request, material_id, proposed_at, expected_revision=None, *,
                   evidence=None, confirmation=None, fault=None):
        req = request_model(request)
        item = next((item for item in req.items if item.material_id == material_id), None)
        if not item or item.requirement is None:
            raise DomainError("INVALID_INPUT", "素材不属于请求或缺少发布时间要求。")
        proposed = parse_time(proposed_at)
        validate_proposed_time(proposed, self.now)
        target = utc_time(proposed)
        if not evidence or not evidence.get("read") or not evidence.get("review"):
            raise DomainError("MISSING_REVIEW_EVIDENCE", "保存前必须读取素材并检查规则。")
        if target not in evidence.get("queried_slots", []):
            raise DomainError("MISSING_SLOT_EVIDENCE", "保存前必须查询并观察该时段可用。")
        committed = False
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            stored_request = self.get_request(req.request_id)
            if fingerprint_request(stored_request) != fingerprint_request(req):
                raise DomainError("IDEMPOTENCY_CONFLICT", "请求与登记输入不一致。")
            review = self.review(material_id, evidence.get("use_supplements", False))
            if review["review_status"] != "pass":
                raise DomainError("RULE_BLOCKED" if review["review_status"] == "blocked" else "REVIEW_REQUIRED",
                                  "素材审核未通过，不能保存排期。")
            for version in ("material_version", "rules_version"):
                if review[version] != evidence["review"].get(version):
                    raise DomainError("FACTS_CHANGED", "审核事实已变化，需重新读取和检查。")
            if review.get("valid_until") and proposed >= parse_time(review["valid_until"]):
                raise DomainError("RULE_BLOCKED", "授权有效期未覆盖建议发布时间。")
            old = self.read_draft(req.request_id, material_id)
            proposal_hash = digest({
                "request_fingerprint": fingerprint_request(req), "material_id": material_id,
                "proposed_at": target, "material_version": review["material_version"],
                "rules_version": review["rules_version"]})
            authorized = is_originally_authorized(item.requirement, proposed)
            confirmed = False
            if confirmation:
                expected = {key: old[key] for key in (
                    "request_id", "material_id", "draft_id", "revision", "proposal_hash", "proposed_at_utc"
                )} if old else {}
                confirmed = bool(expected) and all(confirmation.get(key) == value for key, value in expected.items())
                confirmed = confirmed and confirmation.get("source") == "cli" and target == old["proposed_at_utc"]
                confirmed = confirmed and proposal_hash == old["proposal_hash"]
                if old and old["reservation_status"] == "reserved" and old["confirmation"] == confirmation:
                    confirmed = target == old["proposed_at_utc"] and proposal_hash == old["proposal_hash"]
                if not confirmed:
                    raise DomainError("CONFIRMATION_MISMATCH", "确认凭据不适用于当前草稿版本、事实或时间。")
            state = "reserved" if authorized or confirmed else "pending"
            if old and old["reservation_status"] == "reserved":
                if old["proposal_hash"] != proposal_hash:
                    raise DomainError("CONFIRMED_DRAFT_IMMUTABLE", "已占位草稿不能自动迁移或覆盖。")
                state = "reserved"
            occupied = self.connection.execute(
                "SELECT * FROM slots WHERE channel=? AND account_id=? AND slot_start_utc=?",
                (req.channel, req.account_id, target)).fetchone()
            if occupied and not (old and occupied["owner_type"] == "draft" and occupied["owner_id"] == old["draft_id"]):
                raise DomainError("SLOT_CONFLICT", "该时段在保存时已被占用。")
            if old and old["proposal_hash"] == proposal_hash and old["reservation_status"] == state:
                if state == "reserved" and not occupied:
                    raise DomainError("FACTS_CHANGED", "草稿占位记录缺失，需要人工核对。")
                self.connection.commit()
                return {**old, "save_status": "already_exists", "is_current": True}
            if old and expected_revision != old["revision"]:
                raise DomainError("REVISION_CONFLICT", "草稿版本已变化，请重新查询。")
            draft_id = old["draft_id"] if old else "D-" + uuid.uuid4().hex[:16]
            revision = old["revision"] + 1 if old else 1
            values = (req.request_id, material_id, req.channel, req.account_id, revision, target, state,
                      proposal_hash, review["material_version"], review["rules_version"],
                      canonical_json(confirmation) if confirmed else None, display_time(self.now), draft_id)
            if old:
                self.connection.execute("""
                    UPDATE drafts SET request_id=?,material_id=?,channel=?,account_id=?,revision=?,
                    proposed_at_utc=?,reservation_status=?,proposal_hash=?,material_version=?,
                    rules_version=?,confirmation_json=?,updated_at=? WHERE draft_id=?""", values)
            else:
                self.connection.execute("""INSERT INTO drafts(
                    draft_id,request_id,material_id,channel,account_id,revision,proposed_at_utc,
                    reservation_status,proposal_hash,material_version,rules_version,
                    confirmation_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                        (draft_id, *values[:-1]))
            if state == "reserved":
                self.connection.execute("INSERT INTO slots VALUES(?,?,?,?,?)",
                                        (req.channel, req.account_id, target, "draft", draft_id))
            if fault == "before_commit":
                raise DomainError("SAVE_FAILED", "测试注入：提交前保存失败。", retryable=True)
            self.connection.commit()
            committed = True
            if fault == "after_commit":
                raise DomainError("SAVE_OUTCOME_UNKNOWN", "测试注入：已提交但响应丢失，需查询核实。", retryable=True)
            return {**self.read_draft(req.request_id, material_id), "save_status": "saved", "is_current": True}
        except sqlite3.IntegrityError as exc:
            if committed:
                raise DomainError("SAVE_OUTCOME_UNKNOWN", "草稿已提交但读回未成功，需查询核实。", retryable=True) from exc
            self.connection.rollback()
            code = "MATERIAL_ALREADY_RESERVED" if "drafts.channel" in str(exc) else "SLOT_CONFLICT"
            raise DomainError(code, "唯一约束拒绝重复素材或时段占位。") from None
        except sqlite3.OperationalError as exc:
            if committed:
                raise DomainError("SAVE_OUTCOME_UNKNOWN", "草稿已提交但读回未成功，需查询核实。", retryable=True) from exc
            if self.connection.in_transaction:
                self.connection.rollback()
            # Extended error codes retain the base SQLite result in the low byte.
            base_code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            if base_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise DomainError("DB_BUSY", "数据库正被占用，请稍后核实或重试。", retryable=True) from exc
            raise DomainError("SAVE_FAILED", "数据库保存操作失败：" + str(exc), retryable=False) from exc
        except Exception as exc:
            if committed:
                if isinstance(exc, DomainError) and exc.code == "SAVE_OUTCOME_UNKNOWN":
                    raise
                raise DomainError("SAVE_OUTCOME_UNKNOWN", "草稿已提交但返回结果未核实，需查询草稿。", retryable=True) from exc
            if not committed and self.connection.in_transaction:
                self.connection.rollback()
            raise

    def add_existing_slot(self, channel, account_id, at, owner_id):
        self.connection.execute("INSERT INTO slots VALUES(?,?,?,?,?)",
                                (channel, account_id, utc_time(at), "existing_schedule", owner_id))

    def counts(self):
        return {"drafts": self.connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0],
                "draft_slots": self.connection.execute("SELECT COUNT(*) FROM slots WHERE owner_type='draft'").fetchone()[0]}

    def readback(self):
        return {"counts": self.counts(),
                "drafts": [self._draft(row) for row in self.connection.execute("SELECT * FROM drafts ORDER BY draft_id")],
                "slots": [dict(row) for row in self.connection.execute("SELECT * FROM slots ORDER BY slot_start_utc")]}
