# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Shared contracts; no subsystem business logic."""
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


class S2Error(Exception):
    status = 400


class Forbidden(S2Error):
    status = 403


class NotFound(S2Error):
    status = 404


class Conflict(S2Error):
    status = 409


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def new_id():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


def marking(value=None):
    value = value or {"level": 0, "compartments": []}
    if not isinstance(value, dict) or set(value) - {"level", "compartments"}:
        raise S2Error("Invalid marking")
    level = value.get("level", 0)
    compartments = value.get("compartments", [])
    if type(level) is not int or level < 0 or not isinstance(compartments, list):
        raise S2Error("Invalid marking")
    if any(not isinstance(c, str) or not c for c in compartments):
        raise S2Error("Invalid compartment")
    return {"level": level, "compartments": sorted(set(compartments))}


def dominates(high, low):
    return (high["level"] >= low["level"] and
            set(low["compartments"]).issubset(high["compartments"]))


@dataclass(frozen=True)
class Context:
    tenant: str
    principal: str
    role: str
    clearance: int
    compartments: tuple
    project: str | None = None
