from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import CORPORA_DIR
from .pipeline import BuildPaused, CorpusBuilder
from .seed import PRESETS, query_from_args


def _add_seed_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--doi", help="Seed DOI")
    parser.add_argument("--arxiv", help="Seed arXiv id")
    parser.add_argument("--title", help="Seed title (use with --author when possible)")
    parser.add_argument("--author", help="Author family name, e.g. Di Meglio")
    parser.add_argument("--venue", help="Journal or venue name")
    parser.add_argument("--year", type=int, help="Publication year")
    parser.add_argument("--pdf", type=Path, help="Optional local PDF of the seed paper")
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        help="Named seed. qc4hep is the Di Meglio et al. PRX Quantum 2024 review.",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help=f"Output directory (default: {CORPORA_DIR}/<seed-slug>/)",
    )


def _run_corpus_builder(builder: CorpusBuilder) -> None:
    try:
        builder.run()
    except BuildPaused:
        print("Paused. Re-run the same command to continue.")
        raise SystemExit(0)
    finally:
        builder.manifest.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build a resumable 1-hop citation corpus around any seed paper."
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    sub.add_parser("ui", help="Open the local desktop app.")

    sample = sub.add_parser("sample", help="Seed + N backward + N forward (checkpoint).")
    _add_seed_args(sample)
    sample.add_argument("--n-backward", type=int, default=5)
    sample.add_argument("--n-forward", type=int, default=5)

    build = sub.add_parser("build", help="Full 1-hop corpus after a sample looks right.")
    _add_seed_args(build)
    build.add_argument(
        "--yes",
        "--i-confirmed-the-sample",
        dest="confirmed",
        action="store_true",
        help="Required gate so a full run cannot start by accident.",
    )

    export = sub.add_parser(
        "export",
        help="Write JSON + markdown notes for papers already in a corpus.",
    )
    export.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help=f"Corpus to export (default: every corpus under {CORPORA_DIR}/).",
    )

    extract = sub.add_parser(
        "extract",
        help="Claim extraction API (the desktop UI is the primary interface).",
    )
    ex = extract.add_subparsers(dest="extract_cmd", required=True)
    ex.add_parser("templates", help="List starter schema templates.")
    ex.add_parser("projects", help="List extraction projects.")
    ex.add_parser(
        "abort-model",
        help="Abort an in-flight FreeToken generation (works after the UI has quit).",
    )
    ex.add_parser(
        "unload-model",
        help="Abort generation and unload FreeToken/Ollama from VRAM.",
    )
    for name, help_text in (
        ("start", "Start a new extraction run."),
        ("pause", "Pause the current run."),
        ("resume", "Resume a paused run."),
        ("status", "Show run progress."),
        ("run", "Start or resume, then process until idle."),
        ("claims", "Print claims as JSON."),
        ("export", "Write claims + review state as a JSON handoff file."),
    ):
        p = ex.add_parser(name, help=help_text)
        p.add_argument("--project", required=True)
        if name == "claims":
            p.add_argument("--claim-type")
            p.add_argument("--agreement")
            p.add_argument("--verification")
            p.add_argument("--paper")
        if name == "export":
            p.add_argument(
                "--out",
                type=Path,
                default=None,
                help="Destination JSON path (default: <project>/exports/claims-<run_id>.json).",
            )
            p.add_argument(
                "--verification",
                help="Optional filter, e.g. human_confirmed. Default: every claim in the latest run.",
            )

    args = parser.parse_args(argv)
    if args.cmd in (None, "ui"):
        from .ui.main_window import run_app

        raise SystemExit(run_app())

    if args.cmd == "extract":
        raise SystemExit(_extract_cmd(args) or 0)

    if args.cmd == "export":
        from .artifacts import export_readable
        from .catalog import list_corpora
        from .store import Manifest

        if args.corpus_dir:
            targets = [Path(args.corpus_dir).expanduser()]
        else:
            targets = [item.path for item in list_corpora()]
        if not targets:
            raise SystemExit(f"No corpora found under {CORPORA_DIR}/.")
        for path in targets:
            db = path / "manifest.db"
            if not db.is_file():
                print(f"Skip {path}: no manifest.db")
                continue
            manifest = Manifest(db)
            try:
                n = export_readable(path, manifest)
            finally:
                manifest.close()
            print(f"Exported {n} papers → {path}")
        return

    has_seed = bool(args.doi or args.arxiv or args.title or args.preset)
    if has_seed:
        seed = query_from_args(args)
        corpus_dir = (
            Path(args.corpus_dir).expanduser() if args.corpus_dir else seed.default_corpus_dir()
        )
    elif args.corpus_dir:
        seed = None
        corpus_dir = Path(args.corpus_dir).expanduser()
    else:
        raise SystemExit(
            "Provide a seed paper (--doi, --arxiv, --title, or --preset) "
            "or --corpus-dir pointing at an existing corpus to resume."
        )

    if args.cmd == "sample":
        builder = CorpusBuilder(
            corpus_dir,
            seed=seed,
            sample_backward=args.n_backward,
            sample_forward=args.n_forward,
            write_readme=False,
        )
        _run_corpus_builder(builder)
        print(f"Sample complete. Corpus: {corpus_dir}")
        return
    if args.cmd == "build":
        if not args.confirmed:
            raise SystemExit(
                "Refusing full 1-hop run. Re-run after the sample checkpoint is approved:\n"
                f"  python -m citehop build --yes --corpus-dir {corpus_dir}"
            )
        builder = CorpusBuilder(corpus_dir, seed=seed, write_readme=True)
        _run_corpus_builder(builder)
        print(f"Full 1-hop run complete. Corpus: {corpus_dir}")
        return


