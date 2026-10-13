"""Atomic writes, optimistic concurrency, and validated manual reloads."""

import hashlib
import os
import tempfile
from pathlib import Path

import yaml

from .model import require, validate_rules


class Conflict(ValueError):
    pass


def parse(text, actions=None, validate=True, draft=False):
    try:
        value = yaml.safe_load(text)
        require(isinstance(value, list), "Syncs must be a YAML list")
        if actions:
            require(isinstance(actions, dict) and not (actions.keys() - {"desired", "alternate"}), "Invalid action overrides")
            require(len(value) == 1 and isinstance(value[0], dict), "Action overrides need exactly one sync")
            for key, action in actions.items():
                if isinstance(action, str):
                    value[0][key] = yaml.safe_load(action)
                else:
                    require(isinstance(action, dict) and isinstance(value[0].get(key), list), "Invalid entity action overrides")
                    entries = value[0][key]
                    require(all(isinstance(a, dict) and isinstance(a.get("entity_id"), str) for a in entries), "Invalid entity actions")
                    require(not (action.keys() - {a.get("entity_id") for a in entries}), "Unknown action target")
                    for entry in entries:
                        entity = entry.get("entity_id")
                        if entity in action:
                            require(isinstance(action[entity], str), "Action YAML must be text")
                            parsed = yaml.safe_load(action[entity])
                            require(isinstance(parsed, dict) and "entity_id" not in parsed, "Custom action cannot override its target")
                            entry.clear()
                            entry.update({"entity_id": entity, **parsed})
        if validate:
            validate_rules(value, draft=draft)
        return value
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError("Invalid sync YAML") from exc


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.rules = []
        self.revision = ""
        self.error = None
        self.reload()

    def read(self):
        try:
            require(self.path.stat().st_size <= 1024 * 1024, "Sync file exceeds 1 MiB")
            return self.path.read_bytes()
        except FileNotFoundError:
            return b"[]\n"

    @staticmethod
    def digest(raw):
        return hashlib.sha256(raw).hexdigest()

    def reload(self):
        try:
            raw = self.read()
            revision = self.digest(raw)
            if revision == self.revision and not self.error:
                return False
            rules = parse(raw.decode("utf-8"))
            self.rules, self.revision, self.error = rules, revision, None
            return True
        except (ValueError, OSError, UnicodeError) as exc:
            self.error = str(exc)
            return False

    def save(self, rules, revision):
        validate_rules(rules)
        require(not self.error, "Fix the sync file before saving")
        if revision != self.revision or self.digest(self.read()) != revision:
            raise Conflict("Syncs changed. Reload before saving; your edit was not written.")
        raw = yaml.safe_dump(rules, sort_keys=False, allow_unicode=True).encode()
        require(len(raw) <= 1024 * 1024, "Sync file exceeds 1 MiB")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as f:
                temp = f.name
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            # Check again immediately before replacing, including manual changes.
            if self.digest(self.read()) != revision:
                raise Conflict("Syncs changed while saving. Reload before saving.")
            os.replace(temp, self.path)
            self.rules, self.revision = rules, self.digest(raw)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
