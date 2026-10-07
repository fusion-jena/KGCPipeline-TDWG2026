from __future__ import annotations

import argparse
import copy
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from family_validator import FamilyValidationError, validate_family_docs

YARRRML_VAR_RE = re.compile(r"\$\(([^)]+)\)")
PREFIX_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.+)$")


@dataclass
class TermMapSpec:
    """Backend-neutral/RML-style term map used for subject, object, and graph maps."""

    kind: str  # constant, reference, template, parent
    term_type: str = "iri"  # iri, literal, blanknode
    constant: Optional[str] = None
    reference: Optional[str] = None
    template: Optional[str] = None
    parent_triples_map: Optional[str] = None
    join_conditions: Optional[List[Dict[str, str]]] = None
    datatype: Optional[str] = None
    language: Optional[str] = None
    language_reference: Optional[str] = None
    language_template: Optional[str] = None


@dataclass
class PredicateObjectSpec:
    predicate_map: TermMapSpec
    object_map: TermMapSpec
    when_present: bool = False


@dataclass
class Bundle:
    id: str
    instance_id: str
    family: str
    kind: str
    source: str
    subject_map: TermMapSpec
    graph_map: Optional[TermMapSpec]
    predicate_object_maps: List[PredicateObjectSpec]


class ConfigError(ValueError):
    pass


# ============================================================
# I/O helpers
# ============================================================


