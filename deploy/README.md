# deploy/ — phase 2 units land here

Placeholder per plan §2. When the phase-2 work items land, this directory
carries (modeled on `fleet-cns-v3/deploy/fleet-cns-v3.service`, the
canonical hardened user unit — `Restart=always`, `RestartSec=3`,
`MemoryMax`, full sandbox block, `WantedBy=default.target`):

- `fleet-doctor.service` + `fleet-doctor.timer` (5 min) — runs
  `fleet-doctor.py`, writes status JSON
- optional `field-score.service` if the scorer becomes a daemon rather
  than a demo.sh child

Install pattern (three lines, per plan §2.1):

    cp deploy/*.service deploy/*.timer ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now fleet-doctor.timer