def _extract_cmd(args: argparse.Namespace) -> int:
    from .claims.api import ClaimsAPI

    api = ClaimsAPI()
    cmd = args.extract_cmd
    if cmd == "templates":
        for item in api.templates():
            print(
                f"{item['template_id']}\t{item['claim_type_count']}\t"
                f"{item.get('project_domain_label') or ''}"
            )
        return 0
    if cmd == "projects":
        for proj in api.list_projects():
            print(f"{proj['project_id']}\t{proj.get('display_name')}\t{proj.get('corpus_dir')}")
        return 0
    if cmd == "abort-model":
        from .claims.llm import abort_generation

        abort_generation()
        print("Sent FreeToken abort for the last Citehop generation (if any).")
        return 0
    if cmd == "unload-model":
        result = api.unload_extraction_models()
        print(result.get("message") or json.dumps(result))
        return 0
    pid = args.project
    if cmd == "start":
        print(json.dumps(api.start_run(pid), indent=2))
        return 0
    if cmd == "pause":
        print(json.dumps(api.pause_run(pid), indent=2))
        return 0
    if cmd == "resume":
        print(json.dumps(api.resume_run(pid), indent=2))
        return 0
    if cmd == "status":
        print(json.dumps(api.run_status(pid), indent=2))
        return 0
    if cmd == "run":
        status = api.run_status(pid)
        if status.get("status") in ("idle", "completed", "failed", None):
            status = api.start_run(pid)
        elif status.get("status") == "paused":
            status = api.resume_run(pid)
        while status.get("status") == "running":
            prev_pending = int(status.get("papers_pending") or 0)
            prev_extracting = int(status.get("papers_extracting") or 0)
            status = api.process_available(pid, max_papers=1)
            print(
                f"{status.get('papers_done')}/{status.get('papers_total')} "
                f"tokens={status.get('tokens_used')} status={status.get('status')}",
                flush=True,
            )
            pending = int(status.get("papers_pending") or 0)
            extracting = int(status.get("papers_extracting") or 0)
            if (
                status.get("status") == "running"
                and pending == 0
                and extracting > 0
                and prev_pending == 0
                and prev_extracting == extracting
            ):
                print(
                    "Papers are marked extracting (another worker, or a crashed run). "
                    f"If nothing is processing, resume: citehop extract resume --project {pid}",
                    flush=True,
                )
                break
        print(json.dumps(status, indent=2))
        return 0
    if cmd == "claims":
        claims = api.list_claims(
            pid,
            claim_type=args.claim_type,
            agreement=args.agreement,
            verification_status=args.verification,
            paper_canonical_id=args.paper,
        )
        print(json.dumps(claims, indent=2, ensure_ascii=False))
        return 0
    if cmd == "export":
        result = api.export_claims(
            pid,
            dest=args.out,
            verification_status=args.verification,
        )
        print(json.dumps(result, indent=2))
        return 0
    raise SystemExit(f"Unknown extract command {cmd}")


if __name__ == "__main__":
    main()
