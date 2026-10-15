"""CLI entry point: ``kai pod <name> --namespace <ns>``.

Two console scripts point at the same ``main()`` (see ``pyproject.toml``'s ``[project.scripts]``):
``kai``, the primary, short name, and ``investigate``, kept only as a trivial, backward-compatible
alias. Neither hardcodes its own name as argparse's ``prog`` -- each shows its own invocation name
in ``--help`` because argparse derives ``prog`` from ``sys.argv[0]`` by default.

The command hierarchy is intentionally flat: ``<prog> pod <name> ...``, not the earlier
``investigate investigate pod <name> ...`` double-nesting (the outer "investigate" subcommand
added nothing a flat structure doesn't already say via the executable's own name).

Supports two evidence sources, per docs/phase-0.8-mvp-blueprint.md's acceptance criteria:
``--from-bundle`` runs the deterministic pipeline against an already-collected (or hand-built
fixture) EvidenceBundle with no cluster access at all; omitting it collects live, using the
official Kubernetes client's normal kubeconfig discovery.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .bundle_builder import build_bundle
from .collector.client import DEFAULT_LOG_SINCE_SECONDS, DEFAULT_LOG_TAIL_LINES
from .collector.collect import collect_pod_evidence
from .errors import BundleExportSanitizationError, BundleExportWriteError, InvestigatorError
from .export_sanitization import sanitize_bundle_for_export
from .models.evidence import EvidenceBundle
from .models.refs import ObjectRef
from .models.report import AIHypothesisOutcome, AIHypothesisRejectionReason, AIHypothesisRunStatus
from .rendering import render_text
from .report_builder import build_report
from .rules.engine import evaluate_bundle
from .validation import validate_bundle_integrity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evidence-first, read-only Kubernetes investigation. Collects a bounded, "
            "frozen snapshot of a Pod's evidence, correlates it deterministically (rules "
            "R1-R11), and produces a report whose findings are always traceable to specific, "
            "validated evidence -- no AI model is required. Run `%(prog)s pod --help` for the "
            "full set of options, including offline replay (--from-bundle), safe-by-default "
            "evidence export (--save-bundle), and optional AI hypotheses "
            "(--with-ai-hypotheses)."
        ),
    )
    subparsers = parser.add_subparsers(dest="resource", required=True)

    pod = subparsers.add_parser(
        "pod",
        help="investigate a single Pod",
        description=(
            "Investigate one Pod. Reads live from a cluster by default, or replays an "
            "already-saved EvidenceBundle with --from-bundle (no cluster access at all)."
        ),
    )
    pod.add_argument("name", help="name of the Pod to investigate")
    pod.add_argument("--namespace", "-n", default="default")
    pod.add_argument(
        "--from-bundle",
        metavar="PATH",
        help="run against an already-saved EvidenceBundle JSON file; no cluster access",
    )
    pod.add_argument(
        "--save-bundle", metavar="PATH", help="save the freshly collected bundle to this path"
    )
    pod.add_argument(
        "--allow-unsanitized-bundle-export",
        action="store_true",
        help=(
            "write the RAW bundle to --save-bundle instead of the sanitized (default) one. "
            "The raw bundle may contain credentials or other sensitive values exactly as "
            "collected from the cluster (e.g. a plaintext connection string in a Pod's own env "
            "var) -- sanitization is best-effort, not a security boundary, so review before "
            "sharing. Only valid together with --save-bundle."
        ),
    )
    pod.add_argument("--kubeconfig", default=None, help="path to a kubeconfig file (live collection only)")
    pod.add_argument("--context", default=None, help="kubeconfig context to use (live collection only)")
    pod.add_argument("--log-tail-lines", type=int, default=DEFAULT_LOG_TAIL_LINES)
    pod.add_argument("--log-since-seconds", type=int, default=DEFAULT_LOG_SINCE_SECONDS)
    pod.add_argument("--output", choices=["json", "text", "both"], default="text")
    pod.add_argument("--out", metavar="PATH", help="write output to this file instead of stdout")
    pod.add_argument(
        "--with-ai-hypotheses",
        action="store_true",
        help=(
            "also attempt AI hypothesis generation over the already-validated evidence and "
            "deterministic findings (opt-in; off by default). Requires a configured provider "
            "(AI_HYPOTHESIS_PROVIDER and that provider's own variables) -- see "
            "k8s_investigator.ai.client_factory. A failure here never blocks or alters the "
            "deterministic findings; it is reported honestly as ai_hypothesis_status=FAILED, "
            "never as a silent zero-hypothesis success."
        ),
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.resource != "pod":
        parser.print_help(sys.stderr)
        return 2

    if args.from_bundle and args.save_bundle:
        print(
            "error: --save-bundle and --from-bundle cannot be combined -- --from-bundle loads "
            "an already-collected bundle rather than collecting a new one, so there is nothing "
            "freshly collected to save. Pass --save-bundle on its own (without --from-bundle) "
            "to collect live and save the result.",
            file=sys.stderr,
        )
        return 2

    if args.allow_unsanitized_bundle_export and not args.save_bundle:
        print(
            "error: --allow-unsanitized-bundle-export is only valid together with "
            "--save-bundle.",
            file=sys.stderr,
        )
        return 2

    try:
        bundle = _load_or_collect_bundle(args)
    except Exception as exc:  # noqa: BLE001 -- a CLI boundary; report, don't crash with a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        validate_bundle_integrity(bundle)
        findings, contradictions = evaluate_bundle(bundle)
    except InvestigatorError as exc:
        print(f"internal error: {exc}", file=sys.stderr)
        return 3

    ai_status = AIHypothesisRunStatus.NOT_ATTEMPTED
    ai_error: str | None = None
    ai_outcome: AIHypothesisOutcome | None = None
    ai_rejection_reasons: list[AIHypothesisRejectionReason] = []
    if args.with_ai_hypotheses:
        findings, ai_status, ai_error, ai_outcome, ai_rejection_reasons = _run_ai_hypotheses(
            bundle, findings, contradictions
        )

    try:
        report = build_report(
            bundle, findings, contradictions,
            ai_hypothesis_status=ai_status, ai_hypothesis_error=ai_error,
            ai_hypothesis_outcome=ai_outcome, ai_hypothesis_rejection_reasons=ai_rejection_reasons,
        )
    except InvestigatorError as exc:
        print(f"internal error: {exc}", file=sys.stderr)
        return 3

    _emit(report, args)

    if args.save_bundle and not args.from_bundle:
        if args.allow_unsanitized_bundle_export:
            print(
                "warning: writing an UNSANITIZED evidence bundle -- it may contain credentials "
                "or other sensitive values exactly as collected from the cluster (e.g. a "
                "plaintext connection string in a Pod's own env var). Review this file before "
                "sharing or retaining it.",
                file=sys.stderr,
            )
            bundle_to_save = bundle
        else:
            try:
                bundle_to_save = sanitize_bundle_for_export(bundle)
                validate_bundle_integrity(bundle_to_save)
            except Exception as exc:  # noqa: BLE001 -- never let a raw payload fragment reach stderr
                print(f"error: {BundleExportSanitizationError(type(exc).__name__)}", file=sys.stderr)
                return 4

        try:
            Path(args.save_bundle).write_text(bundle_to_save.model_dump_json(indent=2))
        except Exception as exc:  # noqa: BLE001 -- same rule: type name only, never str(exc)
            print(
                f"error: {BundleExportWriteError(type(exc).__name__, raw=args.allow_unsanitized_bundle_export)}",
                file=sys.stderr,
            )
            return 4

    return 0


def _run_ai_hypotheses(
    bundle: EvidenceBundle, findings: list, contradictions: list
) -> tuple[list, AIHypothesisRunStatus, str | None, AIHypothesisOutcome | None, list[AIHypothesisRejectionReason]]:
    """Attempt AI hypothesis generation. Never raises -- any failure (unconfigured provider,
    a real client's network/auth error, an unrecoverable malformed model response) is caught
    here and reported as AIHypothesisRunStatus.FAILED with the error's own message, so the
    deterministic findings already computed are always still returned and always still shipped
    in the report. A failure is never collapsed into "the AI layer ran and found nothing" --
    see models/report.py::AIHypothesisRunStatus for why that distinction matters. A FAILED
    result never carries an outcome or rejection reasons (both are empty/None) -- those are only
    meaningful once a call actually completed (Phase 0.16).

    The stderr summary line printed on a SUCCEEDED run (outcome + counts only -- never a raw
    candidate statement, evidence payload, or exception message) is this project's one new
    piece of AI-path observability from Phase 0.16; the existing FAILED-path warning is
    unchanged.

    Imports the client factory lazily, mirroring _load_or_collect_bundle's lazy import of the
    live Kubernetes client: --from-bundle-only, deterministic-only usage should never require
    any AI-provider dependency to even be installed.
    """
    from .ai.client_factory import build_client_from_env
    from .ai.hypothesis import generate_ai_hypotheses

    try:
        client, model_identifier = build_client_from_env()
        result = generate_ai_hypotheses(
            bundle, findings, client, model_identifier=model_identifier, contradictions=contradictions,
        )
    except Exception as exc:  # noqa: BLE001 -- an AI-path failure must degrade honestly, not crash the CLI
        print(f"warning: AI hypothesis generation failed: {exc}", file=sys.stderr)
        return findings, AIHypothesisRunStatus.FAILED, str(exc), None, []

    print(
        f"AI hypothesis generation: {result.outcome.value} "
        f"({len(result.accepted)} accepted, {len(result.rejection_reasons)} rejected)",
        file=sys.stderr,
    )
    return (
        findings + result.accepted,
        AIHypothesisRunStatus.SUCCEEDED,
        None,
        result.outcome,
        result.rejection_reasons,
    )


def _load_or_collect_bundle(args: argparse.Namespace) -> EvidenceBundle:
    if args.from_bundle:
        text = Path(args.from_bundle).read_text(encoding="utf-8")
        return EvidenceBundle.model_validate_json(text)

    # Live collection -- imported lazily so `--from-bundle` usage never requires the
    # `kubernetes` package's runtime config-loading machinery to succeed.
    from .collector.k8s_client import K8sApiCollectorClient, load_kubeconfig

    load_kubeconfig(args.kubeconfig, args.context)
    client = K8sApiCollectorClient()

    started_at = datetime.now(timezone.utc)
    outcomes = collect_pod_evidence(
        client,
        args.namespace,
        args.name,
        log_tail_lines=args.log_tail_lines,
        log_since_seconds=args.log_since_seconds,
    )
    finished_at = datetime.now(timezone.utc)

    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace=args.namespace, name=args.name)
    return build_bundle(
        target, outcomes, collection_started_at=started_at, collection_finished_at=finished_at
    )


def _emit(report, args: argparse.Namespace) -> None:
    pieces: list[str] = []
    if args.output in ("text", "both"):
        pieces.append(render_text(report))
    if args.output in ("json", "both"):
        pieces.append(report.model_dump_json(indent=2))
    text = "\n\n".join(pieces)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
