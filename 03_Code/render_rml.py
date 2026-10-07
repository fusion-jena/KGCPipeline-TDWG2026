from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_TEMPLATE_NAME = "rml.ttl.j2"
DEFAULT_OUT_DIR = Path("./out")
DEFAULT_OUT_FILE = "mapping.rml.ttl"


class RenderError(ValueError):
    pass


def load_yaml(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise RenderError(f"Expected mapping at top level in {path}")
    return data


def ensure_angle_brackets(value: str) -> str:
    if value.startswith("<") and value.endswith(">"):
        return value
    if value.startswith("http://") or value.startswith("https://"):
        return f"<{value}>"
    return value


def render_term(term: str) -> str:
    return ensure_angle_brackets(term)


def render_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def quote_template(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def normalize_source_format(source: Dict[str, Any]) -> str:
    raw = (
        source.get("format")
        or source.get("reference_formulation")
        or source.get("referenceFormulation")
        or ""
    )
    raw = str(raw).lower()

    aliases = {
        "csv": "csv",
        "comma-separated-values": "csv",
        "text/csv": "csv",
        "tsv": "tsv",
        "tab-separated-values": "tsv",
        "text/tab-separated-values": "tsv",
        "json": "json",
        "jsonpath": "json",
        "xml": "xml",
        "xpath": "xml",
    }
    if raw not in aliases:
        raise RenderError(f"Unsupported source format/reference formulation: {raw!r}")
    return aliases[raw]


def rml_reference_formulation(source: Dict[str, Any]) -> str:
    source_format = normalize_source_format(source)
    if source_format in {"csv", "tsv"}:
        return "ql:CSV"
    if source_format == "json":
        return "ql:JSONPath"
    if source_format == "xml":
        return "ql:XPath"
    raise RenderError(f"Unsupported source format: {source_format}")


def should_emit_iterator(source: Dict[str, Any]) -> bool:
    source_format = normalize_source_format(source)
    if source_format in {"csv", "tsv"}:
        return False
    return bool(source.get("iterator"))


def source_node_id(source_id: str) -> str:
    return f"LS_{source_id.replace(' ', '_')}"


def prepare_term_map(term_map: Dict[str, Any]) -> Dict[str, Any]:
    term_map = dict(term_map)
    if term_map.get("constant") is not None:
        if term_map.get("term_type") == "literal":
            term_map["constant_rendered"] = render_literal(str(term_map["constant"]))
        else:
            term_map["constant_rendered"] = render_term(str(term_map["constant"]))
    if term_map.get("template") is not None:
        term_map["template_rendered"] = quote_template(term_map["template"])
    if term_map.get("reference") is not None:
        term_map["reference_rendered"] = render_literal(term_map["reference"])
    if term_map.get("datatype") is not None:
        term_map["datatype_rendered"] = render_term(term_map["datatype"])
    if term_map.get("language_reference") is not None:
        term_map["language_reference_rendered"] = render_literal(str(term_map["language_reference"]))
    if term_map.get("language_template") is not None:
        term_map["language_template_rendered"] = quote_template(str(term_map["language_template"]))
    if term_map.get("parent_triples_map") is not None:
        term_map["parent_triples_map_rendered"] = f"<{term_map['parent_triples_map']}>"
    return term_map


def prepare_subject_map(subject_map: Dict[str, Any]) -> Dict[str, Any]:
    subject_map = prepare_term_map(subject_map)
    if subject_map.get("graph_constant") is not None:
        subject_map["graph_constant_rendered"] = render_term(subject_map["graph_constant"])
    if subject_map.get("graph_template") is not None:
        subject_map["graph_template_rendered"] = quote_template(subject_map["graph_template"])
    if subject_map.get("graph_reference") is not None:
        subject_map["graph_reference_rendered"] = render_literal(subject_map["graph_reference"])
    return subject_map


def prepare_context(doc: Dict[str, Any]) -> Dict[str, Any]:
    prefixes = doc.get("prefixes", {}) or {}
    raw_sources = doc.get("sources", []) or []
    triples_maps = doc.get("triples_maps", []) or []

    sources_by_id = {s["id"]: dict(s) for s in raw_sources}
    for source in sources_by_id.values():
        source["node_id"] = source_node_id(source["id"])
        source["source_format"] = normalize_source_format(source)
        source["ql_formulation"] = rml_reference_formulation(source)
        source["emit_iterator"] = should_emit_iterator(source)
        source["is_delimited_text"] = source["source_format"] in {"csv", "tsv"}

    prepared_tms = []
    for tm in triples_maps:
        logical_source_id = tm["logical_source"]
        if logical_source_id not in sources_by_id:
            raise RenderError(f"TriplesMap {tm['id']} references unknown source {logical_source_id}")

        prepared_poms = []
        for pom in tm.get("predicate_object_maps", []):
            prepared_poms.append(
                {
                    "predicate_constant_rendered": render_term(pom["predicate_constant"]),
                    "object_map": prepare_term_map(pom["object_map"]),
                }
            )

        prepared_tms.append(
            {
                "id": tm["id"],
                "id_rendered": f"<{tm['id']}>" if not tm["id"].startswith("<") else tm["id"],
                "logical_source": sources_by_id[logical_source_id],
                "subject_map": prepare_subject_map(tm["subject_map"]),
                "predicate_object_maps": prepared_poms,
            }
        )

    return {
        "prefixes": prefixes,
        "sources": list(sources_by_id.values()),
        "triples_maps": prepared_tms,
    }


def render_rml(doc: Dict[str, Any], template_dir: Path = DEFAULT_TEMPLATE_DIR, template_name: str = DEFAULT_TEMPLATE_NAME) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(enabled_extensions=(), default_for_string=False, default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(template_name)
    return template.render(**prepare_context(doc))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python render_rml.py <triplesmap_ir.yaml>", file=sys.stderr)
        sys.exit(1)

    doc = load_yaml(Path(sys.argv[1]))
    rendered = render_rml(doc)
    out_path = DEFAULT_OUT_DIR / DEFAULT_OUT_FILE
    write_text(out_path, rendered)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
