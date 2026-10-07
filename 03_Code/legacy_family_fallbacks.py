"""Legacy per-family fallback compilers from before Pass 6.

This module is intentionally not imported by the active pipeline. It preserves
the old hard-coded resolve_* and bundle_from_* functions for reference while
normalize_to_bundle_ir.py uses declarative family resolution and emit_bundles.

These snippets depend on the symbols from normalize_to_bundle_ir.py and are not
meant to be executed as a standalone module.
"""

# The functions below were quarantined from normalize_to_bundle_ir.py in Pass 6.
# They are kept only as migration/reference material.

def resolve_unary_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> Dict[str, Any]:
    family = families.get(inst["family"], {"execution_layer": 10})
    roles = inst["roles"]

    statement_unit_type = curie_to_full(inst["statement_unit_type"], prefixes)
    statement_unit_type_local = statement_unit_type.rstrip("/#").rsplit("/", 1)[-1]

    semantic_unit_cfg = inst.get("semantic_unit", {}) or {}
    if "subject_map" in semantic_unit_cfg:
        semantic_unit_map = term_map_from_mapping(semantic_unit_cfg["subject_map"], prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)
    elif "term_map" in semantic_unit_cfg:
        semantic_unit_map = term_map_from_mapping(semantic_unit_cfg["term_map"], prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)
    elif "iri_template" in semantic_unit_cfg:
        # Legacy spelling.
        semantic_unit_map = TermMapSpec(kind="template", template=expand_prefixed_template(semantic_unit_cfg["iri_template"], prefixes), term_type="iri")
    else:
        default_template = family.get("semantic_unit_defaults", {}).get("iri_template")
        if not default_template:
            raise ConfigError(f"Unary instance {instance_id} needs semantic_unit.subject_map/template")
        semantic_unit_map = TermMapSpec(kind="template", template=expand_prefixed_template(default_template, prefixes), term_type="iri")

    subject_map = resolve_role_term(roles["subject"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri")
    predicate_map = resolve_predicate_role(roles["predicate"], prefixes)
    object_map = resolve_role_term(roles["object"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri")

    return {
        "id": instance_id,
        "family": "unary_statement",
        "source": inst["source"],
        "execution_layer": family.get("execution_layer", 10),
        "resolved": {
            "statement_unit_type": statement_unit_type,
            "statement_unit_type_local": statement_unit_type_local,
            "semantic_unit_map": asdict(semantic_unit_map),
            "label_phrase": inst.get("label_phrase", family.get("label_strategy", {}).get("defaults", {}).get("label_phrase", "related to")),
            "roles": {
                "subject": asdict(subject_map),
                "predicate": asdict(predicate_map),
                "object": asdict(object_map),
            },
        },
    }


def resolve_coordinates_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> Dict[str, Any]:
    family = families.get(inst["family"], {"execution_layer": 10})
    roles = inst.get("roles", {})
    for required in ("subject", "latitude", "longitude"):
        if required not in roles:
            raise ConfigError(f"coordinates_statement {instance_id} missing roles.{required}")

    if not inst.get("statement_unit_type"):
        raise ConfigError(f"coordinates_statement {instance_id} needs statement_unit_type")

    statement_unit_type = curie_to_full(inst["statement_unit_type"], prefixes)
    statement_unit_type_local = statement_unit_type.rstrip("/#").rsplit("/", 1)[-1]
    semantic_unit_map = resolve_semantic_unit_map(
        instance_id,
        inst,
        family,
        prefixes,
        tm_name_lookup,
        family_label="coordinates_statement",
    )

    subject_map = resolve_role_term(roles["subject"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri")
    latitude_map = resolve_role_term(roles["latitude"], prefixes, entity_registry, tm_name_lookup, default_term_type="literal")
    longitude_map = resolve_role_term(roles["longitude"], prefixes, entity_registry, tm_name_lookup, default_term_type="literal")

    return {
        "id": instance_id,
        "family": "coordinates_statement",
        "source": inst["source"],
        "execution_layer": family.get("execution_layer", 10),
        "resolved": {
            "statement_unit_type": statement_unit_type,
            "statement_unit_type_local": statement_unit_type_local,
            "semantic_unit_map": asdict(semantic_unit_map),
            "roles": {
                "subject": asdict(subject_map),
                "latitude": asdict(latitude_map),
                "longitude": asdict(longitude_map),
            },
        },
    }


def resolve_geo_index_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> Dict[str, Any]:
    family = families.get(inst["family"], {"execution_layer": 20})
    roles = inst.get("roles", {})
    for required in ("subject", "object"):
        if required not in roles:
            raise ConfigError(f"geo_index_statement {instance_id} missing roles.{required}")

    statement_unit_type = statement_unit_type_from_instance(instance_id, inst, prefixes)
    statement_unit_type_local = statement_unit_type.rstrip("/#").rsplit("/", 1)[-1]
    semantic_unit_map = resolve_semantic_unit_map(
        instance_id,
        inst,
        family,
        prefixes,
        tm_name_lookup,
        family_label="geo_index_statement",
    )

    # RML-specific lowering of the old family behavior: lat/long are read
    # from the same row as the geo subject. Users may override these roles,
    # but the old family defaults to predicates latitude/longitude as xsd:float.
    latitude_role = roles.get("latitude") or roles.get("lat") or {
        "reference": inst.get("latitude_column", family.get("coordinate_defaults", {}).get("latitude_column", "latitude")),
        "term_type": "literal",
        "datatype": inst.get("latitude_datatype", family.get("coordinate_defaults", {}).get("datatype", "xsd:float")),
    }
    longitude_role = roles.get("longitude") or roles.get("long") or {
        "reference": inst.get("longitude_column", family.get("coordinate_defaults", {}).get("longitude_column", "longitude")),
        "term_type": "literal",
        "datatype": inst.get("longitude_datatype", family.get("coordinate_defaults", {}).get("datatype", "xsd:float")),
    }

    subject_map = resolve_role_term(roles["subject"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri")
    object_map = resolve_role_term(roles["object"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri")
    latitude_map = resolve_role_term(latitude_role, prefixes, entity_registry, tm_name_lookup, default_term_type="literal")
    longitude_map = resolve_role_term(longitude_role, prefixes, entity_registry, tm_name_lookup, default_term_type="literal")

    return {
        "id": instance_id,
        "family": "geo_index_statement",
        "source": inst["source"],
        "execution_layer": family.get("execution_layer", 20),
        "resolved": {
            "statement_unit_type": statement_unit_type,
            "statement_unit_type_local": statement_unit_type_local,
            "semantic_unit_map": asdict(semantic_unit_map),
            "roles": {
                "subject": asdict(subject_map),
                "object": asdict(object_map),
                "latitude": asdict(latitude_map),
                "longitude": asdict(longitude_map),
            },
        },
    }


def resolve_time_index_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> Dict[str, Any]:
    family = families.get(inst["family"], {"execution_layer": 20})
    roles = inst.get("roles", {}) or {}
    if "subject" not in roles:
        raise ConfigError(f"time_index_statement {instance_id} missing roles.subject")

    statement_unit_type = statement_unit_type_from_instance(instance_id, inst, prefixes)
    statement_unit_type_local = statement_unit_type.rstrip("/#").rsplit("/", 1)[-1]
    semantic_unit_map = resolve_semantic_unit_map(
        instance_id,
        inst,
        family,
        prefixes,
        tm_name_lookup,
        family_label="time_index_statement",
    )

    subject_map = resolve_role_term(roles["subject"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri")
    time_fields = resolve_time_fields(instance_id, inst, prefixes, entity_registry, tm_name_lookup)

    temporal_entity_map = resolve_resource_term_map(
        inst,
        "temporal_entity",
        prefixes,
        tm_name_lookup,
        default=default_time_resource_map(prefixes, "temporalEntity", instance_id),
    )
    time_description_map = resolve_resource_term_map(
        inst,
        "time_description",
        prefixes,
        tm_name_lookup,
        default=default_time_resource_map(prefixes, "DateTimeDescription", instance_id),
    )

    return {
        "id": instance_id,
        "family": "time_index_statement",
        "source": inst["source"],
        "execution_layer": family.get("execution_layer", 20),
        "resolved": {
            "statement_unit_type": statement_unit_type,
            "statement_unit_type_local": statement_unit_type_local,
            "semantic_unit_map": asdict(semantic_unit_map),
            "roles": {"subject": asdict(subject_map)},
            "resources": {
                "temporal_entity": asdict(temporal_entity_map),
                "time_description": asdict(time_description_map),
            },
            "time_fields": time_fields,
            "emit_semantic_unit_has_time": bool(inst.get("emit_semantic_unit_has_time", family.get("emit_semantic_unit_has_time", True))),
        },
    }


def resolve_time_order_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> Dict[str, Any]:
    family = families.get(inst["family"], {"execution_layer": 20})
    roles = inst.get("roles", {}) or {}
    if "subject" not in roles:
        raise ConfigError(f"time_order_statement {instance_id} missing roles.subject")

    statement_unit_type = statement_unit_type_from_instance(instance_id, inst, prefixes)
    statement_unit_type_local = statement_unit_type.rstrip("/#").rsplit("/", 1)[-1]
    semantic_unit_map = resolve_semantic_unit_map(
        instance_id,
        inst,
        family,
        prefixes,
        tm_name_lookup,
        family_label="time_order_statement",
    )

    subject_map = resolve_role_term(roles["subject"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri")
    position_map = resolve_position_term_map(instance_id, inst, prefixes, entity_registry, tm_name_lookup)

    time_position_map = resolve_resource_term_map(
        inst,
        "time_position",
        prefixes,
        tm_name_lookup,
        default=default_time_position_resource_map(prefixes, instance_id),
    )

    temporal_entity_map = None
    resources_cfg = inst.get("resources", {}) or {}
    if resources_cfg.get("temporal_entity"):
        temporal_entity_map = resolve_resource_term_map(
            inst,
            "temporal_entity",
            prefixes,
            tm_name_lookup,
            default=default_time_resource_map(prefixes, "temporalEntity", instance_id),
        )

    return {
        "id": instance_id,
        "family": "time_order_statement",
        "source": inst["source"],
        "execution_layer": family.get("execution_layer", 20),
        "resolved": {
            "statement_unit_type": statement_unit_type,
            "statement_unit_type_local": statement_unit_type_local,
            "semantic_unit_map": asdict(semantic_unit_map),
            "roles": {"subject": asdict(subject_map), "position": asdict(position_map)},
            "resources": {
                "time_position": asdict(time_position_map),
                **({"temporal_entity": asdict(temporal_entity_map)} if temporal_entity_map else {}),
            },
            # Hand-written RML target emits: timeOrderSU time:hasTime temporalEntity
            # inside the time-index SU graph. This is optional because the old declarative
            # family instead models subject time:inTimePosition time_position.
            "emit_semantic_unit_has_time": bool(
                inst.get(
                    "emit_semantic_unit_has_time",
                    family.get("emit_semantic_unit_has_time", bool(temporal_entity_map)),
                )
            ),
            "emit_subject_in_time_position": bool(
                inst.get(
                    "emit_subject_in_time_position",
                    family.get("emit_subject_in_time_position", False),
                )
            ),
            "time_position_type": curie_to_full(
                inst.get("time_position_type", family.get("time_position_type", "time:timePosition")),
                prefixes,
            ),
        },
    }


def resolve_complex_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> Dict[str, Any]:
    family = families.get(inst["family"], {"execution_layer": 30})
    roles = inst.get("roles", {}) or {}
    if "subject" not in roles:
        raise ConfigError(f"complex_statement {instance_id} missing roles.subject")

    statement_unit_type = statement_unit_type_from_instance(instance_id, inst, prefixes)
    statement_unit_type_local = statement_unit_type.rstrip("/#").rsplit("/", 1)[-1]
    semantic_unit_map = resolve_semantic_unit_map(
        instance_id,
        inst,
        family,
        prefixes,
        tm_name_lookup,
        family_label="complex_statement",
    )

    subject_map = resolve_role_term(roles["subject"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri")
    geo_index_map = resolve_optional_complex_role(roles, "geo_index", prefixes, entity_registry, tm_name_lookup)
    time_index_map = resolve_optional_complex_role(roles, "time_index", prefixes, entity_registry, tm_name_lookup)
    time_order_map = resolve_optional_complex_role(roles, "time_order", prefixes, entity_registry, tm_name_lookup)

    temporal_entity_map = resolve_complex_resource_term_map(inst, "temporal_entity", prefixes, tm_name_lookup)
    time_position_map = resolve_complex_resource_term_map(inst, "time_position", prefixes, tm_name_lookup)

    # The hand-written YARRRML emits complex graph facts in several source mappings.
    # In the RML backend, the complex family can emit the same final quads directly.
    emit_graph = bool(inst.get("emit_complex_graph_triples", family.get("emit_complex_graph_triples", True)))

    return {
        "id": instance_id,
        "family": "complex_statement",
        "source": inst["source"],
        "execution_layer": family.get("execution_layer", 30),
        "resolved": {
            "statement_unit_type": statement_unit_type,
            "statement_unit_type_local": statement_unit_type_local,
            "semantic_unit_map": asdict(semantic_unit_map),
            "roles": {
                "subject": asdict(subject_map),
                **({"geo_index": asdict(geo_index_map)} if geo_index_map else {}),
                **({"time_index": asdict(time_index_map)} if time_index_map else {}),
                **({"time_order": asdict(time_order_map)} if time_order_map else {}),
            },
            "resources": {
                **({"temporal_entity": asdict(temporal_entity_map)} if temporal_entity_map else {}),
                **({"time_position": asdict(time_position_map)} if time_position_map else {}),
            },
            "emit_complex_graph_triples": emit_graph,
            "emit_associated_subject": bool(inst.get("emit_associated_subject", family.get("emit_associated_subject", False))),
            "emit_geo_index_type": bool(inst.get("emit_geo_index_type", family.get("emit_geo_index_type", bool(geo_index_map)))),
            "emit_time_index_type": bool(inst.get("emit_time_index_type", family.get("emit_time_index_type", bool(time_index_map)))),
            "emit_time_order_type": bool(inst.get("emit_time_order_type", family.get("emit_time_order_type", bool(time_order_map)))),
            # Gold-standard mode: time:inTimePosition points from the time-index SU to the time-order SU.
            # Old family mode can be requested by setting this to "time_position" and providing resources.time_position.
            "time_position_object": inst.get("time_position_object", family.get("time_position_object", "time_order")),
        },
    }


def resolve_compound_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
    semantic_unit_registry: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    family = families.get(inst["family"], {"execution_layer": 40})
    roles = inst.get("roles", {}) or {}
    if "subject" not in roles:
        raise ConfigError(f"compound_unit {instance_id} missing roles.subject")

    statement_unit_type = statement_unit_type_from_instance(instance_id, inst, prefixes)
    statement_unit_type_local = statement_unit_type.rstrip("/#").rsplit("/", 1)[-1]
    semantic_unit_map = resolve_semantic_unit_map(
        instance_id,
        inst,
        family,
        prefixes,
        tm_name_lookup,
        family_label="compound_unit",
    )

    subject_map = resolve_role_term(roles["subject"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri")
    association_members = resolve_compound_members(
        instance_id,
        inst,
        prefixes,
        entity_registry,
        tm_name_lookup,
        semantic_unit_registry,
    )

    resources: Dict[str, Any] = {}
    temporal_entity_map = resolve_complex_resource_term_map(inst, "temporal_entity", prefixes, tm_name_lookup)
    if temporal_entity_map:
        resources["temporal_entity"] = asdict(temporal_entity_map)
    complex_graph_map = resolve_complex_resource_term_map(inst, "complex_graph", prefixes, tm_name_lookup)
    if complex_graph_map:
        resources["complex_graph"] = asdict(complex_graph_map)
    geo_index_map = resolve_optional_complex_role(roles, "geo_index", prefixes, entity_registry, tm_name_lookup)
    if geo_index_map:
        resources["geo_index"] = asdict(geo_index_map)

    return {
        "id": instance_id,
        "family": "compound_unit",
        "source": inst["source"],
        "execution_layer": family.get("execution_layer", 40),
        "resolved": {
            "statement_unit_type": statement_unit_type,
            "statement_unit_type_local": statement_unit_type_local,
            "semantic_unit_map": asdict(semantic_unit_map),
            "roles": {"subject": asdict(subject_map)},
            "association_members": [asdict(m) for m in association_members],
            "resources": resources,
            "emit_complex_graph_triples": bool(inst.get("emit_complex_graph_triples", family.get("emit_complex_graph_triples", False))),
        },
    }


def resolve_observation_like_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
    *,
    scalar: bool,
) -> Dict[str, Any]:
    family_id = "scalar_observation_statement" if scalar else "observation_statement"
    family = families.get(inst["family"], {"execution_layer": 10})
    roles = inst.get("roles", {})

    required = ["subject", "quality", "value"] + (["unit"] if scalar else [])
    for role_name in required:
        if role_name not in roles:
            raise ConfigError(f"{family_id} {instance_id} missing roles.{role_name}")

    statement_unit_type = statement_unit_type_from_instance(instance_id, inst, prefixes)
    statement_unit_type_local = statement_unit_type.rstrip("/#").rsplit("/", 1)[-1]

    semantic_unit_cfg = inst.get("semantic_unit", {}) or {}
    if semantic_unit_cfg:
        semantic_unit_map = resolve_semantic_unit_map(
            instance_id, inst, family, prefixes, tm_name_lookup, family_label=family_id
        )
    else:
        semantic_unit_map = default_semantic_unit_map_for_observation(instance_id, prefixes)

    assay_type_role = roles.get("assay_type")
    if assay_type_role is None:
        assay_default = family.get("defaults", {}).get("assay_type", "obi:plannedProcess")
        assay_type_map = TermMapSpec(kind="constant", constant=curie_to_full(assay_default, prefixes), term_type="iri")
    else:
        assay_type_map = resolve_role_term(assay_type_role, prefixes, entity_registry, tm_name_lookup, default_term_type="iri")

    resolved_roles = {
        "subject": asdict(resolve_role_term(roles["subject"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri")),
        "assay_type": asdict(assay_type_map),
        "quality": asdict(resolve_role_term(roles["quality"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri")),
        "value": asdict(resolve_role_term(roles["value"], prefixes, entity_registry, tm_name_lookup, default_term_type="literal")),
    }
    if scalar:
        resolved_roles["unit"] = asdict(resolve_role_term(roles["unit"], prefixes, entity_registry, tm_name_lookup, default_term_type="iri"))

    resources = {
        "assay": asdict(minted_observation_resource(prefixes, "Assay", instance_id)),
        "datum": asdict(minted_observation_resource(prefixes, "Datum", instance_id)),
        "specification": asdict(minted_observation_resource(prefixes, "Specification", instance_id)),
        "quality_instance": asdict(minted_observation_resource(prefixes, "Quality", instance_id)),
    }
    if scalar:
        resources["unit_label"] = asdict(minted_observation_resource(prefixes, "UnitLabel", instance_id))

    return {
        "id": instance_id,
        "family": family_id,
        "source": inst["source"],
        "execution_layer": family.get("execution_layer", 10),
        "resolved": {
            "statement_unit_type": statement_unit_type,
            "statement_unit_type_local": statement_unit_type_local,
            "semantic_unit_map": asdict(semantic_unit_map),
            "roles": resolved_roles,
            "resources": resources,
        },
    }


def resolve_observation_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> Dict[str, Any]:
    return resolve_observation_like_instance(
        instance_id, inst, families, prefixes, entity_registry, tm_name_lookup, scalar=False
    )


def resolve_scalar_observation_instance(
    instance_id: str,
    inst: Dict[str, Any],
    families: Dict[str, Dict[str, Any]],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> Dict[str, Any]:
    return resolve_observation_like_instance(
        instance_id, inst, families, prefixes, entity_registry, tm_name_lookup, scalar=True
    )


def tm_to_term(data: Optional[Dict[str, Any]]) -> Optional[TermMapSpec]:
    return TermMapSpec(**data) if data else None


def bundle_from_entity(resolved: Dict[str, Any]) -> List[Bundle]:
    r = resolved["resolved"]
    poms = [
        PredicateObjectSpec(
            predicate_map=TermMapSpec(kind="constant", constant="rdf:type", term_type="iri"),
            object_map=TermMapSpec(kind="constant", constant=r["class_iri"], term_type="iri"),
        )
    ]

    label_map = tm_to_term(r.get("label_map"))
    if label_map:
        poms.append(
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="rdfs:label", term_type="iri"),
                object_map=label_map,
                when_present=True,
            )
        )

    description_map = tm_to_term(r.get("description_map"))
    if description_map:
        poms.append(
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="dcterms:description", term_type="iri"),
                object_map=description_map,
                when_present=True,
            )
        )

    return [
        Bundle(
            id=f"{resolved['id']}__base_entity",
            instance_id=resolved["id"],
            family="entity",
            kind="base_entity",
            source=resolved["source"],
            subject_map=TermMapSpec(**r["subject_map"]),
            graph_map=None,
            predicate_object_maps=poms,
        )
    ]


def bundle_from_unary(resolved: Dict[str, Any]) -> List[Bundle]:
    r = resolved["resolved"]
    semantic_unit_map = TermMapSpec(**r["semantic_unit_map"])
    subject_map = TermMapSpec(**r["roles"]["subject"])
    predicate_map = TermMapSpec(**r["roles"]["predicate"])
    object_map = TermMapSpec(**r["roles"]["object"])

    su_bundle = Bundle(
        id=f"{resolved['id']}__semantic_unit",
        instance_id=resolved["id"],
        family="unary_statement",
        kind="semantic_unit_resource",
        source=resolved["source"],
        subject_map=semantic_unit_map,
        graph_map=None,
        predicate_object_maps=[
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="rdf:type", term_type="iri"),
                object_map=TermMapSpec(kind="constant", constant=r["statement_unit_type"], term_type="iri"),
            ),
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="rdf:type", term_type="iri"),
                object_map=TermMapSpec(kind="constant", constant="http://example.com/semunit/assertionalStatementUnit", term_type="iri"),
            ),
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="http://example.com/semunit/hasSemanticUnitSubject", term_type="iri"),
                object_map=subject_map,
            ),
        ],
    )

    assertion_bundle = Bundle(
        id=f"{resolved['id']}__assertion_graph",
        instance_id=resolved["id"],
        family="unary_statement",
        kind="assertion_graph",
        source=resolved["source"],
        subject_map=subject_map,
        graph_map=semantic_unit_map,
        predicate_object_maps=[PredicateObjectSpec(predicate_map=predicate_map, object_map=object_map)],
    )

    return [su_bundle, assertion_bundle]


def bundle_from_coordinates(resolved: Dict[str, Any]) -> List[Bundle]:
    r = resolved["resolved"]
    semantic_unit_map = TermMapSpec(**r["semantic_unit_map"])
    subject_map = TermMapSpec(**r["roles"]["subject"])
    latitude_map = TermMapSpec(**r["roles"]["latitude"])
    longitude_map = TermMapSpec(**r["roles"]["longitude"])

    su_bundle = Bundle(
        id=f"{resolved['id']}__semantic_unit",
        instance_id=resolved["id"],
        family="coordinates_statement",
        kind="semantic_unit_resource",
        source=resolved["source"],
        subject_map=semantic_unit_map,
        graph_map=None,
        predicate_object_maps=[
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="rdf:type", term_type="iri"),
                object_map=TermMapSpec(kind="constant", constant=r["statement_unit_type"], term_type="iri"),
            ),
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="rdf:type", term_type="iri"),
                object_map=TermMapSpec(kind="constant", constant="http://example.com/semunit/assertionalStatementUnit", term_type="iri"),
            ),
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="http://example.com/semunit/hasSemanticUnitSubject", term_type="iri"),
                object_map=subject_map,
            ),
        ],
    )

    assertion_bundle = Bundle(
        id=f"{resolved['id']}__assertion_graph",
        instance_id=resolved["id"],
        family="coordinates_statement",
        kind="assertion_graph",
        source=resolved["source"],
        subject_map=subject_map,
        graph_map=semantic_unit_map,
        predicate_object_maps=[
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="geo:lat", term_type="iri"),
                object_map=latitude_map,
            ),
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="geo:long", term_type="iri"),
                object_map=longitude_map,
            ),
        ],
    )

    return [su_bundle, assertion_bundle]


def bundle_from_geo_index(resolved: Dict[str, Any]) -> List[Bundle]:
    r = resolved["resolved"]
    semantic_unit_map = TermMapSpec(**r["semantic_unit_map"])
    subject_map = TermMapSpec(**r["roles"]["subject"])
    object_map = TermMapSpec(**r["roles"]["object"])
    latitude_map = TermMapSpec(**r["roles"]["latitude"])
    longitude_map = TermMapSpec(**r["roles"]["longitude"])

    su_bundle = Bundle(
        id=f"{resolved['id']}__semantic_unit",
        instance_id=resolved["id"],
        family="geo_index_statement",
        kind="semantic_unit_resource",
        source=resolved["source"],
        subject_map=semantic_unit_map,
        graph_map=None,
        predicate_object_maps=[
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="rdf:type", term_type="iri"),
                object_map=TermMapSpec(kind="constant", constant=r["statement_unit_type"], term_type="iri"),
            ),
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="rdf:type", term_type="iri"),
                object_map=TermMapSpec(kind="constant", constant="http://example.com/semunit/assertionalStatementUnit", term_type="iri"),
            ),
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="http://example.com/semunit/hasSemanticUnitSubject", term_type="iri"),
                object_map=subject_map,
            ),
        ],
    )

    assertion_bundle = Bundle(
        id=f"{resolved['id']}__assertion_graph",
        instance_id=resolved["id"],
        family="geo_index_statement",
        kind="assertion_graph",
        source=resolved["source"],
        subject_map=subject_map,
        graph_map=semantic_unit_map,
        predicate_object_maps=[
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="ro:locatedIn", term_type="iri"),
                object_map=object_map,
            ),
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="geo:lat", term_type="iri"),
                object_map=latitude_map,
            ),
            PredicateObjectSpec(
                predicate_map=TermMapSpec(kind="constant", constant="geo:long", term_type="iri"),
                object_map=longitude_map,
            ),
        ],
    )

    return [su_bundle, assertion_bundle]


