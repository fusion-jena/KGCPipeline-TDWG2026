from __future__ import annotations

import argparse
import csv
import itertools
import re
import sys
from urllib.parse import quote
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
TEMPLATE_RE = re.compile(r"\{([^{}]+)\}")


class LabelTableError(ValueError):
    pass


def load_yaml(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise LabelTableError(f"Expected mapping at top level in {path}")
    return data


def dump_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def delimiter_for_source(source: Dict[str, Any]) -> str:
    delimiter = source.get("delimiter")
    if delimiter is not None:
        return str(delimiter)
    fmt = (source.get("format") or source.get("reference_formulation") or "").lower()
    return "\t" if fmt == "tsv" else ","


def read_table(path: Path, delimiter: str) -> List[Dict[str, str]]:
    if not path.exists():
        raise LabelTableError(f"Input table not found: {path}")
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        if reader.fieldnames is None:
            return []
        return [dict(row) for row in reader]


def write_tsv(path: Path, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def source_by_id(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    sources = doc.get("sources") or []
    if not isinstance(sources, list):
        raise LabelTableError("label_jobs.yaml sources must be a list")
    out = {}
    for src in sources:
        if not isinstance(src, dict) or not src.get("id"):
            raise LabelTableError(f"Invalid source entry: {src!r}")
        out[src["id"]] = src
    return out


class SourceCache:
    def __init__(self, sources: Dict[str, Dict[str, Any]], project_root: Path):
        self.sources = sources
        self.project_root = project_root
        self._tables: Dict[str, List[Dict[str, str]]] = {}
        self._indexes: Dict[Tuple[str, str], Dict[str, List[Dict[str, str]]]] = {}

    def table(self, source_id: str) -> List[Dict[str, str]]:
        if source_id not in self.sources:
            raise LabelTableError(f"Unknown source id: {source_id}")
        if source_id not in self._tables:
            src = self.sources[source_id]
            access = Path(str(src["access"]))
            if not access.is_absolute():
                access = self.project_root / access
            self._tables[source_id] = read_table(access, delimiter_for_source(src))
        return self._tables[source_id]

    def index(self, source_id: str, column: str) -> Dict[str, List[Dict[str, str]]]:
        key = (source_id, column)
        if key not in self._indexes:
            idx: Dict[str, List[Dict[str, str]]] = {}
            for row in self.table(source_id):
                idx.setdefault(str(row.get(column, "")), []).append(row)
            self._indexes[key] = idx
        return self._indexes[key]


def template_tokens(template: str) -> List[str]:
    return TOKEN_RE.findall(str(template))


def expand_template(template: str, row: Dict[str, str], *, encode_values: bool = False) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        value = str(row.get(key, ""))
        # rr:template with rr:termType rr:IRI percent-encodes replacement
        # values. This matters when a source column contains a full IRI and is
        # embedded into a larger template, e.g. ex:SU/{gbif_uri}_GBIF.
        return quote(value, safe="") if encode_values else value
    return TEMPLATE_RE.sub(repl, str(template))


def evaluate_term_map(term_map: Optional[Dict[str, Any]], row: Dict[str, str]) -> Optional[str]:
    if not term_map:
        return None
    kind = term_map.get("kind")
    if kind == "constant" and term_map.get("constant") is not None:
        return str(term_map["constant"])
    if kind == "reference" and term_map.get("reference") is not None:
        return str(row.get(str(term_map["reference"]), ""))
    if kind == "template" and term_map.get("template") is not None:
        return expand_template(str(term_map["template"]), row, encode_values=(term_map.get("term_type") == "iri"))
    # Parent maps are not directly evaluable without the matching entity/source context.
    return None


def nonempty(value: Optional[str]) -> bool:
    return value is not None and str(value) != ""


def local_key(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    # Keep existing compact values as-is; for IRIs, use their final path/hash segment.
    text = text.rstrip("/")
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rsplit("/", 1)[-1]


def normalize_literal_fragment(value: Optional[str]) -> str:
    if value is None:
        return ""
    return str(value)


def joined_rows_for_token(token: Dict[str, Any], base_row: Dict[str, str], cache: SourceCache) -> List[Dict[str, str]]:
    entity_source = token.get("entity_source")
    join_conditions = token.get("join_conditions") or []
    if not entity_source or not join_conditions:
        return []

    candidate_rows: Optional[List[Dict[str, str]]] = None
    for cond in join_conditions:
        child_col = str(cond["child"])
        parent_col = str(cond["parent"])
        child_value = str(base_row.get(child_col, ""))
        matches = cache.index(str(entity_source), parent_col).get(child_value, [])
        if candidate_rows is None:
            candidate_rows = list(matches)
        else:
            # Multiple join conditions are ANDed.
            match_ids = {id(row) for row in matches}
            candidate_rows = [row for row in candidate_rows if id(row) in match_ids]
    return candidate_rows or []


def lookup_labels(label_lookup: Dict[str, List[str]], su_iri: Optional[str]) -> List[str]:
    if not nonempty(su_iri):
        return []
    return list(label_lookup.get(str(su_iri), []))


def token_values(
    token_name: str,
    token: Dict[str, Any],
    base_row: Dict[str, str],
    cache: SourceCache,
    label_lookup: Dict[str, List[str]],
) -> List[str]:
    kind = token.get("kind")

    if kind in {"literal", "instance_value", "instance_label_token"}:
        value = token.get("value")
        if value is None:
            value = token.get("default")
        return [normalize_literal_fragment(value)] if value is not None else []

    if kind in {"term_map", "time_field"}:
        value = evaluate_term_map(token.get("term_map"), base_row)
        if nonempty(value):
            return [local_key(value) if token_name.endswith("_key") else str(value)]
        return []

    if kind == "entity_label":
        if token.get("status") != "resolved":
            default = token.get("default")
            return [str(default)] if default is not None else []
        label_map = token.get("label_map")
        if not label_map:
            return []
        if token.get("join_strategy") == "same_source":
            value = evaluate_term_map(label_map, base_row)
            return [str(value)] if nonempty(value) else []
        if token.get("join_strategy") == "parent_triples_map":
            values: List[str] = []
            for joined in joined_rows_for_token(token, base_row, cache):
                value = evaluate_term_map(label_map, joined)
                if nonempty(value):
                    values.append(str(value))
            return values
        return []

    if kind == "semantic_unit_label":
        su_iri = evaluate_term_map(token.get("semantic_unit_map"), base_row)
        return lookup_labels(label_lookup, su_iri)

    if kind == "association_member_labels":
        member_labels: List[str] = []
        for member_map in token.get("members") or []:
            su_iri = evaluate_term_map(member_map, base_row)
            labels = lookup_labels(label_lookup, su_iri)
            if not labels:
                return []
            # Use the first label for each member to avoid combinatorial blow-up in
            # compound labels. The source label jobs are deterministic, so this is stable.
            member_labels.append(labels[0])
        return ["; ".join(member_labels)] if member_labels else []

    return []


def render_template(template: str, values: Dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        return str(values.get(match.group(1), ""))
    return TOKEN_RE.sub(repl, str(template))


def choose_template(job: Dict[str, Any], value_options: Dict[str, List[str]]) -> Optional[str]:
    cases = job.get("cases") or []
    if isinstance(cases, list):
        for case in cases:
            if not isinstance(case, dict) or not isinstance(case.get("template"), str):
                continue
            requires = case.get("requires") or []
            if all(value_options.get(str(token)) for token in requires):
                return str(case["template"])

    template = job.get("template")
    if isinstance(template, str) and all(value_options.get(t) for t in template_tokens(template)):
        return template

    fallback = job.get("fallback_template")
    if isinstance(fallback, str) and all(value_options.get(t) for t in template_tokens(fallback)):
        return fallback

    return None


def cartesian_value_assignments(template: str, value_options: Dict[str, List[str]]) -> Iterable[Dict[str, str]]:
    tokens = template_tokens(template)
    option_lists: List[List[str]] = []
    for token in tokens:
        values = value_options.get(token) or []
        if not values:
            return []
        option_lists.append(values)
    assignments = []
    for combo in itertools.product(*option_lists):
        assignments.append(dict(zip(tokens, combo)))
    return assignments


def build_rows_for_job(
    job: Dict[str, Any],
    cache: SourceCache,
    label_lookup: Dict[str, List[str]],
) -> Tuple[List[Dict[str, str]], Optional[str]]:
    source_id = job.get("source")
    if not source_id:
        return [], "missing job.source"

    rows: List[Dict[str, str]] = []
    subject_map = job.get("subject_map")

    for idx, base_row in enumerate(cache.table(str(source_id)), start=1):
        su_iri = evaluate_term_map(subject_map, base_row)
        if not nonempty(su_iri):
            continue

        if job.get("mode") == "basic_table":
            label = evaluate_term_map(job.get("label_map"), base_row)
            if nonempty(label):
                rows.append({
                    "su_iri": str(su_iri),
                    "label": str(label),
                    "job_id": str(job.get("id", "")),
                    "row_number": str(idx),
                })
            continue

        tokens = job.get("tokens") or {}
        unresolved = set(job.get("unresolved_tokens") or [])
        value_options: Dict[str, List[str]] = {}
        for token_name, token in tokens.items():
            if token_name in unresolved:
                # The fallback template may not need this token.
                values = []
            else:
                values = token_values(token_name, token, base_row, cache, label_lookup)
            value_options[token_name] = [v for v in values if nonempty(v)]

        template = choose_template(job, value_options)
        if not template:
            continue

        for assignment in cartesian_value_assignments(template, value_options):
            label = render_template(template, assignment)
            if label:
                rows.append({
                    "su_iri": str(su_iri),
                    "label": label,
                    "job_id": str(job.get("id", "")),
                    "row_number": str(idx),
                })

    return rows, None


def add_rows_to_label_lookup(label_lookup: Dict[str, List[str]], rows: List[Dict[str, str]]) -> None:
    for row in rows:
        su_iri = str(row.get("su_iri", ""))
        label = str(row.get("label", ""))
        if not su_iri or not label:
            continue
        labels = label_lookup.setdefault(su_iri, [])
        if label not in labels:
            labels.append(label)


def build_label_tables(
    label_jobs_path: Path,
    *,
    project_root: Path,
    out_dir: Path,
) -> Dict[str, Any]:
    doc = load_yaml(label_jobs_path)
    sources = source_by_id(doc)
    cache = SourceCache(sources, project_root)

    raw_jobs = doc.get("label_jobs", []) or []
    jobs: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for job in raw_jobs:
        if not isinstance(job, dict) or not job.get("id"):
            skipped.append({"id": "<unknown>", "reason": "invalid label job entry"})
        else:
            jobs.append(job)

    generated: List[Dict[str, Any]] = []
    label_lookup: Dict[str, List[str]] = {}
    pending = list(jobs)
    generated_ids: set[str] = set()
    pass_no = 0

    while pending:
        pass_no += 1
        progress = False
        next_pending: List[Dict[str, Any]] = []

        for job in pending:
            if job["id"] in generated_ids:
                continue

            try:
                rows, skip_reason = build_rows_for_job(job, cache, label_lookup)
            except Exception as e:  # keep batch processing useful while surfacing the error
                skipped.append({"id": job.get("id"), "instance_id": job.get("instance_id"), "reason": str(e)})
                generated_ids.add(str(job.get("id")))
                continue

            if skip_reason:
                skipped.append({"id": job["id"], "instance_id": job.get("instance_id"), "family": job.get("family"), "reason": skip_reason})
                generated_ids.add(str(job["id"]))
                continue

            if not rows:
                # Jobs that depend on semantic-unit labels may become buildable after
                # another pending job has populated the lookup. Retry them in a later pass.
                if job.get("requires_existing_labels") or any(
                    (tok or {}).get("requires_existing_label") or (tok or {}).get("requires_existing_labels")
                    for tok in (job.get("tokens") or {}).values()
                    if isinstance(tok, dict)
                ):
                    next_pending.append(job)
                    continue
                skipped.append({"id": job["id"], "instance_id": job.get("instance_id"), "family": job.get("family"), "reason": "no label rows generated"})
                generated_ids.add(str(job["id"]))
                continue

            rel_path = Path("intermediate") / "labels" / f"{job['id']}.tsv"
            out_path = out_dir / rel_path
            write_tsv(out_path, rows, ["su_iri", "label", "job_id", "row_number"])
            add_rows_to_label_lookup(label_lookup, rows)

            materialize = bool(job.get("materialize", True))
            generated.append({
                "id": job["id"],
                "instance_id": job.get("instance_id"),
                "family": job.get("family"),
                "rows": len(rows),
                "access": str(out_path.as_posix()),
                "relative_access": str(rel_path.as_posix()),
                "materialize": materialize,
                "lookup_only": bool(job.get("lookup_only", not materialize)),
                "build_pass": pass_no,
            })
            generated_ids.add(str(job["id"]))
            progress = True

        if not next_pending:
            break
        if not progress:
            for job in next_pending:
                skipped.append({
                    "id": job["id"],
                    "instance_id": job.get("instance_id"),
                    "family": job.get("family"),
                    "reason": "unresolved semantic-unit label dependencies or no label rows generated",
                })
                generated_ids.add(str(job["id"]))
            break
        pending = next_pending

    materialized_count = sum(1 for entry in generated if entry.get("materialize", True))
    lookup_only_count = sum(1 for entry in generated if not entry.get("materialize", True))
    manifest = {
        "label_jobs_path": str(label_jobs_path),
        "project_root": str(project_root),
        "out_dir": str(out_dir),
        "generated_count": len(generated),
        "materialized_count": materialized_count,
        "lookup_only_count": lookup_only_count,
        "skipped_count": len(skipped),
        "generated": generated,
        "skipped": skipped,
    }
    dump_yaml(out_dir / "label_tables.yaml", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build intermediate TSV label tables from label_jobs.yaml.")
    parser.add_argument("label_jobs", nargs="?", default="out/label_jobs.yaml", help="Path to label_jobs.yaml")
    parser.add_argument("--project-root", default=".", help="Project root used to resolve source access paths")
    parser.add_argument("--out-dir", default="out", help="Output directory; tables are written under out/intermediate/labels")
    args = parser.parse_args()

    manifest = build_label_tables(
        Path(args.label_jobs),
        project_root=Path(args.project_root).resolve(),
        out_dir=Path(args.out_dir),
    )

    print(
        f"Generated {manifest['generated_count']} label table(s) "
        f"({manifest.get('materialized_count', manifest['generated_count'])} materialized, "
        f"{manifest.get('lookup_only_count', 0)} lookup-only)."
    )
    if manifest["skipped_count"]:
        print(f"Skipped {manifest['skipped_count']} label job(s); see {Path(args.out_dir) / 'label_tables.yaml'}.")


if __name__ == "__main__":
    main()