def load_yaml(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Expected mapping at top level in {path}")
    return data


def write_yaml_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def write_outputs(result: Dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_yaml_file(
        out_dir / "normalized_instances.yaml",
        {
            "prefixes": result["prefixes"],
            "sources": result["sources"],
            "instances": result["instances"],
            "entity_registry": result["entity_registry"],
        },
    )
    write_yaml_file(
        out_dir / "bundle_ir.yaml",
        {
            "prefixes": result["prefixes"],
            "sources": result["sources"],
            "bundles": result["bundles"],
        },
    )
    write_yaml_file(
        out_dir / "triplesmap_ir.yaml",
        {
            "prefixes": result["prefixes"],
            "sources": result["sources"],
            "triples_maps": result["triples_maps"],
        },
    )
    write_yaml_file(
        out_dir / "label_jobs.yaml",
        {
            "prefixes": result["prefixes"],
            "sources": result["sources"],
            "label_jobs": result.get("label_jobs", []),
        },
    )


# ============================================================
# Term expansion / normalization
# ============================================================


def to_rml_template(value: str) -> str:
    return YARRRML_VAR_RE.sub(lambda m: "{" + m.group(1) + "}", value)


def curie_to_full(value: str, prefixes: Dict[str, str]) -> str:
    if not isinstance(value, str):
        return value
    if value.startswith("http://") or value.startswith("https://") or value.startswith("<"):
        return value.strip("<>")
    m = PREFIX_RE.match(value)
    if not m:
        return value
    prefix, rest = m.groups()
    if prefix not in prefixes:
        return value
    return prefixes[prefix] + rest


def expand_prefixed_template(value: str, prefixes: Dict[str, str]) -> str:
    value = to_rml_template(value)
    if value.startswith("http://") or value.startswith("https://"):
        return value
    m = PREFIX_RE.match(value)
    if not m:
        return value
    prefix, rest = m.groups()
    if prefix not in prefixes:
        return value
    return prefixes[prefix] + rest


def normalize_term_type(value: Optional[str], default: str = "iri") -> str:
    if value is None:
        return default
    aliases = {
        "IRI": "iri",
        "iri": "iri",
        "rr:IRI": "iri",
        "literal": "literal",
        "Literal": "literal",
        "rr:Literal": "literal",
        "blanknode": "blanknode",
        "blank_node": "blanknode",
        "BlankNode": "blanknode",
        "rr:BlankNode": "blanknode",
    }
    if value not in aliases:
        raise ConfigError(f"Unsupported term_type: {value}")
    return aliases[value]


def tm_id_for_instance(instance_id: str) -> str:
    return f"TM_{instance_id}_base"


def resolve_parent_tm_id(value: str, tm_name_lookup: Dict[str, str]) -> str:
    # Allow either RML TriplesMap ids or high-level instance ids.
    if value in tm_name_lookup:
        return tm_name_lookup[value]
    return value


def normalize_join_conditions(raw: Any) -> Optional[List[Dict[str, str]]]:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ConfigError("join_conditions must be a list")
    out: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ConfigError("Each join condition must be a mapping")
        if "child" in item and "parent" in item:
            out.append({"child": str(item["child"]), "parent": str(item["parent"])})
        elif "left" in item and "right" in item:
            # Backward-compatible shorthand from the first prototype.
            out.append({"child": str(item["left"]), "parent": str(item["right"])})
        else:
            raise ConfigError(f"Join condition needs child/parent or left/right: {item}")
    return out


def language_map_from_mapping(cfg: Dict[str, Any], prefixes: Dict[str, str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return fixed language, language-reference, and language-template specs.

    ``language`` is the R2RML fixed-language form (``rr:language "en"``).
    ``language_reference`` / ``language_template`` are RML language maps and are
    needed when the language tag comes from a source column.
    """
    language = cfg.get("language")
    language_reference = cfg.get("language_reference") or cfg.get("languageReference")
    language_template = cfg.get("language_template") or cfg.get("languageTemplate")

    language_map = cfg.get("language_map") or cfg.get("languageMap")
    if language_map is not None:
        if not isinstance(language_map, dict):
            raise ConfigError("language_map must be a mapping with reference/template/constant")
        if language_map.get("reference") is not None:
            language_reference = str(language_map["reference"])
        elif language_map.get("template") is not None:
            language_template = to_rml_template(str(language_map["template"]))
        elif language_map.get("constant") is not None:
            language = str(language_map["constant"])
        else:
            raise ConfigError("language_map must define reference, template, or constant")

    language_specs = [v for v in (language, language_reference, language_template) if v]
    if len(language_specs) > 1:
        raise ConfigError("Use only one of language, language_reference, language_template, or language_map")

    if language_template is not None:
        language_template = to_rml_template(str(language_template))

    return (str(language) if language is not None else None,
            str(language_reference) if language_reference is not None else None,
            str(language_template) if language_template is not None else None)


def validate_literal_language_datatype(term_type: str, datatype: Optional[str], language: Optional[str], language_reference: Optional[str], language_template: Optional[str]) -> None:
    has_language = bool(language or language_reference or language_template)
    if has_language and term_type != "literal":
        raise ConfigError("Language tags can only be used on literal term maps")
    if has_language and datatype:
        raise ConfigError("A literal term map cannot define both datatype and language/language_reference")


def term_map_from_mapping(
    cfg: Dict[str, Any],
    prefixes: Dict[str, str],
    *,
    default_term_type: str = "iri",
    tm_name_lookup: Optional[Dict[str, str]] = None,
) -> TermMapSpec:
    """Normalize RML/R2RML-style YAML into one internal TermMapSpec."""

    if not isinstance(cfg, dict):
        raise ConfigError(f"Expected term map mapping, got {type(cfg).__name__}")

    term_type = normalize_term_type(cfg.get("term_type") or cfg.get("termType"), default_term_type)
    datatype = curie_to_full(cfg["datatype"], prefixes) if cfg.get("datatype") else None
    language, language_reference, language_template = language_map_from_mapping(cfg, prefixes)
    validate_literal_language_datatype(term_type, datatype, language, language_reference, language_template)

    # Parent/object map. Allow parent, parent_triples_map, and parentTriplesMap.
    parent_value = cfg.get("parent_triples_map") or cfg.get("parentTriplesMap") or cfg.get("parent")
    if parent_value:
        parent_tm = resolve_parent_tm_id(str(parent_value), tm_name_lookup or {})
        return TermMapSpec(
            kind="parent",
            term_type="iri",
            parent_triples_map=parent_tm,
            join_conditions=normalize_join_conditions(cfg.get("join_conditions") or cfg.get("joinConditions") or cfg.get("join")),
            datatype=datatype,
            language=language,
            language_reference=language_reference,
            language_template=language_template,
        )

    if "reference" in cfg:
        return TermMapSpec(
            kind="reference",
            term_type=term_type,
            reference=str(cfg["reference"]),
            datatype=datatype,
            language=language,
            language_reference=language_reference,
            language_template=language_template,
        )

    if "template" in cfg:
        return TermMapSpec(
            kind="template",
            term_type=term_type,
            template=expand_prefixed_template(str(cfg["template"]), prefixes),
            datatype=datatype,
            language=language,
            language_reference=language_reference,
            language_template=language_template,
        )

    if "constant" in cfg:
        raw_value = cfg["constant"]
        if term_type == "iri":
            constant = curie_to_full(str(raw_value), prefixes)
        else:
            if raw_value is True:
                constant = "true"
            elif raw_value is False:
                constant = "false"
            else:
                constant = str(raw_value)
        return TermMapSpec(
            kind="constant",
            term_type=term_type,
            constant=constant,
            datatype=datatype,
            language=language,
            language_reference=language_reference,
            language_template=language_template,
        )

    raise ConfigError(f"Term map must define one of reference/template/constant/parent_triples_map: {cfg}")


def term_map_to_tm_dict(term: TermMapSpec) -> Dict[str, Any]:
    out: Dict[str, Any] = {"term_type": term.term_type}
    if term.constant is not None:
        out["constant"] = term.constant
    if term.reference is not None:
        out["reference"] = term.reference
    if term.template is not None:
        out["template"] = term.template
    if term.parent_triples_map is not None:
        out["parent_triples_map"] = term.parent_triples_map
    if term.join_conditions:
        out["join_conditions"] = term.join_conditions
    if term.datatype:
        out["datatype"] = term.datatype
    if term.language:
        out["language"] = term.language
    if term.language_reference:
        out["language_reference"] = term.language_reference
    if term.language_template:
        out["language_template"] = term.language_template
    return out


# ============================================================
# Source normalization
# ============================================================


def normalize_sources(raw_sources: Dict[str, Any] | List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if isinstance(raw_sources, list):
        items = [(s.get("id"), s) for s in raw_sources]
    elif isinstance(raw_sources, dict):
        items = list(raw_sources.items())
    else:
        raise ConfigError("sources must be a mapping or list")

    out: List[Dict[str, Any]] = []
    for sid, source in items:
        if not sid:
            raise ConfigError(f"Source is missing id: {source}")
        if not isinstance(source, dict):
            raise ConfigError(f"Source {sid} must be a mapping")
        source_format = source.get("format") or source.get("reference_formulation") or source.get("referenceFormulation")
        out.append(
            {
                "id": sid,
                "access": source["access"],
                "format": source_format,
                # Keep this alias for older renderers/debugging, but renderer should prefer format.
                "reference_formulation": source_format,
                "delimiter": source.get("delimiter"),
                "iterator": source.get("iterator"),
            }
        )
    return out


# ============================================================
# Entity registry and role resolution
# ============================================================


def entity_subject_map_from_legacy(entity_cfg: Dict[str, Any], prefixes: Dict[str, str], instance_id: str) -> TermMapSpec:
    iri_source = entity_cfg.get("iri_source")

    if iri_source == "template":
        # Heuristic: if users said identifier_format=full_iri and the template is only
        # prefixing that identifier column, use a reference term map instead. This keeps
        # legacy examples from producing double/percent-encoded IRIs.
        identifier_column = entity_cfg.get("identifier_column")
        if entity_cfg.get("identifier_format") == "full_iri" and identifier_column:
            return TermMapSpec(kind="reference", reference=identifier_column, term_type="iri")
        if not entity_cfg.get("iri_template"):
            raise ConfigError(f"entity.iri_source=template requires entity.iri_template in {instance_id}")
        return TermMapSpec(
            kind="template",
            template=expand_prefixed_template(entity_cfg["iri_template"], prefixes),
            term_type="iri",
        )

    if iri_source == "identifier_column":
        identifier_column = entity_cfg.get("identifier_column")
        identifier_format = entity_cfg.get("identifier_format")
        if not identifier_column:
            raise ConfigError(f"entity.iri_source=identifier_column requires identifier_column in {instance_id}")
        if identifier_format == "full_iri":
            return TermMapSpec(kind="reference", reference=identifier_column, term_type="iri")
        if identifier_format == "local_name_ex":
            return TermMapSpec(kind="template", template=f"{prefixes['ex']}{{{identifier_column}}}", term_type="iri")
        raise ConfigError(f"Unsupported identifier_format for RML backend in {instance_id}: {identifier_format}")

    raise ConfigError(
        f"Entity {instance_id} should use entity.subject_map in the new config, "
        f"or a supported legacy iri_source. Got: {iri_source}"
    )


def resolve_entity_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    tm_name_lookup: Dict[str, str],
) -> Dict[str, Any]:
    family = families.get(inst["family"], {"execution_layer": 0})
    entity_cfg = inst.get("entity", {})
    if not isinstance(entity_cfg, dict):
        raise ConfigError(f"Entity instance {instance_id} needs an entity mapping")

    class_value = entity_cfg.get("class") or entity_cfg.get("class_mapping")
    if not class_value:
        raise ConfigError(f"Entity instance {instance_id} needs entity.class or entity.class_mapping")
    class_iri = curie_to_full(class_value, prefixes)

    if "subject_map" in entity_cfg:
        subject_map = term_map_from_mapping(entity_cfg["subject_map"], prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)
    else:
        subject_map = entity_subject_map_from_legacy(entity_cfg, prefixes, instance_id)

    label_map = None
    if "label_map" in entity_cfg:
        # Entity-local label_map is the most specific form and therefore wins.
        label_map = term_map_from_mapping(entity_cfg["label_map"], prefixes, default_term_type="literal", tm_name_lookup=tm_name_lookup)
    elif "label_map" in inst:
        # Shared label syntax: all families, including entity, may define a top-level label_map.
        label_map = term_map_from_mapping(inst["label_map"], prefixes, default_term_type="literal", tm_name_lookup=tm_name_lookup)
    elif entity_cfg.get("label_column"):
        label_map = TermMapSpec(kind="reference", reference=entity_cfg["label_column"], term_type="literal")

    description_map = None
    if "description_map" in entity_cfg:
        description_map = term_map_from_mapping(entity_cfg["description_map"], prefixes, default_term_type="literal", tm_name_lookup=tm_name_lookup)
    elif entity_cfg.get("description_column"):
        description_map = TermMapSpec(kind="reference", reference=entity_cfg["description_column"], term_type="literal")

    return {
        "id": instance_id,
        "family": "entity",
        "source": inst["source"],
        "execution_layer": family.get("execution_layer", 0),
        "resolved": {
            "subject_map": asdict(subject_map),
            "class_iri": class_iri,
            "label_map": asdict(label_map) if label_map else None,
            "description_map": asdict(description_map) if description_map else None,
            "triples_map_id": tm_name_lookup[instance_id],
        },
    }


def build_entity_registry(resolved_entities: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    registry: Dict[str, Dict[str, Any]] = {}
    for entry in resolved_entities:
        r = entry["resolved"]
        registry[entry["id"]] = {
            "id": entry["id"],
            "source": entry["source"],
            "triples_map_id": r["triples_map_id"],
            "subject_map": r["subject_map"],
            "class_iri": r["class_iri"],
            "label_map": r.get("label_map"),
        }
    return registry


def term_from_registry(entity_registry: Dict[str, Dict[str, Any]], instance_id: str) -> TermMapSpec:
    if instance_id not in entity_registry:
        raise ConfigError(f"Unknown entity instance reference: {instance_id}")
    return TermMapSpec(**entity_registry[instance_id]["subject_map"])


def resolve_role_term(
    role: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
    *,
    default_term_type: str = "iri",
) -> TermMapSpec:
    """Resolve new RML-style roles and legacy kind-based roles to a TermMapSpec."""

    if not isinstance(role, dict):
        raise ConfigError(f"Role must be a mapping, got {type(role).__name__}")

    # Compatibility with the older SHACL-pipeline instance syntax.
    if "entity" in role and not role.get("kind"):
        return term_from_registry(entity_registry, role["entity"])

    if "term" in role and not role.get("kind"):
        return TermMapSpec(kind="constant", constant=curie_to_full(role["term"], prefixes), term_type="iri")

    if "literal" in role and not role.get("kind"):
        literal_cfg = role["literal"]
        if not isinstance(literal_cfg, dict):
            raise ConfigError(f"literal role must be a mapping: {role}")
        raw_value = literal_cfg.get("value")
        if raw_value is True:
            value = "true"
        elif raw_value is False:
            value = "false"
        else:
            value = str(raw_value)
        return TermMapSpec(
            kind="constant",
            constant=value,
            term_type="literal",
            datatype=curie_to_full(literal_cfg["datatype"], prefixes) if literal_cfg.get("datatype") else None,
            language=literal_cfg.get("language"),
        )

    if "source_literal" in role and not role.get("kind"):
        literal_cfg = role["source_literal"]
        if not isinstance(literal_cfg, dict):
            raise ConfigError(f"source_literal role must be a mapping: {role}")
        column = literal_cfg.get("reference") or literal_cfg.get("predicate")
        if not column:
            raise ConfigError(f"source_literal role needs predicate/reference: {role}")
        return TermMapSpec(
            kind="reference",
            reference=str(column),
            term_type="literal",
            datatype=curie_to_full(literal_cfg["datatype"], prefixes) if literal_cfg.get("datatype") else None,
            language=literal_cfg.get("language"),
        )

    # New preferred RML/R2RML-ish forms.
    if "term_map" in role:
        return term_map_from_mapping(role["term_map"], prefixes, default_term_type=default_term_type, tm_name_lookup=tm_name_lookup)
    if "object_map" in role:
        return term_map_from_mapping(role["object_map"], prefixes, default_term_type=default_term_type, tm_name_lookup=tm_name_lookup)
    if "subject_map" in role:
        return term_map_from_mapping(role["subject_map"], prefixes, default_term_type=default_term_type, tm_name_lookup=tm_name_lookup)
    if any(k in role for k in ("reference", "template", "constant", "parent", "parent_triples_map", "parentTriplesMap")):
        return term_map_from_mapping(role, prefixes, default_term_type=default_term_type, tm_name_lookup=tm_name_lookup)

    if "instance" in role and not role.get("kind"):
        # Subject-style shorthand: roles.subject.instance: botanical_garden
        # Object-style shorthand can become a parent object map when join_conditions are present.
        if role.get("join_conditions") or role.get("join"):
            return TermMapSpec(
                kind="parent",
                term_type="iri",
                parent_triples_map=tm_name_lookup.get(role["instance"], role["instance"]),
                join_conditions=normalize_join_conditions(role.get("join_conditions") or role.get("join")),
            )
        return term_from_registry(entity_registry, role["instance"])

    # Legacy forms.
    kind = role.get("kind")
    if kind in {"current_instance", "joined_instance"}:
        if kind == "joined_instance":
            return TermMapSpec(
                kind="parent",
                term_type="iri",
                parent_triples_map=tm_name_lookup.get(role["instance"], role["instance"]),
                join_conditions=normalize_join_conditions(role.get("join")),
            )
        return term_from_registry(entity_registry, role["instance"])

    if kind == "constant_iri":
        return TermMapSpec(kind="constant", constant=curie_to_full(role["value"], prefixes), term_type="iri")
    if kind == "iri_template":
        return TermMapSpec(kind="template", template=expand_prefixed_template(role["template"], prefixes), term_type="iri")
    if kind == "column_literal":
        return TermMapSpec(kind="reference", reference=role["reference"], term_type="literal", datatype=role.get("datatype"), language=role.get("language"))
    if kind == "constant_literal":
        return TermMapSpec(kind="constant", constant=str(role["value"]), term_type="literal", datatype=role.get("datatype"), language=role.get("language"))

    raise ConfigError(f"Unsupported role term config: {role}")


def resolve_predicate_role(role: Dict[str, Any], prefixes: Dict[str, str]) -> TermMapSpec:
    if not isinstance(role, dict):
        raise ConfigError("Predicate role must be a mapping")
    if "predicate_map" in role:
        return term_map_from_mapping(role["predicate_map"], prefixes, default_term_type="iri")
    if "constant" in role:
        return TermMapSpec(kind="constant", constant=curie_to_full(role["constant"], prefixes), term_type="iri")
    if "term" in role:
        return TermMapSpec(kind="constant", constant=curie_to_full(role["term"], prefixes), term_type="iri")
    if role.get("kind") == "constant_iri" and "value" in role:
        return TermMapSpec(kind="constant", constant=curie_to_full(role["value"], prefixes), term_type="iri")
    raise ConfigError(f"Only constant predicate maps are supported for now: {role}")





def resolve_semantic_unit_map(
    instance_id: str,
    inst: Dict[str, Any],
    family: Dict[str, Any],
    prefixes: Dict[str, str],
    tm_name_lookup: Dict[str, str],
    *,
    family_label: str,
) -> TermMapSpec:
    """Resolve the semantic unit IRI map from preferred RML-style or legacy config."""

    semantic_unit_cfg = inst.get("semantic_unit", {}) or {}
    if "subject_map" in semantic_unit_cfg:
        return term_map_from_mapping(
            semantic_unit_cfg["subject_map"],
            prefixes,
            default_term_type="iri",
            tm_name_lookup=tm_name_lookup,
        )
    if "term_map" in semantic_unit_cfg:
        return term_map_from_mapping(
            semantic_unit_cfg["term_map"],
            prefixes,
            default_term_type="iri",
            tm_name_lookup=tm_name_lookup,
        )
    if "iri_template" in semantic_unit_cfg:
        return TermMapSpec(
            kind="template",
            template=expand_prefixed_template(semantic_unit_cfg["iri_template"], prefixes),
            term_type="iri",
        )

    default_template = family.get("semantic_unit_defaults", {}).get("iri_template")
    if not default_template:
        raise ConfigError(f"{family_label} instance {instance_id} needs semantic_unit.subject_map/template")
    return TermMapSpec(kind="template", template=expand_prefixed_template(default_template, prefixes), term_type="iri")










def resolve_time_field(
    instance_id: str,
    field: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> Dict[str, Any]:
    if not isinstance(field, dict):
        raise ConfigError(f"time_index_statement {instance_id} time field must be a mapping")
    predicate = field.get("predicate") or field.get("p")
    if not predicate:
        raise ConfigError(f"time_index_statement {instance_id} time field needs predicate")

    if "object_map" in field:
        object_map = term_map_from_mapping(field["object_map"], prefixes, default_term_type="literal", tm_name_lookup=tm_name_lookup)
    elif "term_map" in field:
        object_map = term_map_from_mapping(field["term_map"], prefixes, default_term_type="literal", tm_name_lookup=tm_name_lookup)
    elif "source_predicate" in field:
        object_map = TermMapSpec(
            kind="reference",
            reference=str(field["source_predicate"]),
            term_type="literal",
            datatype=curie_to_full(field["datatype"], prefixes) if field.get("datatype") else None,
            language=field.get("language"),
        )
    elif "term" in field:
        object_map = TermMapSpec(kind="constant", constant=curie_to_full(field["term"], prefixes), term_type="iri")
    elif any(k in field for k in ("reference", "template", "constant", "parent", "parent_triples_map", "parentTriplesMap")):
        object_map = term_map_from_mapping(field, prefixes, default_term_type="literal", tm_name_lookup=tm_name_lookup)
    else:
        raise ConfigError(f"time_index_statement {instance_id} time field needs object_map/reference/constant/source_predicate/term")

    return {
        "predicate": str(predicate),
        "object_map": asdict(object_map),
    }


def resolve_time_fields(
    instance_id: str,
    inst: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> List[Dict[str, Any]]:
    roles = inst.get("roles", {}) or {}
    time_cfg = inst.get("time", {}) or {}
    fields = time_cfg.get("fields") or roles.get("time_fields") or inst.get("time_fields")
    if not fields:
        # Handy default for the common day-of-year case.
        year_col = inst.get("year_column", "Year")
        doy_col = inst.get("day_of_year_column") or inst.get("doy_column")
        unit = inst.get("unit", "time:unitDay")
        fields = [
            {"predicate": "time:unitType", "object_map": {"constant": unit, "term_type": "iri"}},
            {"predicate": "time:year", "object_map": {"reference": year_col, "term_type": "literal", "datatype": "xsd:int"}},
        ]
        if doy_col:
            fields.append({"predicate": "time:dayOfYear", "object_map": {"reference": doy_col, "term_type": "literal", "datatype": "xsd:int"}})
    if not isinstance(fields, list):
        raise ConfigError(f"time_index_statement {instance_id} time.fields/time_fields must be a list")
    return [resolve_time_field(instance_id, f, prefixes, entity_registry, tm_name_lookup) for f in fields]




# ============================================================
# Time order statement resolution
# ============================================================








# ============================================================
# Time order statement resolution
# ============================================================









# ============================================================
# Complex statement resolution
# ============================================================








# ============================================================
# Compound unit resolution
# ============================================================


def resolve_compound_member_term_map(
    instance_id: str,
    entry: Any,
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
    semantic_unit_registry: Dict[str, Dict[str, Any]],
) -> TermMapSpec:
    """Resolve one compound association member.

    Preferred forms:
      - {ref: firstflower_complex_statement_unit}
      - {subject_map: {template: ex:SU/complexStatementUnit/{Observation_ID}_obs_FirstFlower}}
      - {template: ex:SU/complexStatementUnit/{Observation_ID}_obs_FirstFlower, term_type: iri}

    The ref form resolves through already-normalized semantic-unit instances. Therefore the
    referenced instance must appear before the compound instance in the YAML config, or the
    user should provide an explicit subject_map/template.
    """
    if isinstance(entry, str):
        ref_id = entry
        if ref_id not in semantic_unit_registry:
            raise ConfigError(
                f"compound_unit {instance_id} association ref {ref_id!r} is unknown. "
                "Put the referenced semantic-unit instance earlier in the config or use an explicit subject_map."
            )
        return TermMapSpec(**semantic_unit_registry[ref_id])

    if not isinstance(entry, dict):
        raise ConfigError(f"compound_unit {instance_id} association member must be string or mapping: {entry!r}")

    if "subject_map" in entry:
        return term_map_from_mapping(entry["subject_map"], prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)
    if "term_map" in entry:
        return term_map_from_mapping(entry["term_map"], prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)
    if any(k in entry for k in ("reference", "template", "constant", "parent", "parent_triples_map", "parentTriplesMap")):
        return term_map_from_mapping(entry, prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)

    ref_id = entry.get("ref") or entry.get("instance_ref") or entry.get("semantic_unit_ref")
    if ref_id:
        if ref_id not in semantic_unit_registry:
            raise ConfigError(
                f"compound_unit {instance_id} association ref {ref_id!r} is unknown. "
                "Put the referenced semantic-unit instance earlier in the config or use an explicit subject_map."
            )
        return TermMapSpec(**semantic_unit_registry[ref_id])

    raise ConfigError(f"compound_unit {instance_id} association member cannot be resolved: {entry}")


def resolve_compound_members(
    instance_id: str,
    inst: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
    semantic_unit_registry: Dict[str, Dict[str, Any]],
) -> List[TermMapSpec]:
    raw_members = inst.get("association_members")
    if raw_members is None:
        raw_members = inst.get("association_refs")
    if raw_members is None:
        raise ConfigError(f"compound_unit {instance_id} needs association_members or association_refs")
    if not isinstance(raw_members, list) or not raw_members:
        raise ConfigError(f"compound_unit {instance_id} association_members/association_refs must be a non-empty list")
    return [
        resolve_compound_member_term_map(
            instance_id,
            item,
            prefixes,
            entity_registry,
            tm_name_lookup,
            semantic_unit_registry,
        )
        for item in raw_members
    ]




# ============================================================
# Observation statement resolution
# ============================================================

def statement_unit_type_from_instance(instance_id: str, inst: Dict[str, Any], prefixes: Dict[str, str]) -> str:
    if inst.get("statement_unit_type"):
        return curie_to_full(inst["statement_unit_type"], prefixes)
    roles = inst.get("roles", {}) or {}
    sut_role = roles.get("statement_unit_type")
    if isinstance(sut_role, dict):
        if sut_role.get("constant"):
            return curie_to_full(sut_role["constant"], prefixes)
        if sut_role.get("term"):
            return curie_to_full(sut_role["term"], prefixes)
        if sut_role.get("kind") == "constant_iri" and sut_role.get("value"):
            return curie_to_full(sut_role["value"], prefixes)
    raise ConfigError(f"{instance_id} needs statement_unit_type or roles.statement_unit_type.term/constant")













# ============================================================
# Declarative instance resolution
# ============================================================


def _instance_path_lookup(root: Any, path: str) -> Any:
    current = root
    for part in path.split('.'):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(path)
    return current


def _maybe_instance_value(inst: Dict[str, Any], path_or_paths: Any) -> Any:
    if path_or_paths is None:
        return None
    paths = path_or_paths if isinstance(path_or_paths, list) else [path_or_paths]
    for path in paths:
        try:
            return _instance_path_lookup(inst, str(path))
        except KeyError:
            continue
    return None


def _family_path_lookup(family: Dict[str, Any], path: str) -> Any:
    return _instance_path_lookup(family, path)


def _maybe_family_value(family: Dict[str, Any], path_or_paths: Any) -> Any:
    if path_or_paths is None:
        return None
    paths = path_or_paths if isinstance(path_or_paths, list) else [path_or_paths]
    for path in paths:
        try:
            return _family_path_lookup(family, str(path))
        except KeyError:
            continue
    return None


def _format_default_template(template: str, instance_id: str) -> str:
    # Only compiler-reserved placeholders are substituted here. Row-column
    # placeholders like {Observation_ID} must remain in the RML template.
    return template.replace('{instance_id}', instance_id).replace('{profile_id}', instance_id)


def _default_term_from_resolution_spec(
    spec: Dict[str, Any],
    instance_id: str,
    prefixes: Dict[str, str],
    tm_name_lookup: Dict[str, str],
    *,
    default_term_type: str = 'iri',
) -> Optional[TermMapSpec]:
    if 'default_term_map' in spec:
        return term_map_from_mapping(
            spec['default_term_map'],
            prefixes,
            default_term_type=default_term_type,
            tm_name_lookup=tm_name_lookup,
        )
    if 'default_template' in spec:
        return TermMapSpec(
            kind='template',
            template=expand_prefixed_template(_format_default_template(str(spec['default_template']), instance_id), prefixes),
            term_type=normalize_term_type(spec.get('term_type'), default_term_type),
        )
    if 'default_constant' in spec:
        return term_map_from_mapping(
            {
                'constant': spec['default_constant'],
                'term_type': spec.get('term_type', default_term_type),
                **({'datatype': spec['datatype']} if spec.get('datatype') else {}),
                **({'language': spec['language']} if spec.get('language') else {}),
            },
            prefixes,
            default_term_type=default_term_type,
            tm_name_lookup=tm_name_lookup,
        )
    if 'default_reference' in spec:
        return term_map_from_mapping(
            {
                'reference': spec['default_reference'],
                'term_type': spec.get('term_type', default_term_type),
                **({'datatype': spec['datatype']} if spec.get('datatype') else {}),
                **({'language': spec['language']} if spec.get('language') else {}),
            },
            prefixes,
            default_term_type=default_term_type,
            tm_name_lookup=tm_name_lookup,
        )
    return None


def resolve_declarative_role(
    instance_id: str,
    inst: Dict[str, Any],
    role_name: str,
    spec: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> Optional[TermMapSpec]:
    path = spec.get('path', f'roles.{role_name}')
    raw = _maybe_instance_value(inst, path)
    required = bool(spec.get('required', False))
    default_term_type = spec.get('default_term_type', spec.get('term_type', 'iri'))

    if raw is None:
        default_term = _default_term_from_resolution_spec(
            spec,
            instance_id,
            prefixes,
            tm_name_lookup,
            default_term_type=default_term_type,
        )
        if default_term is not None:
            return default_term
        if required:
            raise ConfigError(f"{inst.get('family')} {instance_id} missing {path}")
        return None

    if spec.get('resolver') == 'predicate':
        return resolve_predicate_role(raw, prefixes)
    return resolve_role_term(raw, prefixes, entity_registry, tm_name_lookup, default_term_type=default_term_type)


def resolve_declarative_resource(
    instance_id: str,
    inst: Dict[str, Any],
    resource_name: str,
    spec: Dict[str, Any],
    prefixes: Dict[str, str],
    tm_name_lookup: Dict[str, str],
) -> Optional[TermMapSpec]:
    path = spec.get('path', f'resources.{resource_name}')
    raw = _maybe_instance_value(inst, path)
    required = bool(spec.get('required', False))
    default_term_type = spec.get('default_term_type', spec.get('term_type', 'iri'))

    if raw is None:
        default_term = _default_term_from_resolution_spec(
            spec,
            instance_id,
            prefixes,
            tm_name_lookup,
            default_term_type=default_term_type,
        )
        if default_term is not None:
            return default_term
        if required:
            raise ConfigError(f"{inst.get('family')} {instance_id} missing resource {path}")
        return None

    if not isinstance(raw, dict):
        raise ConfigError(f"Resource {resource_name} in {instance_id} must be a mapping")
    if 'subject_map' in raw:
        return term_map_from_mapping(raw['subject_map'], prefixes, default_term_type='iri', tm_name_lookup=tm_name_lookup)
    if 'term_map' in raw:
        return term_map_from_mapping(raw['term_map'], prefixes, default_term_type='iri', tm_name_lookup=tm_name_lookup)
    if any(k in raw for k in ('reference', 'template', 'constant', 'parent', 'parent_triples_map', 'parentTriplesMap')):
        return term_map_from_mapping(raw, prefixes, default_term_type='iri', tm_name_lookup=tm_name_lookup)
    raise ConfigError(f"Resource {resource_name} in {instance_id} must define subject_map/term_map or a term-map shape")




def _extract_label_map_config(inst: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return an optional user-provided semantic-unit label term map.

    Preferred spelling:
      label_map: {template/reference/constant: ..., term_type: literal}

    Also accepted for readability:
      semantic_unit:
        label_map: {...}

      labels:
        semantic_unit: {...}

      labels:
        semantic_unit:
          label_map: {...}
    """
    raw = inst.get("label_map")
    if raw is None:
        semantic_unit = inst.get("semantic_unit") or {}
        if isinstance(semantic_unit, dict):
            raw = semantic_unit.get("label_map")
    if raw is None:
        labels = inst.get("labels") or {}
        if isinstance(labels, dict):
            raw = labels.get("semantic_unit") or labels.get("semantic_unit_label")
            if isinstance(raw, dict) and "label_map" in raw:
                raw = raw["label_map"]
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("label_map must be a term-map mapping")
    return raw


def _iri_local_name(value: str) -> str:
    """Return the local name of an IRI/CURIE-like value."""
    raw = str(value).strip("<>")
    if "#" in raw:
        raw = raw.rsplit("#", 1)[-1]
    else:
        raw = raw.rstrip("/").rsplit("/", 1)[-1]
    # OBO-style IRIs often end in PREFIX_localName. Keep the useful local part.
    if "_" in raw and re.match(r"^[A-Z][A-Za-z0-9]*_", raw):
        raw = raw.split("_", 1)[1]
    return raw


def _humanize_local_label(value: str) -> str:
    """Make compact ontology/local names readable for default labels.

    Examples:
      pollenReleasingInflorescencePresent -> pollen-releasing inflorescence present
      floweringDuration -> flowering duration
      unitDay -> unit day
    """
    raw = _iri_local_name(value)
    raw = raw.replace("_", "-")
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw).lower()
    # Common ontology label convention used in the phenology examples.
    spaced = spaced.replace("pollen releasing", "pollen-releasing")
    return spaced


def _term_map_label_fragment(value: Any, *, humanize_iri_constants: bool = True) -> Optional[str]:
    """Convert a resolved TermMapSpec-like value into an RML literal-template fragment.

    Reference maps become `{column}` placeholders. Template maps use their row
    placeholders as compact key fallbacks. Literal constants are inserted as-is;
    IRI constants fall back to a local/humanized name. Parent maps cannot be
    converted without an additional RML join and therefore return None.
    """
    term = _term_from_resolved_value(value, default_term_type="literal")
    if term is None:
        return None
    if term.kind == "reference" and term.reference:
        return "{" + term.reference + "}"
    if term.kind == "template" and term.template:
        refs = re.findall(r"\{([^{}]+)\}", term.template)
        if refs:
            return "_".join("{" + ref + "}" for ref in refs)
        return term.template
    if term.kind == "constant" and term.constant is not None:
        if term.term_type == "literal":
            return str(term.constant)
        if humanize_iri_constants:
            return _humanize_local_label(str(term.constant))
        return _iri_local_name(str(term.constant))
    return None


def _find_time_field_object_map(resolved: Dict[str, Any], predicate: str, prefixes: Dict[str, str]) -> Optional[Any]:
    fields = resolved.get("time_fields") or []
    wanted = curie_to_full(str(predicate), prefixes)
    for field in fields:
        if not isinstance(field, dict):
            continue
        current = field.get("predicate")
        if current is None:
            continue
        if str(current) == str(predicate) or curie_to_full(str(current), prefixes) == wanted:
            return field.get("object_map")
    return None


def _instance_label_token(inst: Dict[str, Any], token_name: str) -> Optional[Any]:
    labels = inst.get("label_tokens") or inst.get("label_values") or {}
    if isinstance(labels, dict) and token_name in labels:
        return labels[token_name]
    return None


def _role_entity_ref_from_instance_path(inst: Dict[str, Any], path: str) -> Optional[str]:
    """Return the referenced entity id for an instance role path, if present.

    This is deliberately based on the user mapping rather than the resolved term map,
    because the resolved term map no longer records whether it came from an entity
    instance. The helper is used only for same-source RML label defaults.
    """
    raw = _maybe_instance_value(inst, path)
    if not isinstance(raw, dict):
        return None
    if raw.get("instance"):
        return str(raw["instance"])
    if raw.get("entity"):
        return str(raw["entity"])
    if raw.get("kind") in {"current_instance", "joined_instance"} and raw.get("instance"):
        return str(raw["instance"])
    return None


def _entity_label_fragment_for_role_path(
    inst: Dict[str, Any],
    path: str,
    entity_registry: Dict[str, Dict[str, Any]],
    *,
    allow_cross_source: bool = False,
) -> Optional[str]:
    """Resolve a role's referenced entity label_map into an RML label fragment.

    This supports basic family-default labels such as observation labels using the
    plant label instead of the plant IRI. It only uses labels from the same source
    by default; joined/cross-source entity labels should be handled by an advanced
    join/postprocessing label strategy later.
    """
    entity_ref = _role_entity_ref_from_instance_path(inst, path)
    if not entity_ref:
        return None
    entity = entity_registry.get(entity_ref)
    if not entity:
        return None
    if not allow_cross_source and entity.get("source") != inst.get("source"):
        label_map = entity.get("label_map")
        term = _term_from_resolved_value(label_map, default_term_type="literal") if label_map else None
        # Constant labels are source-independent; references/templates are not.
        if term is None or term.kind != "constant":
            return None
    label_map = entity.get("label_map")
    if label_map:
        return _term_map_label_fragment(label_map)
    # Fallback to the entity subject map if no label_map is configured.
    return _term_map_label_fragment(entity.get("subject_map"))


def _label_token_fragment(
    token_name: str,
    token_spec: Any,
    instance_id: str,
    inst: Dict[str, Any],
    resolved: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[str]:
    if token_spec is None:
        # Useful default: a token with the same name as a role/value/collection can
        # be referenced without writing a token spec.
        for path in (f"roles.{token_name}", f"resources.{token_name}", token_name):
            value = _maybe_resolved_value_for_path({"resolved": resolved}, path)
            if value is not None:
                return _term_map_label_fragment(value) if _is_term_map_dict(value) else str(value)
        inst_token = _instance_label_token(inst, token_name)
        return str(inst_token) if inst_token is not None else None

    if isinstance(token_spec, str):
        value = _maybe_resolved_value_for_path({"resolved": resolved}, token_spec)
        if value is None:
            value = _maybe_instance_value(inst, token_spec)
        if value is None:
            return None
        return _term_map_label_fragment(value) if _is_term_map_dict(value) else str(value)

    if not isinstance(token_spec, dict):
        return str(token_spec)

    if "constant" in token_spec:
        return str(token_spec["constant"])
    if "literal" in token_spec:
        return str(token_spec["literal"])

    default = token_spec.get("default")

    if "from_label_token" in token_spec:
        token_value = _instance_label_token(inst, str(token_spec["from_label_token"]))
        if token_value is not None:
            return str(token_value)
        if default is not None:
            return str(default)

    if "from_entity_label" in token_spec:
        if entity_registry is not None:
            fragment = _entity_label_fragment_for_role_path(
                inst,
                str(token_spec["from_entity_label"]),
                entity_registry,
                allow_cross_source=bool(token_spec.get("allow_cross_source", False)),
            )
            if fragment is not None:
                return fragment
        if default is not None:
            return str(default)
        return None

    humanize_iri_constants = bool(token_spec.get("humanize_iri_constants", token_spec.get("humanize", True)))

    if "from_time_field_predicate" in token_spec:
        value = _find_time_field_object_map(resolved, str(token_spec["from_time_field_predicate"]), prefixes)
        fragment = _term_map_label_fragment(value, humanize_iri_constants=humanize_iri_constants) if value is not None else None
        if fragment is not None:
            return fragment
        if default is not None:
            return str(default)
        return None

    if "from" in token_spec:
        path = str(token_spec["from"])
        value = _maybe_resolved_value_for_path({"resolved": resolved}, path)
        if value is None:
            value = _maybe_instance_value(inst, path)
        fragment = _term_map_label_fragment(value, humanize_iri_constants=humanize_iri_constants) if _is_term_map_dict(value) else (str(value) if value is not None else None)
        if fragment is not None:
            return fragment
        if default is not None:
            return str(default)
        return None

    if default is not None:
        return str(default)
    return None


LABEL_TEMPLATE_TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _render_default_label_template(
    template: str,
    token_specs: Dict[str, Any],
    instance_id: str,
    inst: Dict[str, Any],
    resolved: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[str]:
    token_names = LABEL_TEMPLATE_TOKEN_RE.findall(template)
    replacements: Dict[str, str] = {}
    for token_name in token_names:
        fragment = _label_token_fragment(
            token_name,
            token_specs.get(token_name),
            instance_id,
            inst,
            resolved,
            prefixes,
            entity_registry,
        )
        if fragment is None:
            return None
        replacements[token_name] = fragment

    def replace(match: re.Match[str]) -> str:
        return replacements[match.group(1)]

    return LABEL_TEMPLATE_TOKEN_RE.sub(replace, to_rml_template(str(template)))


def resolve_family_default_label_map(
    instance_id: str,
    inst: Dict[str, Any],
    family: Dict[str, Any],
    resolved: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[TermMapSpec]:
    spec = family.get("default_label_map") or family.get("default_label")
    if spec is None:
        return None
    if spec is False:
        return None
    if not isinstance(spec, dict):
        raise ConfigError(f"Family {family.get('id')} default_label_map must be a mapping")

    token_specs = spec.get("tokens") or {}
    if not isinstance(token_specs, dict):
        raise ConfigError(f"Family {family.get('id')} default_label_map.tokens must be a mapping")

    cases = spec.get("cases")
    if cases is None:
        if "template" not in spec:
            raise ConfigError(f"Family {family.get('id')} default_label_map needs template or cases")
        cases = [{"template": spec["template"], "requires": spec.get("requires", [])}]
    if not isinstance(cases, list):
        raise ConfigError(f"Family {family.get('id')} default_label_map.cases must be a list")

    for idx, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ConfigError(f"Family {family.get('id')} default_label_map.cases[{idx}] must be a mapping")
        template = case.get("template")
        if not isinstance(template, str):
            raise ConfigError(f"Family {family.get('id')} default_label_map.cases[{idx}].template must be a string")
        requires = case.get("requires") or []
        if not isinstance(requires, list):
            raise ConfigError(f"Family {family.get('id')} default_label_map.cases[{idx}].requires must be a list")
        if any(
            _label_token_fragment(str(req), token_specs.get(str(req)), instance_id, inst, resolved, prefixes, entity_registry) is None
            for req in requires
        ):
            continue
        rendered = _render_default_label_template(template, token_specs, instance_id, inst, resolved, prefixes, entity_registry)
        if rendered is not None:
            return TermMapSpec(kind="template", term_type="literal", template=rendered)

    return None


def resolve_declarative_label_map(
    instance_id: str,
    inst: Dict[str, Any],
    family: Dict[str, Any],
    resolved: Dict[str, Any],
    prefixes: Dict[str, str],
    tm_name_lookup: Dict[str, str],
    entity_registry: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[TermMapSpec]:
    raw = _extract_label_map_config(inst)
    if raw is not None:
        return term_map_from_mapping(
            raw,
            prefixes,
            default_term_type="literal",
            tm_name_lookup=tm_name_lookup,
        )
    return resolve_family_default_label_map(instance_id, inst, family, resolved, prefixes, entity_registry)


# ============================================================
# Joined label job IR (planning only)
# ============================================================


def _original_instance_for(resolved: Dict[str, Any], instances: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    inst = instances.get(resolved.get("id"))
    return inst if isinstance(inst, dict) else None


def _template_tokens(template: str) -> List[str]:
    return LABEL_TEMPLATE_TOKEN_RE.findall(str(template))


def _label_job_term_map(value: Any, *, default_term_type: str = "literal") -> Optional[Dict[str, Any]]:
    term = _term_from_resolved_value(value, default_term_type=default_term_type)
    return asdict(term) if term is not None else None


def _compile_joined_label_entity_token(
    token_name: str,
    token_spec: Dict[str, Any],
    instance_id: str,
    inst: Dict[str, Any],
    resolved: Dict[str, Any],
    entity_registry: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    role_path = str(token_spec["from_entity_label"])
    entity_ref = _role_entity_ref_from_instance_path(inst, role_path)
    role_value = _maybe_resolved_value_for_path({"resolved": resolved}, role_path)
    role_term = _term_from_resolved_value(role_value, default_term_type="iri")

    token: Dict[str, Any] = {
        "kind": "entity_label",
        "role_path": role_path,
        "entity_ref": entity_ref,
        "role_term_map": asdict(role_term) if role_term else None,
        "allow_join": bool(token_spec.get("allow_join", False)),
    }

    if not entity_ref or entity_ref not in entity_registry:
        token["status"] = "unresolved"
        token["reason"] = "role does not reference a known entity instance"
        if token_spec.get("default") is not None:
            token["default"] = str(token_spec["default"])
        return token

    entity = entity_registry[entity_ref]
    label_map = entity.get("label_map") or entity.get("subject_map")
    label_term = _term_from_resolved_value(label_map, default_term_type="literal")

    same_source = entity.get("source") == inst.get("source")
    parent_join = role_term.kind == "parent" if role_term else False
    requires_join = (not same_source) or parent_join

    token.update({
        "status": "resolved",
        "entity_source": entity.get("source"),
        "base_source": inst.get("source"),
        "same_source": same_source,
        "requires_join": requires_join,
        "label_map": asdict(label_term) if label_term else None,
    })

    if role_term and role_term.kind == "parent":
        token["parent_triples_map"] = role_term.parent_triples_map
        token["join_conditions"] = role_term.join_conditions or []
        token["join_strategy"] = "parent_triples_map"
    elif requires_join:
        token["join_strategy"] = "unresolved"
        token["status"] = "needs_join_information"
        token["reason"] = "entity label is cross-source but the role did not resolve to a parent term map with join conditions"
    else:
        token["join_strategy"] = "same_source"

    return token


def _compile_joined_label_token(
    token_name: str,
    token_spec: Any,
    instance_id: str,
    inst: Dict[str, Any],
    resolved: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if token_spec is None:
        value = _maybe_resolved_value_for_path({"resolved": resolved}, token_name)
        if value is None:
            value = _maybe_resolved_value_for_path({"resolved": resolved}, f"roles.{token_name}")
        if value is None:
            value = _instance_label_token(inst, token_name)
        if _is_term_map_dict(value):
            return {"kind": "term_map", "path": token_name, "term_map": _label_job_term_map(value)}
        return {"kind": "literal", "value": str(value) if value is not None else None}

    if isinstance(token_spec, str):
        value = _maybe_resolved_value_for_path({"resolved": resolved}, token_spec)
        if value is None:
            value = _maybe_instance_value(inst, token_spec)
        if _is_term_map_dict(value):
            return {"kind": "term_map", "path": token_spec, "term_map": _label_job_term_map(value)}
        return {"kind": "literal", "path": token_spec, "value": str(value) if value is not None else None}

    if not isinstance(token_spec, dict):
        return {"kind": "literal", "value": str(token_spec)}

    if "from_entity_label" in token_spec:
        return _compile_joined_label_entity_token(token_name, token_spec, instance_id, inst, resolved, entity_registry)

    if "from" in token_spec:
        path = str(token_spec["from"])
        value = _maybe_resolved_value_for_path({"resolved": resolved}, path)
        if value is None:
            value = _maybe_instance_value(inst, path)
        if _is_term_map_dict(value):
            return {
                "kind": "term_map",
                "path": path,
                "term_map": _label_job_term_map(value),
                "humanize_iri_constants": bool(token_spec.get("humanize_iri_constants", token_spec.get("humanize", True))),
            }
        return {"kind": "literal", "path": path, "value": str(value) if value is not None else None}

    if "from_instance" in token_spec:
        path = str(token_spec["from_instance"])
        value = _maybe_instance_value(inst, path)
        if value is None:
            value = token_spec.get("default")
        return {"kind": "instance_value", "path": path, "value": str(value) if value is not None else None}

    if "from_label_token" in token_spec:
        key = str(token_spec["from_label_token"])
        value = _instance_label_token(inst, key)
        if value is None:
            value = token_spec.get("default")
        return {"kind": "instance_label_token", "key": key, "value": str(value) if value is not None else None}

    if "from_time_field_predicate" in token_spec:
        predicate = str(token_spec["from_time_field_predicate"])
        value = _find_time_field_object_map(resolved, predicate, prefixes)
        return {"kind": "time_field", "predicate": predicate, "term_map": _label_job_term_map(value)}

    if "from_semantic_unit_label" in token_spec:
        path = str(token_spec["from_semantic_unit_label"])
        value = _maybe_resolved_value_for_path({"resolved": resolved}, path)
        return {
            "kind": "semantic_unit_label",
            "path": path,
            "semantic_unit_map": _label_job_term_map(value, default_term_type="iri"),
            "requires_existing_label": True,
        }

    if "from_association_member_labels" in token_spec:
        path = str(token_spec["from_association_member_labels"])
        value = _maybe_resolved_value_for_path({"resolved": resolved}, path)
        members = value if isinstance(value, list) else []
        return {
            "kind": "association_member_labels",
            "path": path,
            "members": [_label_job_term_map(member, default_term_type="iri") for member in members],
            "requires_existing_labels": True,
        }

    if "constant" in token_spec:
        return {"kind": "literal", "value": str(token_spec["constant"])}
    if "literal" in token_spec:
        return {"kind": "literal", "value": str(token_spec["literal"])}
    if token_spec.get("default") is not None:
        return {"kind": "literal", "value": str(token_spec["default"])}

    return {"kind": "unresolved", "reason": "unsupported joined label token spec", "spec": copy.deepcopy(token_spec)}


def compile_joined_label_job(
    resolved_instance: Dict[str, Any],
    inst: Dict[str, Any],
    family: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    spec = family.get("joined_label_map")
    if spec is None or spec is False:
        return None
    if not isinstance(spec, dict):
        raise ConfigError(f"Family {family.get('id')} joined_label_map must be a mapping")

    # Explicit user labels are authoritative; do not also plan a joined family label.
    if _extract_label_map_config(inst) is not None:
        return None

    target = spec.get("target", "semantic_unit")
    if target != "semantic_unit":
        raise ConfigError(f"Family {family.get('id')} joined_label_map currently only supports target: semantic_unit")

    resolved = resolved_instance.get("resolved", {})
    subject_map = _maybe_resolved_value_for_path(resolved_instance, "semantic_unit.subject_map")
    if subject_map is None:
        return None

    token_specs = spec.get("tokens") or {}
    if not isinstance(token_specs, dict):
        raise ConfigError(f"Family {family.get('id')} joined_label_map.tokens must be a mapping")

    tokens_in_templates: List[str] = []
    for key in ("template", "fallback_template"):
        if isinstance(spec.get(key), str):
            tokens_in_templates.extend(_template_tokens(spec[key]))
    for case in spec.get("cases") or []:
        if isinstance(case, dict) and isinstance(case.get("template"), str):
            tokens_in_templates.extend(_template_tokens(case["template"]))
    token_names = list(dict.fromkeys(tokens_in_templates + list(token_specs.keys())))

    tokens: Dict[str, Any] = {}
    for token_name in token_names:
        tokens[token_name] = _compile_joined_label_token(
            token_name,
            token_specs.get(token_name),
            resolved_instance["id"],
            inst,
            resolved,
            prefixes,
            entity_registry,
        )

    unresolved = [name for name, tok in tokens.items() if tok.get("kind") == "unresolved" or tok.get("status") in {"unresolved", "needs_join_information"}]
    requires_join = any(bool(tok.get("requires_join")) for tok in tokens.values())
    requires_existing_labels = any(bool(tok.get("requires_existing_label") or tok.get("requires_existing_labels")) for tok in tokens.values())

    return {
        "id": f"{resolved_instance['id']}__joined_label",
        "instance_id": resolved_instance["id"],
        "family": resolved_instance["family"],
        "mode": spec.get("mode", "joined_table"),
        "target": target,
        "source": resolved_instance.get("source"),
        "subject_map": _label_job_term_map(subject_map, default_term_type="iri"),
        "predicate_map": {"kind": "constant", "term_type": "iri", "constant": "rdfs:label"},
        "template": spec.get("template"),
        **({"fallback_template": spec.get("fallback_template")} if spec.get("fallback_template") is not None else {}),
        **({"cases": copy.deepcopy(spec.get("cases"))} if spec.get("cases") is not None else {}),
        "tokens": tokens,
        "requires_join": requires_join,
        "requires_existing_labels": requires_existing_labels,
        "unresolved_tokens": unresolved,
    }


def compile_basic_label_lookup_job(resolved_instance: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Create a lookup-only label job for labels already emitted by the main RML mapping.

    Basic/default/user labels are already attached to semantic-unit resources in the
    normal TriplesMaps. For second-order joined labels (complex/compound), the
    table builder also needs those labels as a pre-materialization lookup. These
    jobs build intermediate label tables but are marked materialize=false so
    append_label_triplesmaps.py does not add duplicate rdfs:label mappings.
    """
    if resolved_instance.get("family") == "entity":
        return None
    resolved = resolved_instance.get("resolved", {})
    subject_map = resolved.get("semantic_unit_map")
    label_map = resolved.get("label_map")
    if not subject_map or not label_map:
        return None
    return {
        "id": f"{resolved_instance['id']}__basic_label_lookup",
        "instance_id": resolved_instance["id"],
        "family": resolved_instance["family"],
        "mode": "basic_table",
        "target": "semantic_unit",
        "source": resolved_instance.get("source"),
        "subject_map": _label_job_term_map(subject_map, default_term_type="iri"),
        "predicate_map": {"kind": "constant", "term_type": "iri", "constant": "rdfs:label"},
        "label_map": _label_job_term_map(label_map, default_term_type="literal"),
        "materialize": False,
        "lookup_only": True,
    }


def collect_basic_label_lookup_jobs(resolved_instances: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    for resolved in resolved_instances:
        job = compile_basic_label_lookup_job(resolved)
        if job is not None:
            jobs.append(job)
    return jobs


def collect_joined_label_jobs(
    resolved_instances: List[Dict[str, Any]],
    instances: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    for resolved in resolved_instances:
        family_id = resolved.get("family")
        if family_id == "entity":
            continue
        family = families.get(family_id) or {}
        if not family.get("joined_label_map"):
            continue
        inst = _original_instance_for(resolved, instances)
        if inst is None:
            continue
        job = compile_joined_label_job(resolved, inst, family, prefixes, entity_registry)
        if job is not None:
            # Joined labels produce actual label triples unless explicitly disabled.
            job.setdefault("materialize", True)
            jobs.append(job)
    return jobs


def collect_label_jobs(
    resolved_instances: List[Dict[str, Any]],
    instances: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    # Lookup jobs come first so second-order label jobs can resolve basic labels
    # during the iterative label-table build step.
    return collect_basic_label_lookup_jobs(resolved_instances) + collect_joined_label_jobs(
        resolved_instances,
        instances,
        families,
        prefixes,
        entity_registry,
    )


def resolve_declarative_time_fields(
    instance_id: str,
    inst: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> List[Dict[str, Any]]:
    return resolve_time_fields(instance_id, inst, prefixes, entity_registry, tm_name_lookup)


def resolve_declarative_association_members(
    instance_id: str,
    inst: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
    semantic_unit_registry: Dict[str, Dict[str, Any]],
) -> List[TermMapSpec]:
    return resolve_compound_members(instance_id, inst, prefixes, entity_registry, tm_name_lookup, semantic_unit_registry)


def _resolved_context_lookup(r: Dict[str, Any], path: str) -> Any:
    return _path_lookup(r, path)


def resolve_declarative_value(
    instance_id: str,
    inst: Dict[str, Any],
    family: Dict[str, Any],
    key: str,
    spec: Dict[str, Any],
    r: Dict[str, Any],
    prefixes: Dict[str, str],
) -> Any:
    kind = spec.get('kind', 'scalar')

    raw = _maybe_instance_value(inst, spec.get('path', key))
    if raw is None and spec.get('family_path'):
        raw = _maybe_family_value(family, spec.get('family_path'))
    if raw is None and spec.get('default_family_path'):
        raw = _maybe_family_value(family, spec.get('default_family_path'))
    if raw is None and 'default' in spec:
        raw = spec['default']

    if kind == 'bool_exists':
        exists_path = spec.get('exists')
        if not exists_path:
            raise ConfigError(f"Declarative value {key} in {instance_id} kind=bool_exists needs exists")
        try:
            return _resolved_context_lookup(r, str(exists_path)) is not None
        except KeyError:
            return False

    if raw is None and spec.get('default_exists'):
        try:
            raw = _resolved_context_lookup(r, str(spec['default_exists'])) is not None
        except KeyError:
            raw = False

    if raw is None and spec.get('required'):
        raise ConfigError(f"{inst.get('family')} {instance_id} missing value {key}")

    if kind == 'bool':
        return bool(raw)
    if kind == 'iri':
        return curie_to_full(str(raw), prefixes) if raw is not None else None
    return raw


def compile_declarative_resolution(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
    semantic_unit_registry: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    family_id = inst.get('family')
    family = families.get(family_id)
    if family is None:
        raise ConfigError(f"No family spec loaded for {family_id} ({instance_id})")
    resolution = family.get('resolution')
    if not isinstance(resolution, dict):
        raise ConfigError(f"Family {family_id} has no declarative resolution section")

    r: Dict[str, Any] = {}
    resolved_order = resolution.get('resolved_order') or []

    if resolution.get('statement_unit_type', True):
        statement_unit_type = statement_unit_type_from_instance(instance_id, inst, prefixes)
        r['statement_unit_type'] = statement_unit_type
        r['statement_unit_type_local'] = statement_unit_type.rstrip('/#').rsplit('/', 1)[-1]

    if resolution.get('semantic_unit', True):
        r['semantic_unit_map'] = asdict(resolve_semantic_unit_map(
            instance_id,
            inst,
            family,
            prefixes,
            tm_name_lookup,
            family_label=family_id,
        ))

    role_specs = resolution.get('roles', {}) or {}
    roles_out: Dict[str, Any] = {}
    for role_name, role_spec in role_specs.items():
        if role_spec is None:
            role_spec = {}
        if not isinstance(role_spec, dict):
            raise ConfigError(f"Family {family_id} resolution.roles.{role_name} must be a mapping")
        term = resolve_declarative_role(
            instance_id,
            inst,
            role_name,
            role_spec,
            prefixes,
            entity_registry,
            tm_name_lookup,
        )
        if term is not None:
            roles_out[role_name] = asdict(term)
    if role_specs:
        r['roles'] = roles_out

    resource_specs = resolution.get('resources', {}) or {}
    resources_out: Dict[str, Any] = {}
    for resource_name, resource_spec in resource_specs.items():
        if resource_spec is None:
            resource_spec = {}
        if not isinstance(resource_spec, dict):
            raise ConfigError(f"Family {family_id} resolution.resources.{resource_name} must be a mapping")
        term = resolve_declarative_resource(
            instance_id,
            inst,
            resource_name,
            resource_spec,
            prefixes,
            tm_name_lookup,
        )
        if term is not None:
            resources_out[resource_name] = asdict(term)
    if resource_specs:
        r['resources'] = resources_out

    collection_specs = resolution.get('collections', {}) or {}
    for name, collection_spec in collection_specs.items():
        if not isinstance(collection_spec, dict):
            raise ConfigError(f"Family {family_id} resolution.collections.{name} must be a mapping")
        builder = collection_spec.get('builder')
        if builder == 'time_fields':
            r[name] = resolve_declarative_time_fields(instance_id, inst, prefixes, entity_registry, tm_name_lookup)
        elif builder == 'association_members':
            r[name] = [asdict(m) for m in resolve_declarative_association_members(
                instance_id,
                inst,
                prefixes,
                entity_registry,
                tm_name_lookup,
                semantic_unit_registry,
            )]
        else:
            raise ConfigError(f"Unsupported declarative collection builder {builder!r} in {family_id}.{name}")

    value_specs = resolution.get('values', {}) or {}
    for key, value_spec in value_specs.items():
        if value_spec is None:
            value_spec = {}
        if not isinstance(value_spec, dict):
            raise ConfigError(f"Family {family_id} resolution.values.{key} must be a mapping")
        r[key] = resolve_declarative_value(instance_id, inst, family, key, value_spec, r, prefixes)

    label_map = resolve_declarative_label_map(instance_id, inst, family, r, prefixes, tm_name_lookup, entity_registry)
    if label_map is not None:
        r['label_map'] = asdict(label_map)

    if resolved_order:
        ordered: Dict[str, Any] = {}
        for key in resolved_order:
            if key in r:
                ordered[key] = r[key]
        for key, value in r.items():
            if key not in ordered:
                ordered[key] = value
        r = ordered

    return {
        'id': instance_id,
        'family': family_id,
        'source': inst['source'],
        'execution_layer': family.get('execution_layer', 100),
        'resolved': r,
    }

# ============================================================
# Bundle and TriplesMap lowering
# ============================================================





# ============================================================
# Declarative bundle-spec lowering
# ============================================================

PREDICATE_EXPAND_PREFIXES = {"semunit"}


def _path_lookup(root: Any, path: str) -> Any:
    current = root
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(path)
    return current


def _resolved_value_for_path(resolved: Dict[str, Any], path: str, item: Any = None) -> Any:
    if isinstance(path, str) and path.startswith("$item"):
        if item is None:
            raise KeyError(path)
        if path == "$item":
            return item
        if path.startswith("$item."):
            return _path_lookup(item, path[len("$item."):])
        raise KeyError(path)

    r = resolved["resolved"]

    aliases = {
        "entity.subject_map": "subject_map",
        "entity.label_map": "label_map",
        "entity.description_map": "description_map",
        "entity.class": "class_iri",
        "entity.class_mapping": "class_iri",
        "semantic_unit": "semantic_unit_map",
        "semantic_unit.subject_map": "semantic_unit_map",
        "semantic_unit.term_map": "semantic_unit_map",
        "semantic_unit.label_map": "label_map",
        "semantic_unit_label_map": "label_map",
        "statement_unit_type": "statement_unit_type",
    }
    lookup_path = aliases.get(path, path)

    if lookup_path.startswith("roles."):
        return _path_lookup(r, lookup_path)
    if lookup_path.startswith("resources."):
        return _path_lookup(r, lookup_path)
    return _path_lookup(r, lookup_path)


def _maybe_resolved_value_for_path(resolved: Dict[str, Any], path: str, item: Any = None) -> Any:
    try:
        return _resolved_value_for_path(resolved, path, item=item)
    except KeyError:
        return None


def _is_term_map_dict(value: Any) -> bool:
    return isinstance(value, dict) and (
        "kind" in value
        or any(k in value for k in ("constant", "reference", "template", "parent", "parent_triples_map", "parentTriplesMap"))
    )


def _term_from_resolved_value(value: Any, *, default_term_type: str = "iri") -> Optional[TermMapSpec]:
    if value is None:
        return None
    if isinstance(value, TermMapSpec):
        return value
    if _is_term_map_dict(value):
        # Resolved term maps already have expanded constants/templates; do not run
        # term_map_from_mapping again because that could rewrite parent ids or templates.
        if "kind" in value:
            return TermMapSpec(**value)
        term_type = normalize_term_type(value.get("term_type") or value.get("termType"), default_term_type)
        if "reference" in value:
            return TermMapSpec(kind="reference", reference=str(value["reference"]), term_type=term_type, datatype=value.get("datatype"), language=value.get("language"), language_reference=value.get("language_reference"), language_template=value.get("language_template"))
        if "template" in value:
            return TermMapSpec(kind="template", template=str(value["template"]), term_type=term_type, datatype=value.get("datatype"), language=value.get("language"), language_reference=value.get("language_reference"), language_template=value.get("language_template"))
        if "constant" in value:
            return TermMapSpec(kind="constant", constant=str(value["constant"]), term_type=term_type, datatype=value.get("datatype"), language=value.get("language"), language_reference=value.get("language_reference"), language_template=value.get("language_template"))
        parent_value = value.get("parent_triples_map") or value.get("parentTriplesMap") or value.get("parent")
        if parent_value:
            return TermMapSpec(kind="parent", parent_triples_map=str(parent_value), term_type="iri", join_conditions=value.get("join_conditions") or value.get("join"))
    if isinstance(value, dict):
        if "subject_map" in value:
            return _term_from_resolved_value(value["subject_map"], default_term_type=default_term_type)
        if "term_map" in value:
            return _term_from_resolved_value(value["term_map"], default_term_type=default_term_type)
    raise ConfigError(f"Expected resolved term map, got {value!r}")


def _constant_term_from_scalar(
    value: Any,
    prefixes: Dict[str, str],
    *,
    term_type: str = "iri",
    expand_predicate: bool = False,
) -> TermMapSpec:
    if term_type == "iri":
        raw = str(value)
        if expand_predicate:
            prefix = raw.split(":", 1)[0] if ":" in raw else ""
            constant = curie_to_full(raw, prefixes) if prefix in PREDICATE_EXPAND_PREFIXES else raw
        else:
            constant = curie_to_full(raw, prefixes)
        return TermMapSpec(kind="constant", constant=constant, term_type="iri")
    if value is True:
        literal_value = "true"
    elif value is False:
        literal_value = "false"
    else:
        literal_value = str(value)
    return TermMapSpec(kind="constant", constant=literal_value, term_type=term_type)


def _resolve_term_reference_or_shape(
    spec: Any,
    resolved: Dict[str, Any],
    prefixes: Dict[str, str],
    tm_name_lookup: Dict[str, str],
    *,
    default_term_type: str = "iri",
    when_present: bool = False,
    expand_predicate: bool = False,
    item: Any = None,
) -> Optional[TermMapSpec]:
    if spec is None or spec == "default":
        return None

    if isinstance(spec, str):
        value = _maybe_resolved_value_for_path(resolved, spec, item=item)
        if value is None:
            if when_present:
                return None
            # A bare string in a term-map slot is treated as a constant IRI if it is
            # not a known resolved path. This keeps predicate/constant shorthand usable.
            return _constant_term_from_scalar(
                spec,
                prefixes,
                term_type=default_term_type,
                expand_predicate=expand_predicate,
            )
        if isinstance(value, str):
            return _constant_term_from_scalar(
                value,
                prefixes,
                term_type=default_term_type,
                expand_predicate=expand_predicate,
            )
        return _term_from_resolved_value(value, default_term_type=default_term_type)

    if isinstance(spec, dict):
        if "predicate_map" in spec:
            return _resolve_term_reference_or_shape(
                spec["predicate_map"],
                resolved,
                prefixes,
                tm_name_lookup,
                default_term_type="iri",
                when_present=when_present,
                expand_predicate=True,
                item=item,
            )
        if "object_map" in spec:
            return _resolve_term_reference_or_shape(
                spec["object_map"],
                resolved,
                prefixes,
                tm_name_lookup,
                default_term_type=default_term_type,
                when_present=when_present,
                item=item,
            )
        if "subject_map" in spec:
            return _resolve_term_reference_or_shape(
                spec["subject_map"],
                resolved,
                prefixes,
                tm_name_lookup,
                default_term_type=default_term_type,
                when_present=when_present,
                item=item,
            )
        if "term_map" in spec:
            return _resolve_term_reference_or_shape(
                spec["term_map"],
                resolved,
                prefixes,
                tm_name_lookup,
                default_term_type=default_term_type,
                when_present=when_present,
                item=item,
            )

        # For {constant: some.path}, first try to resolve some.path as a scalar
        # or term map token. If it is not resolvable, treat it as a literal RML constant.
        if "constant" in spec:
            raw_constant = spec["constant"]
            term_type = normalize_term_type(spec.get("term_type") or spec.get("termType"), default_term_type)
            if isinstance(raw_constant, str):
                value = _maybe_resolved_value_for_path(resolved, raw_constant, item=item)
                if value is not None:
                    if isinstance(value, str):
                        return _constant_term_from_scalar(
                            value,
                            prefixes,
                            term_type=term_type,
                            expand_predicate=expand_predicate,
                        )
                    return _term_from_resolved_value(value, default_term_type=default_term_type)
                if spec.get("preserve_curie") or spec.get("expand") is False:
                    return TermMapSpec(kind="constant", constant=raw_constant, term_type=term_type)
                return _constant_term_from_scalar(
                    raw_constant,
                    prefixes,
                    term_type=term_type,
                    expand_predicate=expand_predicate,
                )
            return term_map_from_mapping(
                spec,
                prefixes,
                default_term_type=default_term_type,
                tm_name_lookup=tm_name_lookup,
            )

        if any(k in spec for k in ("reference", "template", "parent", "parent_triples_map", "parentTriplesMap")):
            return term_map_from_mapping(
                spec,
                prefixes,
                default_term_type=default_term_type,
                tm_name_lookup=tm_name_lookup,
            )

    raise ConfigError(f"Cannot resolve declarative term-map spec: {spec!r}")


def _normalize_bundle_specs(family: Dict[str, Any]) -> List[Dict[str, Any]]:
    specs = family.get("emit_bundles") or family.get("bundles") or []
    if not isinstance(specs, list):
        raise ConfigError(f"Family {family.get('id')} emit_bundles must be a list")
    return specs


def _bundle_subject_spec(bundle_spec: Dict[str, Any]) -> Any:
    if "subject_map" in bundle_spec:
        return bundle_spec["subject_map"]
    if "subject" in bundle_spec:
        value = bundle_spec["subject"]
        if value == "semantic_unit":
            return "semantic_unit.subject_map"
        return value
    raise ConfigError(f"Bundle {bundle_spec.get('id')} needs subject_map/subject")


def _bundle_graph_spec(bundle_spec: Dict[str, Any]) -> Any:
    if "graph_map" in bundle_spec:
        return bundle_spec["graph_map"]
    if "graph" in bundle_spec:
        value = bundle_spec["graph"]
        if value == "semantic_unit":
            return "semantic_unit.subject_map"
        if value == "default":
            return "default"
        return value
    return "default"


def _bundle_po_specs(bundle_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "predicate_object_maps" in bundle_spec:
        raw = bundle_spec["predicate_object_maps"] or []
        if not isinstance(raw, list):
            raise ConfigError(f"Bundle {bundle_spec.get('id')} predicate_object_maps must be a list")
        return raw

    # Legacy family spelling used by some current family specs.
    triples = bundle_spec.get("triples") or []
    if not isinstance(triples, list):
        raise ConfigError(f"Bundle {bundle_spec.get('id')} triples must be a list")
    out: List[Dict[str, Any]] = []
    for triple in triples:
        if not isinstance(triple, dict):
            raise ConfigError(f"Triple in bundle {bundle_spec.get('id')} must be a mapping")
        out.append(
            {
                "predicate_map": {"constant": triple["predicate"]},
                "object_map": triple.get("object_from"),
                "when_present": bool(triple.get("when_present", False)),
            }
        )
    return out


def _condition_value(value: Any, resolved: Dict[str, Any], prefixes: Dict[str, str], item: Any = None) -> Any:
    if isinstance(value, str):
        resolved_value = _maybe_resolved_value_for_path(resolved, value, item=item)
        if resolved_value is not None:
            return resolved_value
        return curie_to_full(value, prefixes)
    return value


def _condition_matches(condition: Any, resolved: Dict[str, Any], prefixes: Dict[str, str], item: Any = None) -> bool:
    """Evaluate small declarative conditions used by family bundle specs."""
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    if isinstance(condition, str):
        return bool(_maybe_resolved_value_for_path(resolved, condition, item=item))
    if not isinstance(condition, dict):
        raise ConfigError(f"Unsupported declarative condition: {condition!r}")

    if "all" in condition:
        values = condition["all"]
        if not isinstance(values, list):
            raise ConfigError("Condition all must be a list")
        return all(_condition_matches(v, resolved, prefixes, item=item) for v in values)
    if "any" in condition:
        values = condition["any"]
        if not isinstance(values, list):
            raise ConfigError("Condition any must be a list")
        return any(_condition_matches(v, resolved, prefixes, item=item) for v in values)
    if "not" in condition:
        return not _condition_matches(condition["not"], resolved, prefixes, item=item)
    if "exists" in condition:
        return _maybe_resolved_value_for_path(resolved, str(condition["exists"]), item=item) is not None
    if "equals" in condition:
        values = condition["equals"]
        if not isinstance(values, list) or len(values) != 2:
            raise ConfigError("Condition equals must be a two-item list")
        left = _condition_value(values[0], resolved, prefixes, item=item)
        right = _condition_value(values[1], resolved, prefixes, item=item)
        return left == right
    if "not_equals" in condition or "not_equal" in condition:
        values = condition.get("not_equals", condition.get("not_equal"))
        if not isinstance(values, list) or len(values) != 2:
            raise ConfigError("Condition not_equals must be a two-item list")
        left = _condition_value(values[0], resolved, prefixes, item=item)
        right = _condition_value(values[1], resolved, prefixes, item=item)
        return left != right

    raise ConfigError(f"Unsupported declarative condition: {condition!r}")


def _expand_po_specs(
    raw_po_specs: List[Dict[str, Any]],
    resolved: Dict[str, Any],
    prefixes: Dict[str, str],
) -> List[tuple[Dict[str, Any], Any]]:
    """Expand predicate-object specs, supporting simple for_each loops."""
    expanded: List[tuple[Dict[str, Any], Any]] = []
    for po_spec in raw_po_specs:
        if not isinstance(po_spec, dict):
            raise ConfigError("predicate_object_maps entries must be mappings")
        if "for_each" not in po_spec:
            expanded.append((po_spec, None))
            continue

        collection_path = str(po_spec["for_each"])
        collection = _maybe_resolved_value_for_path(resolved, collection_path)
        if collection is None:
            if po_spec.get("optional") or po_spec.get("when_present"):
                continue
            raise ConfigError(f"for_each path {collection_path!r} did not resolve")
        if not isinstance(collection, list):
            raise ConfigError(f"for_each path {collection_path!r} must resolve to a list")

        template = {k: v for k, v in po_spec.items() if k not in {"for_each", "optional"}}
        for item in collection:
            expanded.append((template, item))
    return expanded




def _bundle_is_semantic_unit_resource(bundle_spec: Dict[str, Any]) -> bool:
    return (
        bundle_spec.get("id") == "semantic_unit_resource"
        or bundle_spec.get("kind") == "semantic_unit_resource"
    )


def _predicate_constant_value(pom: PredicateObjectSpec) -> Optional[str]:
    if pom.predicate_map.kind == "constant":
        return pom.predicate_map.constant
    return None


def _has_rdfs_label_pom(poms: List[PredicateObjectSpec]) -> bool:
    return any(
        value in {"rdfs:label", "http://www.w3.org/2000/01/rdf-schema#label"}
        for value in (_predicate_constant_value(pom) for pom in poms)
    )


def _append_semantic_unit_label_pom_if_requested(
    bundle_spec: Dict[str, Any],
    resolved: Dict[str, Any],
    poms: List[PredicateObjectSpec],
) -> None:
    """Append rdfs:label to semantic-unit resource bundles for user label_map.

    This implements instance-level basic labels for all non-entity families without
    requiring every family file to repeat an optional rdfs:label POM.
    """
    if not _bundle_is_semantic_unit_resource(bundle_spec):
        return
    label_map = _maybe_resolved_value_for_path(resolved, "label_map")
    if label_map is None or _has_rdfs_label_pom(poms):
        return
    term = _term_from_resolved_value(label_map, default_term_type="literal")
    if term is None:
        return
    poms.append(
        PredicateObjectSpec(
            predicate_map=TermMapSpec(kind="constant", constant="rdfs:label", term_type="iri"),
            object_map=term,
            when_present=True,
        )
    )

def compile_bundles_from_family_spec(
    resolved: Dict[str, Any],
    family: Dict[str, Any],
    prefixes: Dict[str, str],
    tm_name_lookup: Dict[str, str],
) -> List[Bundle]:
    bundle_specs = _normalize_bundle_specs(family)
    if not bundle_specs:
        raise ConfigError(f"Family {family.get('id')} has no declarative emit_bundles/bundles section")

    bundles: List[Bundle] = []
    for bundle_spec in bundle_specs:
        if not isinstance(bundle_spec, dict):
            raise ConfigError(f"Family {family.get('id')} bundle spec must be a mapping")
        bundle_id = bundle_spec.get("id")
        if not bundle_id:
            raise ConfigError(f"Family {family.get('id')} bundle missing id")

        bundle_condition = bundle_spec.get("when")
        if bundle_condition is None and "when_exists" in bundle_spec:
            bundle_condition = {"exists": bundle_spec["when_exists"]}
        if bundle_condition is None and "when_not_equals" in bundle_spec:
            bundle_condition = {"not_equals": bundle_spec["when_not_equals"]}
        if bundle_condition is None and "when_not_equal" in bundle_spec:
            bundle_condition = {"not_equals": bundle_spec["when_not_equal"]}
        if bundle_condition is not None and not _condition_matches(bundle_condition, resolved, prefixes):
            continue

        subject_map = _resolve_term_reference_or_shape(
            _bundle_subject_spec(bundle_spec),
            resolved,
            prefixes,
            tm_name_lookup,
            default_term_type="iri",
        )
        if subject_map is None:
            raise ConfigError(f"Bundle {bundle_id} subject map resolved to None")

        graph_spec = _bundle_graph_spec(bundle_spec)
        graph_map = None if graph_spec == "default" else _resolve_term_reference_or_shape(
            graph_spec,
            resolved,
            prefixes,
            tm_name_lookup,
            default_term_type="iri",
        )

        poms: List[PredicateObjectSpec] = []
        for po_spec, item in _expand_po_specs(_bundle_po_specs(bundle_spec), resolved, prefixes):
            po_condition = po_spec.get("when")
            if po_condition is None and "when_exists" in po_spec:
                po_condition = {"exists": po_spec["when_exists"]}
            if po_condition is None and "when_not_equals" in po_spec:
                po_condition = {"not_equals": po_spec["when_not_equals"]}
            if po_condition is not None and not _condition_matches(po_condition, resolved, prefixes, item=item):
                continue

            when_present = bool(po_spec.get("when_present", False))
            predicate_map = _resolve_term_reference_or_shape(
                po_spec.get("predicate_map"),
                resolved,
                prefixes,
                tm_name_lookup,
                default_term_type="iri",
                when_present=when_present,
                expand_predicate=True,
                item=item,
            )
            object_map = _resolve_term_reference_or_shape(
                po_spec.get("object_map"),
                resolved,
                prefixes,
                tm_name_lookup,
                default_term_type="iri",
                when_present=when_present,
                item=item,
            )
            if predicate_map is None or object_map is None:
                if when_present:
                    continue
                raise ConfigError(f"Bundle {bundle_id} predicate/object map resolved to None")
            poms.append(PredicateObjectSpec(predicate_map=predicate_map, object_map=object_map, when_present=when_present))

        _append_semantic_unit_label_pom_if_requested(bundle_spec, resolved, poms)

        bundle_suffix = "semantic_unit" if bundle_id == "semantic_unit_resource" else bundle_id
        bundles.append(
            Bundle(
                id=f"{resolved['id']}__{bundle_suffix}",
                instance_id=resolved["id"],
                family=resolved["family"],
                kind=bundle_spec.get("kind", bundle_id),
                source=resolved["source"],
                subject_map=subject_map,
                graph_map=graph_map,
                predicate_object_maps=poms,
            )
        )

    return bundles










def _constant_po(predicate: str, obj: str, *, obj_term_type: str = "iri") -> PredicateObjectSpec:
    return PredicateObjectSpec(
        predicate_map=TermMapSpec(kind="constant", constant=predicate, term_type="iri"),
        object_map=TermMapSpec(kind="constant", constant=obj, term_type=obj_term_type),
    )


def _po(predicate: str, object_map: TermMapSpec) -> PredicateObjectSpec:
    return PredicateObjectSpec(
        predicate_map=TermMapSpec(kind="constant", constant=predicate, term_type="iri"),
        object_map=object_map,
    )























def predicate_map_to_tm_field(term: TermMapSpec) -> Dict[str, Any]:
    if term.kind != "constant" or not term.constant:
        raise ConfigError("Only constant predicate maps are currently supported in the RML renderer")
    return {"predicate_constant": term.constant}


def bundle_to_tm(bundle: Bundle, tm_name_lookup: Dict[str, str]) -> Dict[str, Any]:
    subject_map = term_map_to_tm_dict(bundle.subject_map)
    if bundle.graph_map:
        graph = term_map_to_tm_dict(bundle.graph_map)
        # Keep graph maps separate from the subject term map while preserving the compact
        # TriplesMap IR expected by the renderer.
        if graph.get("constant") is not None:
            subject_map["graph_constant"] = graph["constant"]
        elif graph.get("template") is not None:
            subject_map["graph_template"] = graph["template"]
        elif graph.get("reference") is not None:
            subject_map["graph_reference"] = graph["reference"]
        else:
            raise ConfigError(f"Graph map must resolve to constant/template/reference: {graph}")

    poms = []
    for pom in bundle.predicate_object_maps:
        predicate = predicate_map_to_tm_field(pom.predicate_map)
        poms.append({**predicate, "object_map": term_map_to_tm_dict(pom.object_map)})

    return {
        "id": tm_name_lookup[bundle.instance_id] if bundle.kind == "base_entity" else f"TM_{bundle.id}",
        "logical_source": bundle.source,
        "subject_map": subject_map,
        "predicate_object_maps": poms,
    }


# ============================================================
# Top-level compiler
# ============================================================


def build_family_registry(family_docs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    families: Dict[str, Dict[str, Any]] = {}
    for idx, doc in enumerate(family_docs, start=1):
        if not isinstance(doc, dict):
            raise ConfigError(f"Family document #{idx} must be a mapping")
        family_id = doc.get("id")
        if not family_id:
            raise ConfigError(f"Family document #{idx} is missing top-level id")
        if family_id in families:
            raise ConfigError(f"Duplicate family id loaded: {family_id}")
        families[family_id] = doc
    return families


def resolve_profile_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
    semantic_unit_registry: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Resolve one non-entity family instance using its declarative family spec.

    Entity instances remain the only bootstrap special case because they build the
    entity registry used by all other role resolvers. All other families must
    provide a `resolution:` section in their loaded *.family.rml.yaml file.
    """
    family_id = inst.get("family")
    if family_id == "entity":
        return None

    family = families.get(family_id)
    if family is None:
        raise ConfigError(f"No family spec loaded for {family_id} ({instance_id})")
    if not isinstance(family.get("resolution"), dict):
        raise ConfigError(
            f"Family {family_id} has no declarative resolution section. "
            "Legacy Python fallback resolvers were removed in Pass 6."
        )

    return compile_declarative_resolution(
        instance_id,
        inst,
        families,
        prefixes,
        entity_registry,
        tm_name_lookup,
        semantic_unit_registry,
    )

def remember_semantic_unit(
    semantic_unit_registry: Dict[str, Dict[str, Any]],
    resolved: Dict[str, Any],
) -> None:
    sem_map = resolved.get("resolved", {}).get("semantic_unit_map")
    if sem_map:
        semantic_unit_registry[resolved["id"]] = sem_map


def bundles_from_resolved_instance(
    resolved: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    tm_name_lookup: Dict[str, str],
) -> List[Bundle]:
    """Compile resolved instance into bundles using only declarative emit_bundles."""
    family_id = resolved["family"]
    family = families.get(family_id)
    if family is None:
        raise ConfigError(f"No loaded family spec for family: {family_id}")
    if not isinstance(family.get("emit_bundles"), list):
        raise ConfigError(
            f"Family {family_id} has no declarative emit_bundles section. "
            "Legacy Python fallback bundlers were removed in Pass 6."
        )
    return compile_bundles_from_family_spec(resolved, family, prefixes, tm_name_lookup)

def compile_config(config: Dict[str, Any], family_docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    prefixes = config.get("prefixes", {}) or {}
    raw_sources = config.get("sources", {}) or {}
    instances = config.get("instances", {}) or {}
    families = build_family_registry(family_docs)

    tm_name_lookup = {
        instance_id: tm_id_for_instance(instance_id)
        for instance_id, inst in instances.items()
        if inst.get("family") == "entity"
    }

    resolved_entities: List[Dict[str, Any]] = []
    for instance_id, inst in instances.items():
        if inst.get("family") == "entity":
            resolved_entities.append(resolve_entity_instance(instance_id, inst, families, prefixes, tm_name_lookup))

    entity_registry = build_entity_registry(resolved_entities)

    resolved_profiles: List[Dict[str, Any]] = []
    semantic_unit_registry: Dict[str, Dict[str, Any]] = {}

    for instance_id, inst in instances.items():
        resolved_profile = resolve_profile_instance(
            instance_id,
            inst,
            families,
            prefixes,
            entity_registry,
            tm_name_lookup,
            semantic_unit_registry,
        )
        if resolved_profile is not None:
            resolved_profiles.append(resolved_profile)
            remember_semantic_unit(semantic_unit_registry, resolved_profile)

    resolved_instances = sorted(resolved_entities + resolved_profiles, key=lambda x: (x["execution_layer"], x["id"]))

    bundles: List[Bundle] = []
    for resolved in resolved_instances:
        bundles.extend(bundles_from_resolved_instance(resolved, families, prefixes, tm_name_lookup))

    triples_maps = [bundle_to_tm(bundle, tm_name_lookup) for bundle in bundles]
    label_jobs = collect_label_jobs(
        resolved_instances,
        instances,
        families,
        prefixes,
        entity_registry,
    )

    return {
        "prefixes": prefixes,
        "sources": normalize_sources(raw_sources),
        "instances": resolved_instances,
        "entity_registry": entity_registry,
        "bundles": [asdict(b) for b in bundles],
        "triples_maps": triples_maps,
        "label_jobs": label_jobs,
    }


def load_family_docs(paths: List[Path], families_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    family_paths: List[Path] = []

    if families_dir is not None:
        if not families_dir.exists():
            raise ConfigError(f"Families directory not found: {families_dir}")
        if not families_dir.is_dir():
            raise ConfigError(f"--families-dir must point to a directory: {families_dir}")
        family_paths.extend(sorted(families_dir.glob("*.family.rml.yaml")))

    family_paths.extend(paths)

    if not family_paths:
        raise ConfigError("No family files provided. Pass explicit family YAML files or use --families-dir.")

    docs: List[Dict[str, Any]] = []
    seen_files: set[Path] = set()
    for path in family_paths:
        path = Path(path)
        if path in seen_files:
            continue
        seen_files.add(path)
        docs.append(load_yaml(path))
    return docs


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize RML-style family instances into bundle and TriplesMap IR."
    )
    parser.add_argument("instances", help="Path to rml_instances YAML")
    parser.add_argument(
        "families",
        nargs="*",
        help="Optional explicit family YAML files. Kept for backwards compatibility.",
    )
    parser.add_argument(
        "--families-dir",
        help="Directory containing *.family.rml.yaml files",
    )
    parser.add_argument(
        "--out-dir",
        default="./out",
        help="Output directory for normalized_instances.yaml, bundle_ir.yaml, and triplesmap_ir.yaml",
    )
    parser.add_argument(
        "--skip-family-validation",
        action="store_true",
        help="Skip validation of loaded family YAML specs before compilation.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    config = load_yaml(args.instances)
    family_docs = load_family_docs(
        [Path(path) for path in args.families],
        Path(args.families_dir) if args.families_dir else None,
    )
    if not args.skip_family_validation:
        try:
            validate_family_docs(family_docs)
        except FamilyValidationError as e:
            raise ConfigError(str(e)) from e

    result = compile_config(config, family_docs)

    out_dir = Path(args.out_dir)
    write_outputs(result, out_dir)

    print(f"Wrote {out_dir / 'normalized_instances.yaml'}")
    print(f"Wrote {out_dir / 'bundle_ir.yaml'}")
    print(f"Wrote {out_dir / 'triplesmap_ir.yaml'}")
    print(f"Wrote {out_dir / 'label_jobs.yaml'}")


if __name__ == "__main__":
    main()