def bundle_from_time_index(resolved: Dict[str, Any]) -> List[Bundle]:
    r = resolved["resolved"]
    semantic_unit_map = TermMapSpec(**r["semantic_unit_map"])
    subject_map = TermMapSpec(**r["roles"]["subject"])
    resources = {k: TermMapSpec(**v) for k, v in r["resources"].items()}
    time_fields = r.get("time_fields", [])

    bundles: List[Bundle] = [
        Bundle(
            id=f"{resolved['id']}__semantic_unit",
            instance_id=resolved["id"],
            family="time_index_statement",
            kind="semantic_unit_resource",
            source=resolved["source"],
            subject_map=semantic_unit_map,
            graph_map=None,
            predicate_object_maps=[
                _constant_po("rdf:type", r["statement_unit_type"]),
                _constant_po("rdf:type", "http://example.com/semunit/assertionalStatementUnit"),
                _po("http://example.com/semunit/hasSemanticUnitSubject", subject_map),
            ],
        ),
        Bundle(
            id=f"{resolved['id']}__temporal_entity_resource",
            instance_id=resolved["id"],
            family="time_index_statement",
            kind="minted_resource",
            source=resolved["source"],
            subject_map=resources["temporal_entity"],
            graph_map=None,
            predicate_object_maps=[_constant_po("rdf:type", "time:temporalEntity")],
        ),
        Bundle(
            id=f"{resolved['id']}__time_description_resource",
            instance_id=resolved["id"],
            family="time_index_statement",
            kind="minted_resource",
            source=resolved["source"],
            subject_map=resources["time_description"],
            graph_map=None,
            predicate_object_maps=[_constant_po("rdf:type", "time:DateTimeDescription")],
        ),
        Bundle(
            id=f"{resolved['id']}__temporal_entity_graph",
            instance_id=resolved["id"],
            family="time_index_statement",
            kind="assertion_graph",
            source=resolved["source"],
            subject_map=resources["temporal_entity"],
            graph_map=semantic_unit_map,
            predicate_object_maps=[_po("time:inDateTime", resources["time_description"])],
        ),
    ]

    description_poms = [_po(field["predicate"], TermMapSpec(**field["object_map"])) for field in time_fields]
    if description_poms:
        bundles.append(
            Bundle(
                id=f"{resolved['id']}__time_description_graph",
                instance_id=resolved["id"],
                family="time_index_statement",
                kind="assertion_graph",
                source=resolved["source"],
                subject_map=resources["time_description"],
                graph_map=semantic_unit_map,
                predicate_object_maps=description_poms,
            )
        )

    if r.get("emit_semantic_unit_has_time", True):
        bundles.append(
            Bundle(
                id=f"{resolved['id']}__semantic_unit_graph",
                instance_id=resolved["id"],
                family="time_index_statement",
                kind="assertion_graph",
                source=resolved["source"],
                subject_map=semantic_unit_map,
                graph_map=semantic_unit_map,
                predicate_object_maps=[_po("time:hasTime", resources["temporal_entity"])],
            )
        )

    return bundles


