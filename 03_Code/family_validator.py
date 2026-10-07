from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml


class FamilyValidationError(ValueError):
    pass


@dataclass
class ValidationMessage:
    severity: str
    path: str
    message: str
    def format(self) -> str:
        return f"[{self.severity}] {self.path}: {self.message}"


@dataclass
class FamilyValidationResult:
    errors: list[ValidationMessage] = field(default_factory=list)
    warnings: list[ValidationMessage] = field(default_factory=list)
    @property
    def ok(self) -> bool:
        return not self.errors
    def error(self, path: str, message: str) -> None:
        self.errors.append(ValidationMessage("ERROR", path, message))
    def warn(self, path: str, message: str) -> None:
        self.warnings.append(ValidationMessage("WARN", path, message))
    def extend(self, other: "FamilyValidationResult") -> None:
        self.errors.extend(other.errors); self.warnings.extend(other.warnings)
    def raise_for_errors(self) -> None:
        if self.errors:
            raise FamilyValidationError("Family validation failed:\n" + "\n".join(e.format() for e in self.errors))


ALLOWED_TOP = {"id", "extends", "execution_layer", "resolution", "emit_bundles", "default_label_map", "default_label", "joined_label_map"}
ALLOWED_RESOLUTION = {"resolved_order", "statement_unit_type", "semantic_unit", "roles", "resources", "collections", "values"}
ALLOWED_ROLE = {"path", "required", "default_term_type", "term_type", "resolver", "default_term_map", "default_template", "default_constant", "default_reference", "datatype", "language", "language_reference", "languageReference", "language_template", "languageTemplate", "language_map", "languageMap"}
ALLOWED_RESOURCE = ALLOWED_ROLE - {"resolver"}
ALLOWED_COLLECTION_BUILDERS = {"time_fields", "association_members"}
ALLOWED_VALUE = {"kind", "path", "family_path", "default_family_path", "default", "exists", "default_exists", "required"}
ALLOWED_VALUE_KINDS = {"scalar", "bool", "bool_exists", "iri"}
ALLOWED_BUNDLE = {"id", "kind", "subject_map", "subject", "graph_map", "graph", "predicate_object_maps", "when", "when_exists", "when_not_equals", "when_not_equal"}
ALLOWED_PO = {"predicate_map", "object_map", "when_present", "when", "when_exists", "when_not_equals", "when_not_equal", "for_each", "optional"}
ALLOWED_CONDITION = {"all", "any", "not", "exists", "equals", "not_equals", "not_equal"}
ALLOWED_TERM = {"constant", "reference", "template", "parent", "parent_triples_map", "parentTriplesMap", "term_type", "termType", "datatype", "language", "language_reference", "languageReference", "language_template", "languageTemplate", "language_map", "languageMap", "preserve_curie", "expand", "join", "join_conditions", "joinConditions"}
ALLOWED_TERM_TYPES = {"iri", "IRI", "rr:IRI", "literal", "Literal", "rr:Literal", "blanknode", "blank_node", "BlankNode", "rr:BlankNode"}
TERM_KEYS = {"constant", "reference", "template", "parent", "parent_triples_map", "parentTriplesMap"}

ALLOWED_LABEL = {"template", "fallback_template", "cases", "tokens", "requires", "target", "mode"}
ALLOWED_LABEL_CASE = {"template", "requires"}
ALLOWED_LABEL_TOKEN = {"from", "from_entity_label", "from_instance", "from_semantic_unit_label", "from_association_member_labels", "allow_cross_source", "allow_join", "humanize", "humanize_iri_constants", "from_time_field_predicate", "from_label_token", "constant", "literal", "default"}


def _p(base: str, key: str | int) -> str:
    return f"{base}[{key}]" if isinstance(key, int) else (f"{base}.{key}" if base else key)


def _unknown(result: FamilyValidationResult, mapping: dict[str, Any], allowed: set[str], path: str) -> None:
    for key in mapping:
        if key not in allowed:
            result.error(_p(path, key), "unsupported key")


def _is_str_or_str_list(value: Any) -> bool:
    return isinstance(value, str) or (isinstance(value, list) and all(isinstance(v, str) for v in value))


