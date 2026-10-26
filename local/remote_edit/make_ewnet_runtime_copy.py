from __future__ import annotations

import hashlib
import json
from pathlib import Path


source = Path(r"F:\作业\aibola\tmp\Epicasting-3740776f65c6f4157e67e6a78f71daeade45246b\Models\EWNet.R")
target = Path(r"F:\作业\aibola\remote_edit\post2023_inputs\EWNet.runtime.R")
text = source.read_text(encoding="utf-8")
if text.count("\n@export\n") != 1:
    raise RuntimeError("expected exactly one invalid bare @export token")
runtime = text.replace("\n@export\n", "\n# @export (runtime syntax repair; original bare token is invalid R)\n", 1)
old_train = "train_val = subset(ts(ts),  end= length(ts(ts))-NVal)"
old_val = "val = subset(ts(ts),  start= length(ts)-NVal+1)"
if runtime.count(old_train) != 1 or runtime.count(old_val) != 1:
    raise RuntimeError("expected the two invalid subset calls exactly once")
runtime = runtime.replace(old_train, "train_val = utils::head(stats::ts(ts), -NVal)", 1)
runtime = runtime.replace(old_val, "val = utils::tail(stats::ts(ts), NVal)", 1)
target.write_text(runtime, encoding="utf-8", newline="\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


receipt = {
    "source": str(source),
    "source_sha256": digest(source),
    "runtime_copy": target.name,
    "runtime_copy_sha256": digest(target),
    "changes": [
        "one bare @export token changed to an R comment",
        "invalid subset() training split replaced by head(ts, -NVal)",
        "invalid subset() validation split replaced by tail(ts, NVal)",
    ],
    "algorithm_or_parameter_change": False,
}
(target.parent / "ewnet_runtime_patch.json").write_text(
    json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(receipt, ensure_ascii=False))
