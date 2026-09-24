"""Persistent local history for meaningful actions, separate from debug logs."""
from datetime import datetime, timezone
import json
import os
from uuid import uuid4


def record_action(config, action, detail, *, task="cafe", student=None, cafe=None, **extra):
    record = {"id": uuid4().hex, "time": datetime.now(timezone.utc).isoformat(),
              "task": task, "action": action, "detail": detail,
              "student": student, "cafe": cafe, **extra}
    config.state_dir.mkdir(parents=True, exist_ok=True)
    with (config.state_dir / "important-actions.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return record