@dataclass
class Symbols:
    is_entity: bool = False
    roles: set[str] = field(default_factory=set)
    resources: set[str] = field(default_factory=set)
    collections: set[str] = field(default_factory=set)
    values: set[str] = field(default_factory=set)

    def ref_ok(self, ref: str) -> bool:
        if ref in {"default", "$item"} or ref.startswith("$item."):
            return True
        if self.is_entity:
            return ref in {"entity.subject_map", "entity.label_map", "entity.description_map", "entity.class", "entity.class_mapping"}
        if ref in {"statement_unit_type", "statement_unit_type_local", "semantic_unit", "semantic_unit_map", "semantic_unit.subject_map", "semantic_unit.term_map", "label_map", "semantic_unit.label_map", "semantic_unit_label_map"}:
            return True
        if ref.startswith("roles."):
            return len(ref.split(".")) >= 2 and ref.split(".")[1] in self.roles
        if ref.startswith("resources."):
            return len(ref.split(".")) >= 2 and ref.split(".")[1] in self.resources
        root = ref.split(".", 1)[0]
        return root in self.collections or root in self.values


def _symbols(family: dict[str, Any]) -> Symbols:
    if family.get("id") == "entity":
        return Symbols(is_entity=True)
    r = family.get("resolution") or {}
    return Symbols(
        roles=set((r.get("roles") or {}).keys()) if isinstance(r.get("roles") or {}, dict) else set(),
        resources=set((r.get("resources") or {}).keys()) if isinstance(r.get("resources") or {}, dict) else set(),
        collections=set((r.get("collections") or {}).keys()) if isinstance(r.get("collections") or {}, dict) else set(),
        values=set((r.get("values") or {}).keys()) if isinstance(r.get("values") or {}, dict) else set(),
    )


def _looks_like_curie_or_iri(value: str) -> bool:
    return value.startswith(("http://", "https://", "<")) or (":" in value and not value.startswith(("roles.", "resources.", "semantic_unit")))


def _validate_ref(result: FamilyValidationResult, value: Any, path: str, symbols: Symbols, *, allow_constant: bool = False) -> None:
    if not isinstance(value, str):
        return
    if allow_constant and not symbols.ref_ok(value):
        # equals/not_equals often compare a resolved scalar to a literal string such as
        # "time_position". Treat unknown strings in that context as constants.
        return
    if not symbols.ref_ok(value):
        result.error(path, f"unknown reference {value!r}; declare it under resolution or use a term-map constant")


def _validate_join_conditions(result: FamilyValidationResult, value: Any, path: str) -> None:
    if value is None: return
    if not isinstance(value, list):
        result.error(path, "must be a list"); return
    for i, item in enumerate(value):
        ip = _p(path, i)
        if not isinstance(item, dict):
            result.error(ip, "must be a mapping"); continue
        if not (("child" in item and "parent" in item) or ("left" in item and "right" in item)):
            result.error(ip, "must contain child/parent or left/right")


def _validate_term(result: FamilyValidationResult, value: Any, path: str, symbols: Symbols) -> None:
    if isinstance(value, str):
        _validate_ref(result, value, path, symbols)
        return
    if not isinstance(value, dict):
        result.error(path, "must be a string reference or term-map mapping"); return
    _unknown(result, value, ALLOWED_TERM, path)
    tt = value.get("term_type", value.get("termType"))
    if tt is not None and tt not in ALLOWED_TERM_TYPES:
        result.error(_p(path, "term_type"), f"unsupported term type {tt!r}")
    keys = [k for k in TERM_KEYS if k in value]
    if len(keys) != 1:
        result.error(path, "term map must define exactly one of constant/reference/template/parent/parent_triples_map/parentTriplesMap")
    for key in ("join", "join_conditions", "joinConditions"):
        if key in value:
            _validate_join_conditions(result, value[key], _p(path, key))


def _validate_condition(result: FamilyValidationResult, cond: Any, path: str, symbols: Symbols) -> None:
    if cond is None or isinstance(cond, bool): return
    if isinstance(cond, str):
        _validate_ref(result, cond, path, symbols); return
    if not isinstance(cond, dict):
        result.error(path, "condition must be bool, string path, or mapping"); return
    _unknown(result, cond, ALLOWED_CONDITION, path)
    for key in ("all", "any"):
        if key in cond:
            if not isinstance(cond[key], list): result.error(_p(path, key), "must be a list")
            else:
                for i, item in enumerate(cond[key]): _validate_condition(result, item, _p(_p(path, key), i), symbols)
    if "not" in cond: _validate_condition(result, cond["not"], _p(path, "not"), symbols)
    if "exists" in cond:
        if not isinstance(cond["exists"], str): result.error(_p(path, "exists"), "must be a string path")
        else: _validate_ref(result, cond["exists"], _p(path, "exists"), symbols)
    for key in ("equals", "not_equals", "not_equal"):
        if key in cond:
            if not isinstance(cond[key], list) or len(cond[key]) != 2: result.error(_p(path, key), "must be a two-item list")
            else:
                for i, item in enumerate(cond[key]): _validate_ref(result, item, _p(_p(path, key), i), symbols, allow_constant=True)


