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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "investigate" or args.resource != "pod":
        parser.print_help(sys.stderr)
        return 2

    try:
        bundle = _load_or_collect_bundle(args)
    except Exception as exc:  # noqa: BLE001 -- a CLI boundary; report, don't crash with a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        validate_bundle_integrity(bundle)
        findings, contradictions = evaluate_bundle(bundle)
        report = build_report(bundle, findings, contradictions)
    except InvestigatorError as exc:
        print(f"internal error: {exc}", file=sys.stderr)
        return 3

    _emit(report, args)

    if args.save_bundle and not args.from_bundle:
        Path(args.save_bundle).write_text(bundle.model_dump_json(indent=2))

    return 0


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
