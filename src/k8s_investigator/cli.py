"""CLI entry point: ``investigate pod <name> --namespace <ns>``.

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
from .errors import InvestigatorError
from .models.evidence import EvidenceBundle
from .models.refs import ObjectRef
from .models.report import AIHypothesisRunStatus
from .rendering import render_text
from .report_builder import build_report
from .rules.engine import evaluate_bundle
from .validation import validate_bundle_integrity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="investigate")
    subparsers = parser.add_subparsers(dest="command", required=True)

    investigate = subparsers.add_parser("investigate", help="run an investigation")
    investigate_sub = investigate.add_subparsers(dest="resource", required=True)

    pod = investigate_sub.add_parser("pod", help="investigate a single Pod")
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

    if args.command != "investigate" or args.resource != "pod":
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
    if args.with_ai_hypotheses:
        findings, ai_status, ai_error = _run_ai_hypotheses(bundle, findings, contradictions)

    try:
        report = build_report(
            bundle, findings, contradictions,
            ai_hypothesis_status=ai_status, ai_hypothesis_error=ai_error,
        )
    except InvestigatorError as exc:
        print(f"internal error: {exc}", file=sys.stderr)
        return 3

    _emit(report, args)

    if args.save_bundle and not args.from_bundle:
        Path(args.save_bundle).write_text(bundle.model_dump_json(indent=2))

    return 0


def _run_ai_hypotheses(
    bundle: EvidenceBundle, findings: list, contradictions: list
) -> tuple[list, AIHypothesisRunStatus, str | None]:
    """Attempt AI hypothesis generation. Never raises -- any failure (unconfigured provider,
    a real client's network/auth error, an unrecoverable malformed model response) is caught
    here and reported as AIHypothesisRunStatus.FAILED with the error's own message, so the
    deterministic findings already computed are always still returned and always still shipped
    in the report. A failure is never collapsed into "the AI layer ran and found nothing" --
    see models/report.py::AIHypothesisRunStatus for why that distinction matters.

    Imports the client factory lazily, mirroring _load_or_collect_bundle's lazy import of the
    live Kubernetes client: --from-bundle-only, deterministic-only usage should never require
    any AI-provider dependency to even be installed.
    """
    from .ai.client_factory import build_client_from_env
    from .ai.hypothesis import generate_ai_hypotheses

    try:
        client, model_identifier = build_client_from_env()
        ai_findings = generate_ai_hypotheses(
            bundle, findings, client, model_identifier=model_identifier, contradictions=contradictions,
        )
    except Exception as exc:  # noqa: BLE001 -- an AI-path failure must degrade honestly, not crash the CLI
        print(f"warning: AI hypothesis generation failed: {exc}", file=sys.stderr)
        return findings, AIHypothesisRunStatus.FAILED, str(exc)

    return findings + ai_findings, AIHypothesisRunStatus.SUCCEEDED, None


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
