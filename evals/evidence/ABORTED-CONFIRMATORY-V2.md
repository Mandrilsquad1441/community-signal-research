# Aborted confirmatory attempt v2

This record discloses an operator interruption without treating it as behavioral evidence.

The same facts are available in the [machine-readable incident record](aborted-confirmatory-v2.json).

## Disposition

`community-signal-confirmatory-v2-a1900773694` is **ineligible and unscored**. The host began shutting down before any trial completed. The frozen directory is preserved as an aborted first attempt; no response was repaired, retried, resumed, selected, blinded, or scored.

This event says nothing about the baseline or skill condition. A later confirmatory attempt must use a new committed operator, new paths, and fresh domain-separated seeds.

## Frozen identity

- Repository commit: `3d2b5ff3d4e1bcf65dc95260bfaeb1aace879352`
- Preregistered: `2026-08-31T00:31:50.5347861Z`
- Allocation seed: `1900773694`
- Blinding seed: `160678537`
- Bootstrap seed: `376096950`
- Planned design: 12 cases, two conditions, five replicates, 120 trials
- Operator: Codex CLI `0.151.0-alpha.7.2`, `gpt-5.4-mini`, low reasoning, four foreground workers, no trial retries

## Observed boundary

- All 120 trial directories were prepared.
- Four trials wrote their prompt, start record, stdout file, and stderr file.
- Those four starts occurred at approximately `2026-08-31T00:34:21.936Z`.
- Zero trials wrote a final execution record.
- Zero trials produced a preserved raw response.
- No operator summary exists.
- Blinding and scoring never began.

Windows session logs record the first explicit shutdown event at `2026-08-31T00:34:32.9969390Z`—approximately 11.1 seconds after the four starts. `Microsoft-Windows-TerminalServices-LocalSessionManager/Operational` event 54, record 75, says the session manager received a system-shutdown message. The later shutdown chain includes session-logoff, Event Log service-stop, Kernel-Power shutdown/reboot-transition, and Kernel-General OS-shutdown events through `2026-08-31T00:35:25.4883716Z`. All four ephemeral per-trial working directories remained on disk, which is consistent with abrupt process termination rather than the operator's normal cleanup path. No Python or Codex application-crash event was found in the inspected interval.

| UTC | Provider/log | Event ID | Record | Public-safe interpretation |
| --- | --- | ---: | ---: | --- |
| `00:34:32.9969390Z` | TerminalServices LocalSessionManager / Operational | 54 | 75 | System-shutdown message received |
| `00:34:55.8938344Z` | TerminalServices LocalSessionManager / Operational | 23 | 76 | Session logoff succeeded; user identity omitted |
| `00:34:55.9032160Z` | Winlogon / System | 7002 | 1517 | User-logoff notification |
| `00:35:20.0634495Z` | EventLog / System | 6006 | 1519 | Event Log service stopped |
| `00:35:25.0139003Z` | Kernel-Power / System | 109 | 1539 | Kernel API shutdown transition |
| `00:35:25.3675736Z` | Kernel-Power / System | 577 | 1540 | System-initiated reboot preparation |
| `00:35:25.4883716Z` | Kernel-General / System | 13 | 1541 | Operating system shutting down |

## Preserved-tree fingerprints

The post-incident, read-only forensic inventory recorded:

| Tree | Files | Bytes | SHA-256 tree fingerprint |
| --- | ---: | ---: | --- |
| Prepared run | 799 | 5,599,275 | `17e20822b83b13a630f0d521c4957d86b504f35aa218962d14aa5f2a636bd361` |
| Preregistration/audit | 7 | 22,282 | `7f400fcd0f6cd7e7777cd31ea3b1429ffac3000753223b3931193128db2beaf4` |
| Passed smoke run | 6 | 10,254 | `72cde16fb2bdc40b39c33a41ad1d5137e49a27852937d78e8304fd3833f4094d` |

The fingerprint is SHA-256 over a no-BOM UTF-8 manifest. Each recursively discovered regular file contributes `relative/path<TAB>byte_length<TAB>lowercase_file_sha256<LF>`; paths use `/` and records are ordered by PowerShell `Sort-Object FullName`. The final line also has LF. Directories, timestamps, ACLs, and the root path/name are excluded.

The preserved trees are intentionally not modified to add an abort marker retroactively. The subsequent operator adds an exclusive, durable `operator-abort.json` on catchable interruptions; the absence of that marker cannot cover a host power-off, so a config without a complete summary remains a hard ineligibility sentinel.

## Recovery rule

The next allocation, blinding, and bootstrap seeds are derived independently from a domain-separated SHA-256 construction that includes the new commit and the aborted run-tree fingerprint. This prevents reuse while keeping the derivation auditable. The successor must also run directly in one foreground terminal session with bounded in-flight submissions and visible heartbeats.