def _validate_resolution(result: FamilyValidationResult, family: dict[str, Any], path: str) -> None:
    fid = family.get("id")
    r = family.get("resolution")
    if fid == "entity":
        if r is not None: result.error(path, "entity is the bootstrap family and must not define resolution")
        return
    if not isinstance(r, dict):
        result.error(path, "non-entity families must define a resolution mapping"); return
    _unknown(result, r, ALLOWED_RESOLUTION, path)
    if "resolved_order" in r and (not isinstance(r["resolved_order"], list) or not all(isinstance(x, str) for x in r["resolved_order"])):
        result.error(_p(path, "resolved_order"), "must be a list of strings")
    for key in ("statement_unit_type", "semantic_unit"):
        if key in r and not isinstance(r[key], bool): result.error(_p(path, key), "must be boolean")
    for section, allowed, is_role in (("roles", ALLOWED_ROLE, True), ("resources", ALLOWED_RESOURCE, False)):
        block = r.get(section) or {}
        if not isinstance(block, dict): result.error(_p(path, section), "must be a mapping"); continue
        for name, spec in block.items():
            sp = _p(_p(path, section), name)
            if spec is None: continue
            if not isinstance(spec, dict): result.error(sp, "must be a mapping"); continue
            _unknown(result, spec, allowed, sp)
            if "path" in spec and not _is_str_or_str_list(spec["path"]): result.error(_p(sp, "path"), "must be a string or list of strings")
            if "required" in spec and not isinstance(spec["required"], bool): result.error(_p(sp, "required"), "must be boolean")
            if is_role and "resolver" in spec and spec["resolver"] != "predicate": result.error(_p(sp, "resolver"), "only 'predicate' is supported")
            for tkey in ("default_term_type", "term_type"):
                if tkey in spec and spec[tkey] not in ALLOWED_TERM_TYPES: result.error(_p(sp, tkey), f"unsupported term type {spec[tkey]!r}")
            if "default_term_map" in spec: _validate_term(result, spec["default_term_map"], _p(sp, "default_term_map"), Symbols())
    collections = r.get("collections") or {}
    if not isinstance(collections, dict): result.error(_p(path, "collections"), "must be a mapping")
    else:
        for name, spec in collections.items():
            sp = _p(_p(path, "collections"), name)
            if not isinstance(spec, dict): result.error(sp, "must be a mapping"); continue
            if spec.get("builder") not in ALLOWED_COLLECTION_BUILDERS: result.error(_p(sp, "builder"), f"unsupported builder {spec.get('builder')!r}")
            for key in spec:
                if key != "builder": result.error(_p(sp, key), "unsupported key")
    values = r.get("values") or {}
    if not isinstance(values, dict): result.error(_p(path, "values"), "must be a mapping")
    else:
        for name, spec in values.items():
            sp = _p(_p(path, "values"), name)
            if spec is None: spec = {}
            if not isinstance(spec, dict): result.error(sp, "must be a mapping"); continue
            _unknown(result, spec, ALLOWED_VALUE, sp)
            kind = spec.get("kind", "scalar")
            if kind not in ALLOWED_VALUE_KINDS: result.error(_p(sp, "kind"), f"unsupported value kind {kind!r}")
            if kind == "bool_exists" and not spec.get("exists"): result.error(_p(sp, "exists"), "required when kind=bool_exists")
            for pkey in ("path", "family_path", "default_family_path"):
                if pkey in spec and not _is_str_or_str_list(spec[pkey]): result.error(_p(sp, pkey), "must be a string or list of strings")


