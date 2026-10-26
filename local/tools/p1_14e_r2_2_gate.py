from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_11 as p111


def main() -> None:
    client, gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        remote = (
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json; "
                "out=Path('/tmp/p1_14e_r2_2_exact_remediation_authorization_packet'); "
                f"proj=Path({project!r}); "
                "print(json.dumps({'out_exists': out.exists(), 'project_exists': proj.exists(), 'project_is_dir': proj.is_dir()}))"
            )
        )
        out = p111.p110r3.previous.p19.a100.checked(client, remote)
        result = json.loads(out)
        result["stable_second_connection"] = bool(gate.get("stable_second_connection"))
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        client.close()


if __name__ == "__main__":
    main()
