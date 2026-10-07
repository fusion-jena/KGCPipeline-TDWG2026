from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any, Dict, List

import yaml


RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


class LabelMappingError(ValueError):
    pass


def load_yaml(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise LabelMappingError(f"Expected mapping at top level in {path}")
    return data


def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def label_source_id(job_id: str) -> str:
    return f"label_source__{job_id}"


def label_tm_id(job_id: str) -> str:
    return f"TM_{job_id}"


def label_source_from_manifest_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    job_id = entry.get("id")
    access = entry.get("access") or entry.get("relative_access")
    if not job_id or not access:
        raise LabelMappingError(f"Invalid generated label-table manifest entry: {entry!r}")
    return {
        "id": label_source_id(str(job_id)),
        "access": str(access),
        "format": "tsv",
        "reference_formulation": "tsv",
        "delimiter": "\t",
        "iterator": None,
    }


def label_triples_map_from_manifest_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(entry["id"])
    return {
        "id": label_tm_id(job_id),
        "logical_source": label_source_id(job_id),
        "subject_map": {
            "reference": "su_iri",
            "term_type": "iri",
        },
        "predicate_object_maps": [
            {
                "predicate_constant": RDFS_LABEL,
                "object_map": {
                    "reference": "label",
                    "term_type": "literal",
                },
            }
        ],
    }


def append_label_triplesmaps(triplesmap_ir: Dict[str, Any], label_tables_manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Append RML TriplesMaps that attach labels from generated label tables.

    The function is idempotent for the generated entries in the manifest: if an
    output document already contains the same label sources/TriplesMaps, they are
    replaced rather than duplicated.
    """
    generated_all = label_tables_manifest.get("generated") or []
    if not isinstance(generated_all, list):
        raise LabelMappingError("label_tables.yaml field 'generated' must be a list")
    generated = [entry for entry in generated_all if isinstance(entry, dict) and entry.get("materialize", True)]

    out = copy.deepcopy(triplesmap_ir)
    sources = out.get("sources") or []
    triples_maps = out.get("triples_maps") or []
    if not isinstance(sources, list):
        raise LabelMappingError("triplesmap_ir.yaml field 'sources' must be a list")
    if not isinstance(triples_maps, list):
        raise LabelMappingError("triplesmap_ir.yaml field 'triples_maps' must be a list")

    managed_source_ids = {label_source_id(str(entry.get("id"))) for entry in generated if entry.get("id")}
    managed_tm_ids = {label_tm_id(str(entry.get("id"))) for entry in generated if entry.get("id")}

    out["sources"] = [s for s in sources if not (isinstance(s, dict) and s.get("id") in managed_source_ids)]
    out["triples_maps"] = [tm for tm in triples_maps if not (isinstance(tm, dict) and tm.get("id") in managed_tm_ids)]

    for entry in generated:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        out["sources"].append(label_source_from_manifest_entry(entry))
        out["triples_maps"].append(label_triples_map_from_manifest_entry(entry))

    out.setdefault("label_mapping_metadata", {})
    out["label_mapping_metadata"] = {
        "label_tables_path": label_tables_manifest.get("label_tables_path"),
        "source_label_jobs_path": label_tables_manifest.get("label_jobs_path"),
        "generated_label_tables": len(generated),
        "lookup_only_label_tables": label_tables_manifest.get("lookup_only_count", 0),
        "skipped_label_tables": label_tables_manifest.get("skipped_count", 0),
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Append TriplesMaps that attach rdfs:label values from generated label tables."
    )
    parser.add_argument(
        "triplesmap_ir",
        nargs="?",
        default="out/triplesmap_ir.yaml",
        help="Base triplesmap_ir.yaml produced by normalize_to_bundle_ir.py",
    )
    parser.add_argument(
        "label_tables",
        nargs="?",
        default="out/label_tables.yaml",
        help="label_tables.yaml produced by build_label_tables.py",
    )
    parser.add_argument(
        "--out",
        default="out/triplesmap_ir.with_labels.yaml",
        help="Output TriplesMap IR with label-table sources and label TriplesMaps appended",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input triplesmap_ir file instead of writing --out",
    )
    args = parser.parse_args()

    triplesmap_path = Path(args.triplesmap_ir)
    label_tables_path = Path(args.label_tables)

    triplesmap_ir = load_yaml(triplesmap_path)
    label_tables = load_yaml(label_tables_path)
    # Record the path in the metadata for traceability; older manifests do not carry it.
    label_tables.setdefault("label_tables_path", str(label_tables_path))

    out_doc = append_label_triplesmaps(triplesmap_ir, label_tables)
    out_path = triplesmap_path if args.in_place else Path(args.out)
    write_yaml(out_path, out_doc)

    generated_count = sum(1 for entry in (label_tables.get("generated") or []) if isinstance(entry, dict) and entry.get("materialize", True))
    print(f"Appended {generated_count} label TriplesMap(s).")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