def bundle_from_time_order(resolved: Dict[str, Any]) -> List[Bundle]:
    r = resolved["resolved"]
    semantic_unit_map = TermMapSpec(**r["semantic_unit_map"])
    subject_map = TermMapSpec(**r["roles"]["subject"])
    position_map = TermMapSpec(**r["roles"]["position"])
    resources = {k: TermMapSpec(**v) for k, v in r["resources"].items()}

    bundles: List[Bundle] = [
        Bundle(
            id=f"{resolved['id']}__semantic_unit",
            instance_id=resolved["id"],
            family="time_order_statement",
            kind="semantic_unit_resource",
            source=resolved["source"],
            subject_map=semantic_unit_map,
            graph_map=None,
            predicate_object_maps=[
                _constant_po("rdf:type", r["statement_unit_type"]),
                _constant_po("rdf:type", "http://example.com/semunit/assertionalStatementUnit"),
                _po("http://example.com/semunit/hasSemanticUnitSubject", subject_map),
            ],
        ),
        Bundle(
            id=f"{resolved['id']}__time_position_resource",
            instance_id=resolved["id"],
            family="time_order_statement",
            kind="minted_resource",
            source=resolved["source"],
            subject_map=resources["time_position"],
            graph_map=None,
            predicate_object_maps=[_constant_po("rdf:type", r["time_position_type"])],
        ),
        Bundle(
            id=f"{resolved['id']}__time_position_graph",
            instance_id=resolved["id"],
            family="time_order_statement",
            kind="assertion_graph",
            source=resolved["source"],
            subject_map=resources["time_position"],
            graph_map=semantic_unit_map,
            predicate_object_maps=[_po("time:numericPosition", position_map)],
        ),
    ]

    if r.get("emit_semantic_unit_has_time") and resources.get("temporal_entity"):
        bundles.append(
            Bundle(
                id=f"{resolved['id']}__semantic_unit_has_time_graph",
                instance_id=resolved["id"],
                family="time_order_statement",
                kind="assertion_graph",
                source=resolved["source"],
                subject_map=semantic_unit_map,
                # This matches the hand-written YARRRML: the hasTime triple lives in the
                # corresponding time-index SU graph.
                graph_map=subject_map,
                predicate_object_maps=[_po("time:hasTime", resources["temporal_entity"])],
            )
        )

    if r.get("emit_subject_in_time_position"):
        bundles.append(
            Bundle(
                id=f"{resolved['id']}__subject_in_time_position_graph",
                instance_id=resolved["id"],
                family="time_order_statement",
                kind="assertion_graph",
                source=resolved["source"],
                subject_map=subject_map,
                graph_map=semantic_unit_map,
                predicate_object_maps=[_po("time:inTimePosition", resources["time_position"])],
            )
        )

    return bundles


