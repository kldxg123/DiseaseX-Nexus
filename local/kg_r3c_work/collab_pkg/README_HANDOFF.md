# epidemic-openclaw collaboration handoff bundle

Generated UTC: 20260809T062021Z

This bundle is intended for another coding model to collaborate on the current project without exposing secrets.

## Safety rules

- Do not request, print, copy, commit, or store passwords, tokens, private keys, Cookie values, DSNs, or raw SSH config.
- Use the existing local SSH alias only; do not change reverse tunnel, cloud tunnel user, SSH config, security group, or public tunnel port.
- Active remote project root: `USER_HOME/projects/openclaw`.
- Dashboard listens on remote loopback `127.0.0.1:8020`; local browser access has typically used a local forward such as `127.0.0.1:8130`.
- PostgreSQL listens on remote loopback `127.0.0.1:55432`.
- Current development focus: Forecast Model Knowledge Graph / PRE_EXECUTION_EVIDENCE_SCORE, not real model execution.
- Do not run training, fit/predict, rolling-origin backtest, forecast generation, promotion, trusted ingestion, or active pointer updates unless explicitly authorized in a new task.

## Current high-level state

The R3A preexecution scoring layer has been implemented partially and deployed. Login was repaired by regenerating the Dashboard password hash from the user-authorized local credential file. R3A API endpoints now authenticate and return data. The latest R3A report remains `IMPLEMENTATION_INCOMPLETE` because full 263 metadata / 40 PDF accounting, >=40 targeted tests, DB/API/DOM browser matrix, and 12 PNG screenshot matrix were not completed yet.
