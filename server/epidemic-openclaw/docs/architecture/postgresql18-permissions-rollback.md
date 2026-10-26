# PostgreSQL path permission rollback

Method used: Fallback mode change on /root: 701 -> 701.

Rollback: After relocating PostgreSQL and stopping it, restore `/root` mode with `chmod 701 /root`.