def bundle_from_observation_like(resolved: Dict[str, Any], *, scalar: bool) -> List[Bundle]:
    r = resolved["resolved"]
    semantic_unit_map = TermMapSpec(**r["semantic_unit_map"])
    roles = {k: TermMapSpec(**v) for k, v in r["roles"].items()}
    resources = {k: TermMapSpec(**v) for k, v in r["resources"].items()}

    datum_type = "iao:scalarMeasurementDatum" if scalar else "iao:measurementDatum"
    specification_type = "obi:scalarValueSpecification" if scalar else "iao:valueSpecification"

    bundles: List[Bundle] = [
        Bundle(
            id=f"{resolved['id']}__semantic_unit",
            instance_id=resolved["id"],
            family=resolved["family"],
            kind="semantic_unit_resource",
            source=resolved["source"],
            subject_map=semantic_unit_map,
            graph_map=None,
            predicate_object_maps=[
                _constant_po("rdf:type", r["statement_unit_type"]),
                _constant_po("rdf:type", "http://example.com/semunit/assertionalStatementUnit"),
                _constant_po("rdf:type", "http://example.com/semunit/observationStatementUnit"),
                _po("http://example.com/semunit/hasSemanticUnitSubject", roles["subject"]),
            ],
        ),
        Bundle(
            id=f"{resolved['id']}__assay_resource",
            instance_id=resolved["id"],
            family=resolved["family"],
            kind="minted_resource",
            source=resolved["source"],
            subject_map=resources["assay"],
            graph_map=None,
            predicate_object_maps=[_po("rdf:type", roles["assay_type"])],
        ),
        Bundle(
            id=f"{resolved['id']}__datum_resource",
            instance_id=resolved["id"],
            family=resolved["family"],
            kind="minted_resource",
            source=resolved["source"],
            subject_map=resources["datum"],
            graph_map=None,
            predicate_object_maps=[_constant_po("rdf:type", datum_type)],
        ),
        Bundle(
            id=f"{resolved['id']}__specification_resource",
            instance_id=resolved["id"],
            family=resolved["family"],
            kind="minted_resource",
            source=resolved["source"],
            subject_map=resources["specification"],
            graph_map=None,
            predicate_object_maps=[_constant_po("rdf:type", specification_type)],
        ),
        Bundle(
            id=f"{resolved['id']}__quality_resource",
            instance_id=resolved["id"],
            family=resolved["family"],
            kind="minted_resource",
            source=resolved["source"],
            subject_map=resources["quality_instance"],
            graph_map=None,
            predicate_object_maps=[_po("rdf:type", roles["quality"])],
        ),
    ]

    if scalar:
        bundles.append(
            Bundle(
                id=f"{resolved['id']}__unit_label_resource",
                instance_id=resolved["id"],
                family=resolved["family"],
                kind="minted_resource",
                source=resolved["source"],
                subject_map=resources["unit_label"],
                graph_map=None,
                predicate_object_maps=[
                    _constant_po("rdf:type", "iao:measurementUnitLabel"),
                    _po("rdf:type", roles["unit"]),
                ],
            )
        )

    graph = semantic_unit_map
    bundles.extend(
        [
            Bundle(
                id=f"{resolved['id']}__assertion_subject_graph",
                instance_id=resolved["id"],
                family=resolved["family"],
                kind="assertion_graph",
                source=resolved["source"],
                subject_map=roles["subject"],
                graph_map=graph,
                predicate_object_maps=[
                    _po("obi:isSpecifiedInputOf", resources["assay"]),
                    _po("ro:hasQuality", resources["quality_instance"]),
                ],
            ),
            Bundle(
                id=f"{resolved['id']}__assertion_assay_graph",
                instance_id=resolved["id"],
                family=resolved["family"],
                kind="assertion_graph",
                source=resolved["source"],
                subject_map=resources["assay"],
                graph_map=graph,
                predicate_object_maps=[
                    _po("obi:hasSpecifiedOutput", resources["datum"]),
                    _po("obi:hasSpecifiedInput", roles["subject"]),
                ],
            ),
            Bundle(
                id=f"{resolved['id']}__assertion_datum_graph",
                instance_id=resolved["id"],
                family=resolved["family"],
                kind="assertion_graph",
                source=resolved["source"],
                subject_map=resources["datum"],
                graph_map=graph,
                predicate_object_maps=[
                    _po("obi:hasValueSpecification", resources["specification"]),
                    _po("iao:isQualityMeasurementOf", resources["quality_instance"]),
                ],
            ),
            Bundle(
                id=f"{resolved['id']}__assertion_specification_graph",
                instance_id=resolved["id"],
                family=resolved["family"],
                kind="assertion_graph",
                source=resolved["source"],
                subject_map=resources["specification"],
                graph_map=graph,
                predicate_object_maps=[_po("iao:hasMeasurementValue", roles["value"])],
            ),
        ]
    )

    if scalar:
        # Add the scalar-only link from the value specification to the unit label.
        bundles[-1].predicate_object_maps.append(_po("iao:hasMeasurementUnitLabel", resources["unit_label"]))
        bundles.append(
            Bundle(
                id=f"{resolved['id']}__assertion_unit_label_graph",
                instance_id=resolved["id"],
                family=resolved["family"],
                kind="assertion_graph",
                source=resolved["source"],
                subject_map=resources["unit_label"],
                graph_map=graph,
                predicate_object_maps=[_po("rdf:type", roles["unit"])],
            )
        )

    return bundles


