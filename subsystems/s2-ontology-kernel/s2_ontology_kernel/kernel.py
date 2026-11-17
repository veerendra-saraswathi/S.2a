# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Semantic validation and authoritative object projection for Gate 1."""
import math
from s2_platform import Context, S2Error, Conflict, Forbidden, NotFound, marking, dominates


class Kernel:
    def __init__(self, store, security):
        self.store, self.security = store, security

    def resource(self, db, tenant, manifest, key):
        if key not in manifest:
            raise NotFound("Resource not found")
        ctx = tenant if isinstance(tenant, Context) else None
        payload = self.store.revision(db, ctx.tenant if ctx else tenant, manifest[key])
        if ctx:
            if key.startswith(('type/', 'interface/')):
                payload['marking'] = self.security.effective(db, ctx, key, payload.get('marking'))
                for prop, spec in payload['properties'].items():
                    spec['marking'] = self.security.effective(db, ctx, key + '/property/' + prop, spec.get('marking'))
            elif key.startswith('dataset/'):
                payload['schema']['marking'] = self.security.effective(db, ctx, key, payload['schema'].get('marking'))
                if 'source_pipeline' in payload:
                    from .bindings import joined
                    payload['schema']['marking'] = joined(payload['schema']['marking'], payload['source_pipeline'].get('marking'))
                    for dependency in payload['source_pipeline']['policy_resources']:
                        payload['schema']['marking'] = joined(payload['schema']['marking'],
                            self.security.effective(db, ctx, dependency, None))
                for prop, spec in payload['schema']['properties'].items():
                    spec['marking'] = self.security.effective(db, ctx, key + '/property/' + prop, spec.get('marking'))
                from s2_platform import canonical
                for row in payload['rows']:
                    identity = canonical(row['values'][payload['primary_key']])
                    row['marking'] = self.security.effective(db, ctx, key + '/row/' + identity, row.get('marking'))
            elif key.startswith('pipeline/'):
                payload['marking'] = self.security.effective(db, ctx, key, payload.get('marking'))
            elif key.startswith(('object/', 'link/', 'linktype/')):
                payload['marking'] = self.security.effective(db, ctx, key, payload.get('marking'))
                if key.startswith('object/') and 'source_binding' in payload:
                    from .bindings import joined
                    for dependency in payload['source_binding']['policy_resources']:
                        payload['marking'] = joined(payload['marking'],
                            self.security.effective(db, ctx, dependency, None))
        return payload

    def validate_definition(self, definition):
        if not isinstance(definition, dict) or not isinstance(definition.get("properties"), dict):
            raise S2Error("Definition requires properties")
        marking(definition.get("marking"))
        for name, spec in definition["properties"].items():
            if not isinstance(name, str) or not name or not isinstance(spec, dict):
                raise S2Error("Invalid property definition")
            if spec.get("type") not in {"string", "integer", "number", "boolean"}:
                raise S2Error("Unsupported property type")
            if "required" in spec and type(spec["required"]) is not bool:
                raise S2Error("required must be a boolean")
            marking(spec.get("marking"))
        interfaces = definition.get("interfaces", [])
        if not isinstance(interfaces, list) or any(not isinstance(i, str) for i in interfaces):
            raise S2Error("Invalid interface list")

    def validate_values(self, definition, values):
        if not isinstance(values, dict) or set(values) - set(definition["properties"]):
            raise S2Error("Unknown property or invalid values")
        for name, spec in definition["properties"].items():
            if name not in values:
                if spec.get("required", False):
                    raise S2Error("Required property missing: " + name)
                continue
            value = values[name]
            expected = spec["type"]
            valid = {"string": isinstance(value, str), "integer": type(value) is int,
                     "number": type(value) in {int, float} and math.isfinite(value),
                     "boolean": type(value) is bool}[expected]
            if not valid:
                raise S2Error("Invalid value type: " + name)

    def validate_interfaces(self, db, tenant, manifest, definition):
        for name in definition.get("interfaces", []):
            interface = self.resource(db, tenant, manifest, "interface/" + name)
            for prop, spec in interface["properties"].items():
                actual = definition["properties"].get(prop)
                if (actual is None or actual["type"] != spec["type"] or
                        (spec.get("required", False) and not actual.get("required", False))):
                    raise S2Error("Interface contract not satisfied: " + name)
                if not dominates(marking(actual.get("marking")), marking(spec.get("marking"))):
                    raise S2Error("Interface property marking cannot be weakened")
            if not dominates(marking(definition.get("marking")), marking(interface.get("marking"))):
                raise S2Error("Interface marking cannot be weakened")

    def check_definition_access(self, ctx, definition):
        self.security.check_marking(ctx, definition.get("marking"))
        for spec in definition["properties"].values():
            self.security.check_marking(ctx, spec.get("marking"))

    def no_downgrade(self, old, new):
        if not dominates(marking(new.get("marking")), marking(old.get("marking"))):
            raise Forbidden("Marking downgrade requires a future declassification workflow")
        for name, spec in old["properties"].items():
            if name not in new["properties"]:
                raise Conflict("Property removal requires a migration")
            replacement = new["properties"][name]
            if spec["type"] != replacement["type"]:
                raise Conflict("Property type change requires a migration")
            if not dominates(marking(replacement.get("marking")), marking(spec.get("marking"))):
                raise Forbidden("Property marking downgrade denied")

    def check_object_write(self, db, ctx, manifest, object_id, type_name, values, label,
                           expected_revision=None, materializing=False):
        definition = self.resource(db, ctx, manifest, "type/" + type_name)
        self.check_definition_access(ctx, definition)
        label = self.security.effective(db, ctx, 'object/' + object_id, label)
        self.security.check_marking(ctx, label)
        self.validate_values(definition, values)
        key = "object/" + object_id
        previous = manifest.get(key)
        if previous != expected_revision:
            raise Conflict("Object revision changed")
        if previous:
            old = self.resource(db, ctx, manifest, key)
            self.security.check_marking(ctx, old["marking"])
            if old["type"] != type_name:
                raise Conflict("Object type is immutable")
            if not dominates(marking(label), old["marking"]):
                raise Forbidden("Object marking downgrade denied")
            if 'source_binding' in old and not materializing:
                for prop in old['source_binding']['source_properties']:
                    if (prop in values) != (prop in old['values']) or values.get(prop) != old['values'].get(prop):
                        raise Forbidden('Source-owned properties can only change through materialization')
        result = {"id": object_id, "type": type_name, "values": values, "marking": marking(label)}
        if previous and 'source_binding' in old:
            result['source_binding'] = old['source_binding']
        if previous and 'source_state' in old:
            result['source_state'] = old['source_state']
        return result

    def visible_object(self, db, ctx, manifest, object_id):
        obj = self.resource(db, ctx, manifest, "object/" + object_id)
        definition = self.resource(db, ctx, manifest, "type/" + obj["type"])
        if not self.security.allowed(ctx, obj["marking"]) or not self.security.allowed(ctx, definition.get("marking")):
            raise NotFound("Object not found")
        values = {name: value for name, value in obj["values"].items()
                  if self.security.allowed(ctx, definition["properties"][name].get("marking"))}
        result = {"id": obj["id"], "type": obj["type"], "values": values,
                  "revision": manifest["object/" + object_id]}
        if 'source_state' in obj:
            result['source_presence'] = obj['source_state']['presence']
        return result

    def validate_manifest(self, db, tenant, manifest):
        for key in manifest:
            if key.startswith("type/"):
                definition = self.resource(db, tenant, manifest, key)
                self.validate_interfaces(db, tenant, manifest, definition)
            if key.startswith("object/"):
                obj = self.resource(db, tenant, manifest, key)
                definition = self.resource(db, tenant, manifest, "type/" + obj["type"])
                self.validate_values(definition, obj["values"])
            if key.startswith("link/"):
                link = self.resource(db, tenant, manifest, key)
                definition = self.resource(db, tenant, manifest, "linktype/" + link["type"])
                for endpoint in ("source", "target"):
                    obj = self.resource(db, tenant, manifest, "object/" + link[endpoint])
                    if obj["type"] != definition[endpoint]:
                        raise Conflict("Link endpoint type mismatch")
