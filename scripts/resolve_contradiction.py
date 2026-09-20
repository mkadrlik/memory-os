"""Explicit, audited resolution of one Qdrant point; preview unless --apply."""
import argparse
from contextlib import closing
import copy
from datetime import datetime, timezone
import json
import os
import uuid

from qdrant_client import QdrantClient


def resolution_payload(payload, *, actor, reason):
    if not actor.strip() or not reason.strip():
        raise ValueError('Actor and resolution reason must be nonempty')
    notes = str(payload.get('reflection_notes') or '')
    if not (payload.get('contradiction_unresolved') or notes.startswith(
        ('[CONTRADICTION', '[CONSISTENT frozen]')
    )):
        raise ValueError('Point has no unresolved contradiction')
    history = copy.deepcopy(payload.get('contradiction_resolutions', []))
    if not isinstance(history, list):
        raise ValueError('Malformed resolution history; refusing to overwrite it')
    history.append({
        'id': str(uuid.uuid4()),
        'resolved_at': datetime.now(timezone.utc).isoformat(),
        'actor': actor.strip(),
        'reason': reason.strip(),
        'previous': {key: copy.deepcopy(payload[key]) for key in (
            'contradiction_unresolved', 'reflection_notes', 'reflection_count',
            'last_reflected', 'confidence_score',
        ) if key in payload},
    })
    return {
        'contradiction_unresolved': False,
        'reflection_notes': '[RESOLVED] ' + reason.strip(),
        'reflection_count': 0,
        'contradiction_resolutions': history,
    }


def resolve(client, collection, point_id, *, actor, reason, apply=False):
    points = client.retrieve(collection_name=collection, ids=[point_id],
                             with_payload=True, with_vectors=False)
    if len(points) != 1:
        raise ValueError('Point not found; nothing changed')
    update = resolution_payload(points[0].payload or {}, actor=actor, reason=reason)
    if apply:
        client.set_payload(collection_name=collection, points=[point_id],
                           payload=update, wait=True)
    return {'applied': apply, 'collection': collection, 'point_id': point_id,
            'payload_update': update}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Explicit Qdrant endpoint')
    parser.add_argument('--collection', required=True)
    parser.add_argument('--point-id', required=True, help='Unsigned integer or UUID')
    parser.add_argument('--actor', required=True)
    parser.add_argument('--reason', required=True, help='Evidence/justification for resolution')
    parser.add_argument('--apply', action='store_true', help='Write the resolution (default: preview)')
    args = parser.parse_args()
    try:
        point_id = int(args.point_id) if args.point_id.isdecimal() else str(uuid.UUID(args.point_id))
        if isinstance(point_id, int) and not 0 <= point_id < 2**64:
            raise ValueError('Point ID must be an unsigned 64-bit integer')
        with closing(QdrantClient(url=args.url, api_key=os.environ.get('QDRANT_API_KEY') or None)) as client:
            result = resolve(client, args.collection, point_id, actor=args.actor,
                             reason=args.reason, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