def bundle_from_observation(resolved: Dict[str, Any]) -> List[Bundle]:
    return bundle_from_observation_like(resolved, scalar=False)


def bundle_from_scalar_observation(resolved: Dict[str, Any]) -> List[Bundle]:
    return bundle_from_observation_like(resolved, scalar=True)


def bundle_from_complex(resolved: Dict[str, Any]) -> List[Bundle]:
    r = resolved["resolved"]
    semantic_unit_map = TermMapSpec(**r["semantic_unit_map"])
    roles = {k: TermMapSpec(**v) for k, v in r.get("roles", {}).items()}
    resources = {k: TermMapSpec(**v) for k, v in r.get("resources", {}).items()}

    poms: List[PredicateObjectSpec] = [
        _constant_po("rdf:type", r["statement_unit_type"]),
        _constant_po("rdf:type", "http://example.com/semunit/assertionalStatementUnit"),
    ]

    # The hand-written mapping uses semunit:complexStatementUnit as the statement_unit_type.
    # If a more specific type is ever configured, keep the generic complex type as well.
    if r["statement_unit_type"] != "http://example.com/semunit/complexStatementUnit":
        poms.append(_constant_po("rdf:type", "http://example.com/semunit/complexStatementUnit"))

    if r.get("emit_geo_index_type"):
        poms.append(_constant_po("rdf:type", "http://example.com/semunit/geoIndexedStatementUnit"))
    if r.get("emit_time_index_type"):
        poms.append(_constant_po("rdf:type", "http://example.com/semunit/timeIndexedStatementUnit"))
    if r.get("emit_time_order_type"):
        poms.append(_constant_po("rdf:type", "http://example.com/semunit/timeOrderedStatementUnit"))

    poms.append(_po("http://example.com/semunit/hasSemanticUnitSubject", roles["subject"]))

    if r.get("emit_associated_subject"):
        poms.append(_po("http://example.com/semunit/hasAssociatedSemanticUnit", roles["subject"]))
    if roles.get("geo_index"):
        poms.append(_po("http://example.com/semunit/hasAssociatedSemanticUnit", roles["geo_index"]))
    if roles.get("time_index"):
        poms.append(_po("http://example.com/semunit/hasAssociatedSemanticUnit", roles["time_index"]))
    if roles.get("time_order"):
        poms.append(_po("http://example.com/semunit/hasAssociatedSemanticUnit", roles["time_order"]))

    bundles: List[Bundle] = [
        Bundle(
            id=f"{resolved['id']}__semantic_unit",
            instance_id=resolved["id"],
            family="complex_statement",
            kind="semantic_unit_resource",
            source=resolved["source"],
            subject_map=semantic_unit_map,
            graph_map=None,
            predicate_object_maps=poms,
        )
    ]

    if r.get("emit_complex_graph_triples"):
        # Match the hand-written output quads:
        #   observation/compound SU bfo:occursIn geo-index SU  in GRAPH complex SU
        #   observation/compound SU time:hasTime temporalEntity in GRAPH complex SU
        #   time-index SU time:inTimePosition time-order SU     in GRAPH complex SU
        graph_poms_by_subject: List[tuple[str, TermMapSpec, List[PredicateObjectSpec]]] = []

        if roles.get("geo_index"):
            graph_poms_by_subject.append((
                "occurs_in",
                roles["subject"],
                [_po("bfo:occursIn", roles["geo_index"])],
            ))

        if resources.get("temporal_entity"):
            graph_poms_by_subject.append((
                "has_time",
                roles["subject"],
                [_po("time:hasTime", resources["temporal_entity"])],
            ))

        if roles.get("time_index"):
            if r.get("time_position_object") == "time_position":
                target = resources.get("time_position")
            else:
                target = roles.get("time_order")
            if target:
                graph_poms_by_subject.append((
                    "in_time_position",
                    roles["time_index"],
                    [_po("time:inTimePosition", target)],
                ))

        for suffix, subject, poms_for_subject in graph_poms_by_subject:
            bundles.append(
                Bundle(
                    id=f"{resolved['id']}__complex_graph_{suffix}",
                    instance_id=resolved["id"],
                    family="complex_statement",
                    kind="assertion_graph",
                    source=resolved["source"],
                    subject_map=subject,
                    graph_map=semantic_unit_map,
                    predicate_object_maps=poms_for_subject,
                )
            )

    return bundles


