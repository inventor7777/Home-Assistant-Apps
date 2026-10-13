"""One serialized reconciler; events only request another evaluation."""

import asyncio
import logging
import time
from datetime import datetime

import aiohttp

from .model import active_action, action_commands, action_targets, event_affects, target_entities

LOG = logging.getLogger(__name__)


class Engine:
    def __init__(self, store, ha, retry_seconds=10, updated=lambda: None):
        self.store, self.ha, self.retry_seconds = store, ha, retry_seconds
        self.updated = updated
        self.snoozes, self.status, self.pending = {}, {}, {}
        self.events = set()
        self.rule_changes = set()
        self.force = True
        self.wake = asyncio.Event()
        self.intervals = {}
        self.minute = None

    def changed(self, entity=None):
        if entity is None:
            self.force = True
        else:
            self.events.add(entity)
        self.wake.set()

    def snooze(self, ident, seconds):
        if seconds:
            self.snoozes[ident] = time.monotonic() + seconds
        else:
            self.snoozes.pop(ident, None)
        self.rule_changes.add(ident)
        self.wake.set()

    def config_changed(self, previous):
        before = {r["id"]: r for r in previous}
        after = {r["id"]: r for r in self.store.rules}
        self.rule_changes.update(ident for ident in before.keys() | after.keys() if before.get(ident) != after.get(ident))
        self.wake.set()

    def winners(self, now, monotonic):
        winners, status = {}, {}
        for r in sorted(self.store.rules, key=lambda r: -r.get("priority", 50)):
            action, reason = active_action(r, self.ha.states, now)
            remaining = max(0, self.snoozes.get(r["id"], 0) - monotonic)
            if remaining:
                action, reason = None, "Snoozed"
            info = {"reason": reason, "snooze_seconds": round(remaining), "targets": {}}
            status[r["id"]] = info
            if action is not None:
                # shortcut: only explicit IDs compete, expand groups if group/member conflicts must be resolved.
                for e, target_action in action_targets(action):
                    if e in winners:
                        info["targets"][e] = f"Overridden by {winners[e][0]['name']}"
                    else:
                        winners[e] = (r, target_action)
        return winners, status

    async def reconcile(self):
        mono, now = time.monotonic(), datetime.now(self.ha.zone)
        events, force = self.events, self.force
        rule_changes = self.rule_changes
        self.events, self.force = set(), False
        self.rule_changes = set()
        minute = now.strftime("%Y-%m-%d %H:%M %z")
        time_changed, self.minute = minute != self.minute, minute
        expired = [key for key, until in self.snoozes.items() if until <= mono]
        for key in expired:
            del self.snoozes[key]
        rule_changes.update(expired)
        if not self.ha.connected or self.store.error:
            self.status = {r["id"]: {"reason": "Paused — configuration error" if self.store.error else "Paused — HA disconnected", "targets": {}, "snooze_seconds": 0} for r in self.store.rules}
            return
        affected = set()
        for r in self.store.rules:
            interval = r.get("reevaluate", {}).get("interval", 60)
            interval_due = interval and mono - self.intervals.get(r["id"], 0) >= interval
            if interval_due:
                self.intervals[r["id"]] = mono
            if force or interval_due or time_changed and r["trigger"]["type"] == "time" or any(event_affects(r, e) for e in events):
                affected.update(target_entities(r))
        winners, self.status = self.winners(now, mono)
        for entity in list(self.pending):
            if entity not in winners:
                del self.pending[entity]
        for entity, (r, action) in winners.items():
            fingerprint = (r["id"], action)
            previous = self.pending.get(entity)
            retry_due = previous and previous[0] == fingerprint and mono >= previous[1]
            target_status = self.status[r["id"]]["targets"]
            commands = action_commands(entity, action, self.ha.states)
            if commands is None:
                target_status[entity] = "Unknown/unavailable — skipped"
                self.pending.pop(entity, None)
                continue
            if not commands:
                target_status[entity] = "In sync"
                self.pending.pop(entity, None)
                continue
            if previous and previous[0] == fingerprint and mono < previous[1]:
                target_status[entity] = previous[2]
                continue
            if entity not in affected and r["id"] not in rule_changes and not retry_due:
                target_status[entity] = "Out of sync — waiting for enabled reevaluation"
                self.pending.pop(entity, None)
                continue
            try:
                for service, data in commands:
                    # State and config may change while the previous service call awaits.
                    fresh, _ = self.winners(datetime.now(self.ha.zone), time.monotonic())
                    if not self.ha.connected or self.store.error or entity not in fresh or (fresh[entity][0]["id"], fresh[entity][1]) != fingerprint:
                        break
                    await self.ha.call(service, entity, data)
                message = "Command sent — waiting for device"
                backoff = max(self.retry_seconds, action.get("data", {}).get("transition", 0) + 2)
            except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as exc:
                message = str(exc) if isinstance(exc, ValueError) else "Service call failed"
                backoff = min(300, previous[3] * 2) if previous else self.retry_seconds
                LOG.warning("%s: %s", entity, message)
            self.pending[entity] = (fingerprint, time.monotonic() + backoff, message, backoff)
            target_status[entity] = message

    async def run(self):
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            except TimeoutError:
                pass
            self.wake.clear()
            previous = self.store.rules
            if self.store.reload():
                self.config_changed(previous)
            await self.reconcile()
            self.updated()
