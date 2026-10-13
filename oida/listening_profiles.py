"""Owner custody for drafts, immutable revisions and scoped LISTENING.md state.

Authority and material grants are explicit owner declarations. They do not
establish acceptance by other parties or legal standing. SQLite transactions
keep expected revisions/generations separate from active binding changes.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

from akousma import new_id
from akouo_contract.listening_profiles import (
    LISTENING_CONTRACTS,
    SUPPORTED_CONTROLS,
    active_adoptions,
    adopt_profile,
    apply_profiles,
    compile_profile,
    create_document,
    empty_state,
    revoke_adoption,
    select_context,
    state_errors,
)

MATERIAL_ACTIONS = frozenset(
    {
        "read",
        "read_metadata",
        "execute",
        "retain",
        "retain_metadata",
        "disclose_local",
        "disclose_external",
        "export",
        "export_public",
        "forget",
    }
)
AUTHORITY_ACTIONS = frozenset({"adopt", "replace", "revoke", "apply"})


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class ProfileConflict(ValueError):
    pass


class ListeningProfiles:
    def __init__(
        self, path, *, workspace_ref, actor_ref, clock=utc_now, material_terms=None
    ):
        requested = Path(path)
        if requested.is_symlink():
            raise ValueError("Profile database cannot be a symlink")
        self.path = requested.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        self.workspace_ref, self.actor_ref, self.clock = workspace_ref, actor_ref, clock
        self.material_terms = material_terms
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS drafts (document_id TEXT PRIMARY KEY, bytes BLOB NOT NULL, sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (revision_ref TEXT PRIMARY KEY, document TEXT NOT NULL, profile TEXT NOT NULL, bytes BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS authorities (authority_ref TEXT PRIMARY KEY, declaration TEXT NOT NULL, active INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS lifecycle (singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS turn_counts (scope TEXT PRIMARY KEY, count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS executions (operation_ref TEXT PRIMARY KEY, receipt TEXT NOT NULL);
        """)
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO lifecycle VALUES(1,?)",
                (json.dumps(empty_state()),),
            )

    def close(self):
        self.conn.close()

    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def scope(self, path):
        from akouo_contract.listening_profiles import _scope

        _scope(path)
        if path[0] != dict(kind="workspace", ref=self.workspace_ref):
            raise PermissionError(
                "Scope does not identify this authenticated workspace"
            )

    def capabilities(self, *, max_context_bytes=65536):
        return dict(
            contract="akouo/listening-capabilities/v1",
            host_ref="oida:" + self.workspace_ref,
            supported_contracts=list(LISTENING_CONTRACTS),
            supported_controls=list(SUPPORTED_CONTROLS),
            max_context_bytes=max_context_bytes,
        )

    def state(self):
        state = json.loads(
            self.conn.execute(
                "SELECT state FROM lifecycle WHERE singleton=1"
            ).fetchone()["state"]
        )
        errors = state_errors(state)
        if errors:
            raise ValueError("Invalid stored lifecycle state: " + "; ".join(errors))
        return state

    def _save_state(self, state):
        errors = state_errors(state)
        if errors:
            raise ValueError("Invalid lifecycle mutation: " + "; ".join(errors))
        self.conn.execute(
            "UPDATE lifecycle SET state=? WHERE singleton=1",
            (json.dumps(state, ensure_ascii=False),),
        )

    def save_draft(self, document_id, material: bytes, *, expected_sha256):
        from akouo_contract.listening_profiles import _bytes

        _bytes(material)
        with self.transaction():
            row = self.conn.execute(
                "SELECT sha256 FROM drafts WHERE document_id=?", (document_id,)
            ).fetchone()
            if (row["sha256"] if row else None) != expected_sha256:
                raise ProfileConflict("Draft changed; reload its exact revision")
            self.conn.execute(
                "INSERT OR REPLACE INTO drafts VALUES(?,?,?)",
                (document_id, material, sha256(material).hexdigest()),
            )
        return dict(
            document_id=document_id,
            sha256=sha256(material).hexdigest(),
            byte_count=len(material),
            adoption="unchanged",
        )

    def draft(self, document_id):
        row = self.conn.execute(
            "SELECT bytes,sha256 FROM drafts WHERE document_id=?", (document_id,)
        ).fetchone()
        if row is None:
            raise KeyError(document_id)
        return dict(
            document_id=document_id,
            text=bytes(row["bytes"]).decode("utf-8"),
            sha256=row["sha256"],
            adoption="unchanged",
        )

    def freeze(
        self,
        document_id,
        *,
        expected_sha256,
        authors,
        terms_ref,
        purpose="orientation",
        previous_revision=None,
        fork=False,
    ):
        with self.transaction():
            row = self.conn.execute(
                "SELECT bytes,sha256 FROM drafts WHERE document_id=?", (document_id,)
            ).fetchone()
            if row is None or row["sha256"] != expected_sha256:
                raise ProfileConflict("Draft revision conflict")
            previous = self.resolve(previous_revision)[0] if previous_revision else None
            material = bytes(row["bytes"])
            doc = create_document(
                material,
                document_id=document_id,
                revision_ref=new_id("lrev"),
                representation_ref=new_id("rep"),
                artifact_ref=new_id("lart"),
                authors=authors,
                authored_at=self.clock(),
                terms_ref=terms_ref,
                purpose=purpose,
                previous=previous,
                expected_sha256=previous["sha256"] if previous else None,
                fork=fork,
            )
            profile = compile_profile(doc, material)
            self.conn.execute(
                "INSERT INTO documents VALUES(?,?,?,?)",
                (doc["revision_ref"], json.dumps(doc), json.dumps(profile), material),
            )
        return dict(document=doc, profile=profile, adoption="not_adopted")

    def import_canonical(self, document, material, profile):
        if compile_profile(document, material) != profile:
            raise ValueError("Canonical source and profile disagree")
        with self.transaction():
            row = self.conn.execute(
                "SELECT * FROM documents WHERE revision_ref=?",
                (document["revision_ref"],),
            ).fetchone()
            if row and (
                json.loads(row["document"]) != document
                or json.loads(row["profile"]) != profile
                or bytes(row["bytes"]) != material
            ):
                raise ProfileConflict(
                    "Canonical revision already names different material"
                )
            self.conn.execute(
                "INSERT OR IGNORE INTO documents VALUES(?,?,?,?)",
                (
                    document["revision_ref"],
                    json.dumps(document),
                    json.dumps(profile),
                    material,
                ),
            )
        return deepcopy(document)

    def resolve(self, revision_ref):
        row = self.conn.execute(
            "SELECT * FROM documents WHERE revision_ref=?", (revision_ref,)
        ).fetchone()
        if row is None:
            raise KeyError(revision_ref)
        doc, profile, material = (
            json.loads(row["document"]),
            json.loads(row["profile"]),
            bytes(row["bytes"]),
        )
        if compile_profile(doc, material) != profile:
            raise ValueError("Immutable profile source changed")
        return doc, material, profile

    def grant_authority(self, *, scope_path, party_refs, actions, basis):
        self.scope(scope_path)
        if (
            not party_refs
            or any(not isinstance(p, str) or not p for p in party_refs)
            or not set(actions) <= AUTHORITY_ACTIONS
            or not actions
            or not basis
        ):
            raise ValueError(
                "Explicit parties, supported authority actions and a basis are required"
            )
        ref = new_id("auth")
        value = dict(
            authority_ref=ref,
            actor_ref=self.actor_ref,
            scope_path=deepcopy(scope_path),
            party_refs=list(dict.fromkeys(party_refs)),
            actions=list(dict.fromkeys(actions)),
            basis=basis,
            issued_at=self.clock(),
            attestation="owner_declared",
        )
        with self.conn:
            self.conn.execute(
                "INSERT INTO authorities VALUES(?,?,1)", (ref, json.dumps(value))
            )
        return value

    def verify_authority(self, request):
        self.scope(request["scope_path"])
        row = self.conn.execute(
            "SELECT declaration FROM authorities WHERE authority_ref=? AND active=1",
            (request["authority_ref"],),
        ).fetchone()
        if row is None:
            return False
        grant = json.loads(row["declaration"])
        path = request["scope_path"]
        return (
            request["actor_ref"] == self.actor_ref == grant["actor_ref"]
            and request["action"] in grant["actions"]
            and path[: len(grant["scope_path"])] == grant["scope_path"]
            and set(request["party_refs"]) <= set(grant["party_refs"])
        )

    def set_terms(
        self, terms_ref, *, actions, basis, expected_revision=None, active=True
    ):
        if self.material_terms is None:
            raise PermissionError("No canonical material terms owner is connected")
        return self.material_terms.set(
            terms_ref,
            actions=actions,
            basis=basis,
            expected_revision=expected_revision,
            active=active,
        )

    def revoke_terms(self, terms_ref, *, expected_revision):
        if self.material_terms is None:
            raise PermissionError("No canonical material terms owner is connected")
        return self.material_terms.revoke(
            terms_ref, expected_revision=expected_revision
        )

    def permit(self, action, rep, *, resolve_representation=None):
        return self.material_terms is not None and self.material_terms.permit(
            action, rep, resolve_representation=resolve_representation
        )

    def adopt(
        self,
        revision_ref,
        *,
        scope_path,
        party_refs,
        authority_ref,
        expected_generation,
        replace_ref=None,
        expires_at=None,
    ):
        self.scope(scope_path)
        with self.transaction():
            state = self.state()
            doc, material, profile = self.resolve(revision_ref)
            parents = (
                active_adoptions(state, scope_path[:-1], self.clock())
                if len(scope_path) > 1
                else []
            )
            updated, adoption = adopt_profile(
                state,
                doc,
                material,
                profile,
                adoption_ref=new_id("adopt"),
                event_ref=new_id("lev"),
                scope_path=scope_path,
                party_refs=party_refs,
                actor_ref=self.actor_ref,
                authority_ref=authority_ref,
                recorded_at=self.clock(),
                effective_at=self.clock(),
                capabilities=self.capabilities(),
                verify_authority=self.verify_authority,
                expected_generation=expected_generation,
                resolve_document=self.resolve,
                parent_adoption_ref=parents[-1]["adoption_ref"] if parents else None,
                replace_ref=replace_ref,
                expires_at=expires_at,
            )
            self._save_state(updated)
        return adoption

    def revoke(self, adoption_ref, *, authority_ref, expected_generation):
        with self.transaction():
            updated, event = revoke_adoption(
                self.state(),
                adoption_ref,
                event_ref=new_id("lev"),
                actor_ref=self.actor_ref,
                authority_ref=authority_ref,
                recorded_at=self.clock(),
                effective_at=self.clock(),
                verify_authority=self.verify_authority,
                expected_generation=expected_generation,
            )
            self._save_state(updated)
        return event

    def next_turn(self, scope_path):
        self.scope(scope_path)
        # The run owns the count when declared; otherwise the workspace does.
        index = next(
            (
                i
                for i in range(len(scope_path) - 1, -1, -1)
                if scope_path[i]["kind"] == "run"
            ),
            0,
        )
        key = json.dumps(scope_path[: index + 1], sort_keys=True)
        with self.transaction():
            self.conn.execute(
                "INSERT INTO turn_counts VALUES(?,1) ON CONFLICT(scope) DO UPDATE SET count=count+1",
                (key,),
            )
            count = self.conn.execute(
                "SELECT count FROM turn_counts WHERE scope=?", (key,)
            ).fetchone()["count"]
        return count

    def profile_contexts(
        self, scope_path, operation_ref, *, selections=None, max_bytes=65536
    ):
        self.scope(scope_path)
        state = self.state()
        adoptions = active_adoptions(state, scope_path, self.clock())
        deliveries, documents = [], {}
        for adoption in adoptions:
            doc, material, profile = self.resolve(adoption["revision_ref"])
            ranges = (selections or {}).get(adoption["adoption_ref"])
            selected, metadata = select_context(
                doc,
                material,
                profile,
                received_representation_ref=new_id("rep")
                if ranges is not None
                else doc["representation_ref"],
                max_bytes=max_bytes,
                ranges=ranges,
            )
            if (
                metadata["coverage"]["kind"] == "full"
                and metadata["received_representation_ref"] != doc["representation_ref"]
            ):
                selected, metadata = select_context(
                    doc,
                    material,
                    profile,
                    received_representation_ref=doc["representation_ref"],
                    max_bytes=max_bytes,
                    ranges=ranges,
                )
            deliveries.append(
                dict(
                    adoption_ref=adoption["adoption_ref"],
                    received_representation_ref=metadata["received_representation_ref"],
                    received_bytes=selected,
                    ranges=ranges,
                )
            )
            documents[adoption["adoption_ref"]] = (doc, material, profile, metadata)
        return adoptions, deliveries, documents

    def gate(
        self,
        *,
        scope_path,
        operation_ref,
        gate,
        authority_ref,
        facts,
        contexts=None,
        expected_adoptions=None,
        max_bytes=65536,
    ):
        self.scope(scope_path)
        with self.transaction():
            state = self.state()
            current = active_adoptions(state, scope_path, self.clock())
            if (
                expected_adoptions is not None
                and [a["adoption_ref"] for a in current] != expected_adoptions
            ):
                raise PermissionError(
                    "Operation binding changed; a new operation is required"
                )
            if not current:
                return None
            if not all(
                self.verify_authority(
                    dict(
                        action="apply",
                        actor_ref=self.actor_ref,
                        authority_ref=authority_ref,
                        scope_path=scope_path,
                        party_refs=a["party_refs"],
                    )
                )
                for a in current
            ):
                raise PermissionError(
                    "Current owner authority did not authorize this application"
                )
            updated, receipt = apply_profiles(
                state,
                application_ref=new_id("apply"),
                operation_ref=operation_ref,
                scope_path=scope_path,
                gate=gate,
                at=self.clock(),
                actor_ref=self.actor_ref,
                authority_ref=authority_ref,
                capabilities=self.capabilities(max_context_bytes=max_bytes),
                verify_authority=self.verify_authority,
                resolve_document=self.resolve,
                resolve_fact=lambda target: facts.get(target),
                expected_generation=state["generation"],
                contexts=contexts if gate == "execution" else None,
            )
            self._save_state(updated)
        if receipt["gate_result"] != "cleared":
            raise PermissionError(
                "Required profile controls blocked the " + gate + " gate"
            )
        return receipt

    def record_execution(self, receipt):
        retained = deepcopy(receipt)
        report = retained.pop("report", None)
        if report is not None:
            retained["report_sha256"] = sha256(
                json.dumps(report, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
        with self.conn:
            self.conn.execute(
                "INSERT INTO executions VALUES(?,?)",
                (receipt["operation_ref"], json.dumps(retained, ensure_ascii=False)),
            )
        return deepcopy(receipt)

    def revoke_authority(self, authority_ref):
        with self.conn:
            result = self.conn.execute(
                "UPDATE authorities SET active=0 WHERE authority_ref=?",
                (authority_ref,),
            )
        if not result.rowcount:
            raise KeyError(authority_ref)
        return dict(authority_ref=authority_ref, active=False)