def bundle_from_compound(resolved: Dict[str, Any]) -> List[Bundle]:
    r = resolved["resolved"]
    semantic_unit_map = TermMapSpec(**r["semantic_unit_map"])
    roles = {k: TermMapSpec(**v) for k, v in r.get("roles", {}).items()}
    association_members = [TermMapSpec(**m) for m in r.get("association_members", [])]
    resources = {k: TermMapSpec(**v) for k, v in r.get("resources", {}).items()}

    poms: List[PredicateObjectSpec] = [
        _constant_po("rdf:type", "http://example.com/semunit/compoundUnit"),
    ]
    if r["statement_unit_type"] != "http://example.com/semunit/compoundUnit":
        poms.append(_constant_po("rdf:type", r["statement_unit_type"]))
    poms.append(_po("http://example.com/semunit/hasSemanticUnitSubject", roles["subject"]))
    for member in association_members:
        poms.append(_po("http://example.com/semunit/hasAssociatedSemanticUnit", member))

    bundles: List[Bundle] = [
        Bundle(
            id=f"{resolved['id']}__semantic_unit",
            instance_id=resolved["id"],
            family="compound_unit",
            kind="semantic_unit_resource",
            source=resolved["source"],
            subject_map=semantic_unit_map,
            graph_map=None,
            predicate_object_maps=poms,
        )
    ]

    if r.get("emit_complex_graph_triples"):
        graph_map = resources.get("complex_graph")
        if not graph_map:
            raise ConfigError(f"compound_unit {resolved['id']} emit_complex_graph_triples=true requires resources.complex_graph")
        if resources.get("geo_index"):
            bundles.append(
                Bundle(
                    id=f"{resolved['id']}__complex_graph_occurs_in",
                    instance_id=resolved["id"],
                    family="compound_unit",
                    kind="assertion_graph",
                    source=resolved["source"],
                    subject_map=semantic_unit_map,
                    graph_map=graph_map,
                    predicate_object_maps=[_po("bfo:occursIn", resources["geo_index"])],
                )
            )
        if resources.get("temporal_entity"):
            bundles.append(
                Bundle(
                    id=f"{resolved['id']}__complex_graph_has_time",
                    instance_id=resolved["id"],
                    family="compound_unit",
                    kind="assertion_graph",
                    source=resolved["source"],
                    subject_map=semantic_unit_map,
                    graph_map=graph_map,
                    predicate_object_maps=[_po("time:hasTime", resources["temporal_entity"])],
                )
            )

    return bundles


