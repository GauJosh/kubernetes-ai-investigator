from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from k8s_investigator.bundle_builder import build_bundle
from k8s_investigator.collector.collect import collect_pod_evidence
from k8s_investigator.models.refs import ObjectRef


def collect_and_build(client, namespace: str = "demo-ns", name: str = "demo-pod", **kwargs):
    """Run the real collection + bundle-building pipeline against a fake client.

    This exercises exactly the same code path the CLI's live-collection mode uses, just with
    FakeKubernetesClient standing in for a real cluster.
    """
    started = datetime.now(timezone.utc)
    outcomes = collect_pod_evidence(client, namespace, name, **kwargs)
    finished = datetime.now(timezone.utc)
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace=namespace, name=name)
    return build_bundle(
        target, outcomes, collection_started_at=started, collection_finished_at=finished
    )