def _validate_bundle_conditions(result: FamilyValidationResult, mapping: dict[str, Any], path: str, symbols: Symbols) -> None:
    if "when" in mapping: _validate_condition(result, mapping["when"], _p(path, "when"), symbols)
    if "when_exists" in mapping:
        if not isinstance(mapping["when_exists"], str): result.error(_p(path, "when_exists"), "must be a string path")
        else: _validate_ref(result, mapping["when_exists"], _p(path, "when_exists"), symbols)
    for key in ("when_not_equals", "when_not_equal"):
        if key in mapping:
            v = mapping[key]
            if not isinstance(v, list) or len(v) != 2: result.error(_p(path, key), "must be a two-item list")
            else:
                for i, item in enumerate(v): _validate_ref(result, item, _p(_p(path, key), i), symbols, allow_constant=True)


def _validate_po(result: FamilyValidationResult, po: Any, path: str, symbols: Symbols) -> None:
    if not isinstance(po, dict): result.error(path, "must be a mapping"); return
    _unknown(result, po, ALLOWED_PO, path)
    if "for_each" in po:
        if not isinstance(po["for_each"], str): result.error(_p(path, "for_each"), "must be a string path")
        else: _validate_ref(result, po["for_each"], _p(path, "for_each"), symbols)
    if "predicate_map" not in po: result.error(path, "predicate_map is required")
    else: _validate_term(result, po["predicate_map"], _p(path, "predicate_map"), symbols)
    if "object_map" not in po: result.error(path, "object_map is required")
    else: _validate_term(result, po["object_map"], _p(path, "object_map"), symbols)
    if "when_present" in po and not isinstance(po["when_present"], bool): result.error(_p(path, "when_present"), "must be boolean")
    if "optional" in po and not isinstance(po["optional"], bool): result.error(_p(path, "optional"), "must be boolean")
    _validate_bundle_conditions(result, po, path, symbols)


def _validate_emit_bundles(result: FamilyValidationResult, family: dict[str, Any], path: str, symbols: Symbols) -> None:
    bundles = family.get("emit_bundles")
    if not isinstance(bundles, list) or not bundles:
        result.error(path, "must be a non-empty list"); return
    for i, bundle in enumerate(bundles):
        bp = _p(path, i)
        if not isinstance(bundle, dict): result.error(bp, "bundle must be a mapping"); continue
        _unknown(result, bundle, ALLOWED_BUNDLE, bp)
        if not isinstance(bundle.get("id"), str) or not bundle.get("id"): result.error(_p(bp, "id"), "id is required and must be a non-empty string")
        if "kind" in bundle and not isinstance(bundle["kind"], str): result.error(_p(bp, "kind"), "must be a string")
        if "subject_map" in bundle: _validate_term(result, bundle["subject_map"], _p(bp, "subject_map"), symbols)
        elif "subject" in bundle: _validate_term(result, bundle["subject"], _p(bp, "subject"), symbols)
        else: result.error(bp, "bundle must define subject_map or subject")
        if "graph_map" in bundle and bundle["graph_map"] != "default": _validate_term(result, bundle["graph_map"], _p(bp, "graph_map"), symbols)
        if "graph" in bundle and bundle["graph"] != "default": _validate_term(result, bundle["graph"], _p(bp, "graph"), symbols)
        _validate_bundle_conditions(result, bundle, bp, symbols)
        poms = bundle.get("predicate_object_maps")
        if not isinstance(poms, list): result.error(_p(bp, "predicate_object_maps"), "must be a list"); continue
        for j, po in enumerate(poms): _validate_po(result, po, _p(_p(bp, "predicate_object_maps"), j), symbols)