# Additional legacy helper functions quarantined in Pass 6.

def default_time_resource_map(prefixes: Dict[str, str], segment: str, instance_id: str) -> TermMapSpec:
    # Keep the historical ex:/segment/... shape used by the hand-written mappings.
    # With ex: = http://example.com/base/, this expands to http://example.com/base//segment/...
    base = prefixes.get("ex", "http://example.com/base/")
    return TermMapSpec(
        kind="template",
        template=f"{base}/{segment}/{{Observation_ID}}_{instance_id}",
        term_type="iri",
    )


def resolve_resource_term_map(
    inst: Dict[str, Any],
    resource_name: str,
    prefixes: Dict[str, str],
    tm_name_lookup: Dict[str, str],
    *,
    default: TermMapSpec,
) -> TermMapSpec:
    resources_cfg = inst.get("resources", {}) or {}
    cfg = resources_cfg.get(resource_name) or {}
    if not cfg:
        return default
    if "subject_map" in cfg:
        return term_map_from_mapping(cfg["subject_map"], prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)
    if "term_map" in cfg:
        return term_map_from_mapping(cfg["term_map"], prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)
    if any(k in cfg for k in ("reference", "template", "constant")):
        return term_map_from_mapping(cfg, prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)
    raise ConfigError(f"Resource {resource_name} must define subject_map/term_map or a term-map shape")


