from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence

import render_rml


class PipelineError(RuntimeError):
    pass


def project_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_path(path_like: str | Path, root: Path) -> Path:
    path = Path(path_like)
    if path.is_absolute():
        return path
    return root / path


def run_command(cmd: Sequence[str], *, cwd: Path, verbose: bool = True) -> None:
    if verbose:
        print("\n$ " + " ".join(str(part) for part in cmd))
    completed = subprocess.run(list(cmd), cwd=str(cwd))
    if completed.returncode != 0:
        raise PipelineError(f"Command failed with exit code {completed.returncode}: {' '.join(cmd)}")


def render_mapping(triplesmap_ir_path: Path, mapping_out_path: Path) -> None:
    doc = render_rml.load_yaml(triplesmap_ir_path)
    rendered = render_rml.render_rml(doc)
    render_rml.write_text(mapping_out_path, rendered)
    print(f"Wrote {mapping_out_path}")


def main() -> None:
    root_default = project_root_from_script()
    parser = argparse.ArgumentParser(
        description=(
            "Run the RML generator pipeline: normalize instances, optionally build joined label "
            "tables and append label TriplesMaps, then render mapping.rml.ttl."
        )
    )
    parser.add_argument(
        "--project-root",
        default=str(root_default),
        help="Project root. Defaults to the parent directory of 03_Code.",
    )
    parser.add_argument(
        "--instances",
        default="02_Mappings/rml_instances.example.yaml",
        help="Path to the RML instances YAML, relative to project root unless absolute.",
    )
    parser.add_argument(
        "--families-dir",
        default="05_RSTemplateLibrary",
        help="Directory containing *.family.rml.yaml files, relative to project root unless absolute.",
    )
    parser.add_argument(
        "--out-dir",
        default="06_Output",
        help="Output directory, relative to project root unless absolute.",
    )
    parser.add_argument(
        "--skip-family-validation",
        action="store_true",
        help="Forwarded to normalize_to_bundle_ir.py.",
    )
    parser.add_argument(
        "--skip-labels",
        action="store_true",
        help="Skip joined-label table generation and render the base TriplesMap IR only.",
    )
    parser.add_argument(
        "--mapping-out",
        default=None,
        help="Output mapping TTL path. Defaults to <out-dir>/mapping.rml.ttl.",
    )
    parser.add_argument(
        "--run-morphkgc",
        action="store_true",
        help="After rendering, run `python -m morph_kgc` with --morph-config.",
    )
    parser.add_argument(
        "--morph-config",
        default="04_MorphKGCConfig/morphkgc.ini",
        help="Morph-KGC config path, relative to project root unless absolute.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not echo subprocess commands before running them.",
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    code_dir = root / "03_Code"
    instances = resolve_path(args.instances, root)
    families_dir = resolve_path(args.families_dir, root)
    out_dir = resolve_path(args.out_dir, root)
    mapping_out = resolve_path(args.mapping_out, root) if args.mapping_out else out_dir / "mapping.rml.ttl"
    verbose = not args.quiet

    if not instances.exists():
        raise PipelineError(f"Instances file not found: {instances}")
    if not families_dir.exists():
        raise PipelineError(f"Families directory not found: {families_dir}")
    if not code_dir.exists():
        raise PipelineError(f"Code directory not found: {code_dir}")

    py = sys.executable
    normalize_cmd = [
        py,
        str(code_dir / "normalize_to_bundle_ir.py"),
        str(instances),
        "--families-dir",
        str(families_dir),
        "--out-dir",
        str(out_dir),
    ]
    if args.skip_family_validation:
        normalize_cmd.append("--skip-family-validation")
    run_command(normalize_cmd, cwd=root, verbose=verbose)

    base_triplesmap_ir = out_dir / "triplesmap_ir.yaml"
    render_input = base_triplesmap_ir

    if not args.skip_labels:
        label_jobs = out_dir / "label_jobs.yaml"
        if not label_jobs.exists():
            raise PipelineError(f"Expected label jobs file not found: {label_jobs}")

        run_command(
            [
                py,
                str(code_dir / "build_label_tables.py"),
                str(label_jobs),
                "--project-root",
                str(root),
                "--out-dir",
                str(out_dir),
            ],
            cwd=root,
            verbose=verbose,
        )

        label_tables = out_dir / "label_tables.yaml"
        with_labels_ir = out_dir / "triplesmap_ir.with_labels.yaml"
        run_command(
            [
                py,
                str(code_dir / "append_label_triplesmaps.py"),
                str(base_triplesmap_ir),
                str(label_tables),
                "--out",
                str(with_labels_ir),
            ],
            cwd=root,
            verbose=verbose,
        )
        render_input = with_labels_ir

    print("\n$ render_rml " + str(render_input)) if verbose else None
    render_mapping(render_input, mapping_out)

    if args.run_morphkgc:
        morph_config = resolve_path(args.morph_config, root)
        if not morph_config.exists():
            raise PipelineError(f"Morph-KGC config not found: {morph_config}")
        run_command([py, "-m", "morph_kgc", str(morph_config)], cwd=root, verbose=verbose)

    print("\nPipeline complete.")
    print(f"Mapping: {mapping_out}")
    if not args.skip_labels:
        print(f"Label tables: {out_dir / 'label_tables.yaml'}")
        print(f"TriplesMap IR with labels: {out_dir / 'triplesmap_ir.with_labels.yaml'}")


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