def _validate_default_label_map(result: FamilyValidationResult, family: dict[str, Any], path: str, symbols: Symbols) -> None:
    spec = family.get("default_label_map", family.get("default_label"))
    if spec is None:
        return
    if spec is False:
        return
    if not isinstance(spec, dict):
        result.error(path, "must be a mapping")
        return
    _unknown(result, spec, ALLOWED_LABEL, path)
    if "target" in spec and spec["target"] != "semantic_unit":
        result.error(_p(path, "target"), "only 'semantic_unit' is supported")
    if "tokens" in spec:
        if not isinstance(spec["tokens"], dict):
            result.error(_p(path, "tokens"), "must be a mapping")
        else:
            for token, token_spec in spec["tokens"].items():
                tp = _p(_p(path, "tokens"), token)
                if isinstance(token_spec, str):
                    _validate_ref(result, token_spec, tp, symbols, allow_constant=True)
                elif isinstance(token_spec, dict):
                    _unknown(result, token_spec, ALLOWED_LABEL_TOKEN, tp)
                    if "from" in token_spec:
                        _validate_ref(result, token_spec["from"], _p(tp, "from"), symbols, allow_constant=False)
                    if "from_entity_label" in token_spec:
                        _validate_ref(result, token_spec["from_entity_label"], _p(tp, "from_entity_label"), symbols, allow_constant=False)
                    if "from_time_field_predicate" in token_spec and not isinstance(token_spec["from_time_field_predicate"], str):
                        result.error(_p(tp, "from_time_field_predicate"), "must be a string")
                    if "from_label_token" in token_spec and not isinstance(token_spec["from_label_token"], str):
                        result.error(_p(tp, "from_label_token"), "must be a string")
                    if "allow_cross_source" in token_spec and not isinstance(token_spec["allow_cross_source"], bool):
                        result.error(_p(tp, "allow_cross_source"), "must be boolean")
                    for bool_key in ("humanize", "humanize_iri_constants"):
                        if bool_key in token_spec and not isinstance(token_spec[bool_key], bool):
                            result.error(_p(tp, bool_key), "must be boolean")
                else:
                    result.error(tp, "must be a string path or mapping")
    if "cases" in spec:
        cases = spec["cases"]
        if not isinstance(cases, list) or not cases:
            result.error(_p(path, "cases"), "must be a non-empty list")
        else:
            for i, case in enumerate(cases):
                cp = _p(_p(path, "cases"), i)
                if not isinstance(case, dict):
                    result.error(cp, "must be a mapping")
                    continue
                _unknown(result, case, ALLOWED_LABEL_CASE, cp)
                if not isinstance(case.get("template"), str):
                    result.error(_p(cp, "template"), "required and must be a string")
                if "requires" in case and (not isinstance(case["requires"], list) or not all(isinstance(x, str) for x in case["requires"])):
                    result.error(_p(cp, "requires"), "must be a list of strings")
    elif "template" not in spec or not isinstance(spec.get("template"), str):
        result.error(_p(path, "template"), "required and must be a string when cases are not used")




def _validate_joined_label_map(result: FamilyValidationResult, family: dict[str, Any], path: str, symbols: Symbols) -> None:
    spec = family.get("joined_label_map")
    if spec is None or spec is False:
        return
    if not isinstance(spec, dict):
        result.error(path, "must be a mapping")
        return
    _unknown(result, spec, ALLOWED_LABEL, path)
    if "target" in spec and spec["target"] != "semantic_unit":
        result.error(_p(path, "target"), "only 'semantic_unit' is supported")
    if "mode" in spec and spec["mode"] != "joined_table":
        result.error(_p(path, "mode"), "only 'joined_table' is supported for now")
    if "template" in spec and not isinstance(spec["template"], str):
        result.error(_p(path, "template"), "must be a string")
    if "fallback_template" in spec and not isinstance(spec["fallback_template"], str):
        result.error(_p(path, "fallback_template"), "must be a string")
    if "cases" not in spec and "template" not in spec:
        result.error(path, "must define template or cases")
    if "tokens" in spec:
        if not isinstance(spec["tokens"], dict):
            result.error(_p(path, "tokens"), "must be a mapping")
        else:
            for token, token_spec in spec["tokens"].items():
                tp = _p(_p(path, "tokens"), token)
                if isinstance(token_spec, str):
                    _validate_ref(result, token_spec, tp, symbols, allow_constant=True)
                elif isinstance(token_spec, dict):
                    _unknown(result, token_spec, ALLOWED_LABEL_TOKEN, tp)
                    for key in ("from", "from_entity_label", "from_semantic_unit_label", "from_association_member_labels"):
                        if key in token_spec:
                            _validate_ref(result, token_spec[key], _p(tp, key), symbols, allow_constant=False)
                    if "from_instance" in token_spec and not isinstance(token_spec["from_instance"], str):
                        result.error(_p(tp, "from_instance"), "must be a string")
                    if "from_time_field_predicate" in token_spec and not isinstance(token_spec["from_time_field_predicate"], str):
                        result.error(_p(tp, "from_time_field_predicate"), "must be a string")
                    if "from_label_token" in token_spec and not isinstance(token_spec["from_label_token"], str):
                        result.error(_p(tp, "from_label_token"), "must be a string")
                    for bool_key in ("allow_cross_source", "allow_join", "humanize", "humanize_iri_constants"):
                        if bool_key in token_spec and not isinstance(token_spec[bool_key], bool):
                            result.error(_p(tp, bool_key), "must be boolean")
                else:
                    result.error(tp, "must be a string path or mapping")
    if "cases" in spec:
        cases = spec["cases"]
        if not isinstance(cases, list) or not cases:
            result.error(_p(path, "cases"), "must be a non-empty list")
        else:
            for i, case in enumerate(cases):
                cp = _p(_p(path, "cases"), i)
                if not isinstance(case, dict):
                    result.error(cp, "must be a mapping")
                    continue
                _unknown(result, case, ALLOWED_LABEL_CASE, cp)
                if not isinstance(case.get("template"), str):
                    result.error(_p(cp, "template"), "required and must be a string")
                if "requires" in case and (not isinstance(case["requires"], list) or not all(isinstance(x, str) for x in case["requires"])):
                    result.error(_p(cp, "requires"), "must be a list of strings")