def default_time_position_resource_map(prefixes: Dict[str, str], instance_id: str) -> TermMapSpec:
    base = prefixes.get("ex", "http://example.com/base/")
    return TermMapSpec(
        kind="template",
        template=f"{base}TimePosition/{{Observation_ID}}_{instance_id}",
        term_type="iri",
    )


def resolve_position_term_map(
    instance_id: str,
    inst: Dict[str, Any],
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> TermMapSpec:
    roles = inst.get("roles", {}) or {}
    cfg = inst.get("position") or roles.get("position")
    if cfg is None:
        raise ConfigError(f"time_order_statement {instance_id} needs position or roles.position")
    if not isinstance(cfg, dict):
        raise ConfigError(f"time_order_statement {instance_id} position must be a mapping")

    if "object_map" in cfg:
        return term_map_from_mapping(cfg["object_map"], prefixes, default_term_type="literal", tm_name_lookup=tm_name_lookup)
    if "term_map" in cfg:
        return term_map_from_mapping(cfg["term_map"], prefixes, default_term_type="literal", tm_name_lookup=tm_name_lookup)
    if "column" in cfg:
        return TermMapSpec(
            kind="reference",
            reference=str(cfg["column"]),
            term_type="literal",
            datatype=curie_to_full(cfg["datatype"], prefixes) if cfg.get("datatype") else None,
            language=cfg.get("language"),
        )
    if "source_predicate" in cfg:
        return TermMapSpec(
            kind="reference",
            reference=str(cfg["source_predicate"]),
            term_type="literal",
            datatype=curie_to_full(cfg["datatype"], prefixes) if cfg.get("datatype") else None,
            language=cfg.get("language"),
        )
    if any(k in cfg for k in ("reference", "template", "constant", "parent", "parent_triples_map", "parentTriplesMap")):
        return term_map_from_mapping(cfg, prefixes, default_term_type="literal", tm_name_lookup=tm_name_lookup)
    raise ConfigError(f"time_order_statement {instance_id} position needs reference/template/constant/column/object_map")


def resolve_optional_complex_role(
    roles: Dict[str, Any],
    role_name: str,
    prefixes: Dict[str, str],
    entity_registry: Dict[str, Dict[str, Any]],
    tm_name_lookup: Dict[str, str],
) -> Optional[TermMapSpec]:
    role = roles.get(role_name)
    if role is None:
        return None
    return resolve_role_term(role, prefixes, entity_registry, tm_name_lookup, default_term_type="iri")


def resolve_complex_resource_term_map(
    inst: Dict[str, Any],
    resource_name: str,
    prefixes: Dict[str, str],
    tm_name_lookup: Dict[str, str],
) -> Optional[TermMapSpec]:
    resources_cfg = inst.get("resources", {}) or {}
    cfg = resources_cfg.get(resource_name)
    if not cfg:
        return None
    if not isinstance(cfg, dict):
        raise ConfigError(f"complex_statement resource {resource_name} must be a mapping")
    if "subject_map" in cfg:
        return term_map_from_mapping(cfg["subject_map"], prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)
    if "term_map" in cfg:
        return term_map_from_mapping(cfg["term_map"], prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)
    if any(k in cfg for k in ("reference", "template", "constant", "parent", "parent_triples_map", "parentTriplesMap")):
        return term_map_from_mapping(cfg, prefixes, default_term_type="iri", tm_name_lookup=tm_name_lookup)
    raise ConfigError(f"complex_statement resource {resource_name} must define subject_map/term_map or a term-map shape")


def default_semantic_unit_map_for_observation(instance_id: str, prefixes: Dict[str, str]) -> TermMapSpec:
    return TermMapSpec(
        kind="template",
        template=f"{prefixes.get('ex', 'http://example.com/base/')}SU/observationStatementUnit/{{Observation_ID}}_{instance_id}",
        term_type="iri",
    )


def minted_observation_resource(prefixes: Dict[str, str], segment: str, instance_id: str) -> TermMapSpec:
    return TermMapSpec(
        kind="template",
        template=f"{prefixes.get('ex', 'http://example.com/base/')}{segment}/{{Observation_ID}}_{instance_id}",
        term_type="iri",
    )