def validate_family_doc(family: dict[str, Any], *, path: str = "<family>") -> FamilyValidationResult:
    result = FamilyValidationResult()
    if not isinstance(family, dict):
        result.error(path, "family document must be a mapping"); return result
    _unknown(result, family, ALLOWED_TOP, path)
    fid = family.get("id")
    if not isinstance(fid, str) or not fid: result.error(_p(path, "id"), "required and must be a non-empty string")
    if "extends" in family and not isinstance(family["extends"], str): result.error(_p(path, "extends"), "must be a string")
    if not isinstance(family.get("execution_layer"), int): result.error(_p(path, "execution_layer"), "required and must be an integer")
    symbols = _symbols(family)
    _validate_resolution(result, family, _p(path, "resolution"))
    _validate_default_label_map(result, family, _p(path, "default_label_map" if "default_label_map" in family else "default_label"), symbols)
    _validate_joined_label_map(result, family, _p(path, "joined_label_map"), symbols)
    _validate_emit_bundles(result, family, _p(path, "emit_bundles"), symbols)
    return result


def validate_family_docs(family_docs: list[dict[str, Any]], *, paths: list[str] | None = None, raise_on_error: bool = True) -> FamilyValidationResult:
    result = FamilyValidationResult()
    seen: dict[str, str] = {}
    for i, family in enumerate(family_docs):
        p = paths[i] if paths and i < len(paths) else f"family[{i}]"
        r = validate_family_doc(family, path=p)
        result.extend(r)
        fid = family.get("id") if isinstance(family, dict) else None
        if isinstance(fid, str):
            if fid in seen: result.error(_p(p, "id"), f"duplicate family id also seen in {seen[fid]}")
            else: seen[fid] = p
    if raise_on_error: result.raise_for_errors()
    return result


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict): raise FamilyValidationError(f"Expected mapping at top level in {path}")
    return data


def load_family_docs(paths: Iterable[Path], families_dir: Path | None = None) -> list[tuple[Path, dict[str, Any]]]:
    family_paths: list[Path] = []
    if families_dir is not None:
        if not families_dir.exists(): raise FamilyValidationError(f"Families directory not found: {families_dir}")
        if not families_dir.is_dir(): raise FamilyValidationError(f"--families-dir must point to a directory: {families_dir}")
        family_paths.extend(sorted(families_dir.glob("*.family.rml.yaml")))
    family_paths.extend(paths)
    out: list[tuple[Path, dict[str, Any]]] = []
    seen: set[Path] = set()
    for path in family_paths:
        path = Path(path)
        if path in seen: continue
        seen.add(path)
        out.append((path, load_yaml(path)))
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validate RML family YAML files.")
    parser.add_argument("families", nargs="*", help="Explicit family YAML files")
    parser.add_argument("--families-dir", help="Directory containing *.family.rml.yaml files")
    parser.add_argument("--show-warnings", action="store_true", help="Print non-fatal warnings")
    args = parser.parse_args(argv)
    loaded = load_family_docs([Path(p) for p in args.families], Path(args.families_dir) if args.families_dir else None)
    result = validate_family_docs([doc for _, doc in loaded], paths=[str(path) for path, _ in loaded], raise_on_error=False)
    if result.errors:
        for msg in result.errors: print(msg.format())
        raise SystemExit(1)
    if args.show_warnings:
        for msg in result.warnings: print(msg.format())
    print(f"Validated {len(loaded)} family file(s).")


if __name__ == "__main__":
    main()
