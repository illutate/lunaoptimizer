### Luna System Core

Advanced Windows Optimization Suite for Windows 11.

## Design

Luna System Core is intentionally not a generic debloat script. The runtime model is:

`DETECT -> BACKUP -> APPLY -> VERIFY -> LOG -> COMMIT`

A tweak is considered applicable only when the current Windows build and the required object are present. Missing tasks, registry values, services, or unsupported mechanisms are reported as **NOT APPLICABLE** rather than created or simulated.

## Supported Windows target

The runtime accepts Windows 11 builds from the requested 21H2-era baseline onward and performs build-aware checks. The release catalog is not treated as a promise that every historical or future tweak exists on every build.

The application also detects newer/unknown Windows 11 builds and treats them as a runtime-detected environment rather than assuming an old registry/task layout.

## Included capabilities

- Administrator/UAC detection and elevation.
- Windows version/build/edition/architecture detection.
- Secure Boot and VBS/HVCI state detection.
- CPU, RAM, GPU, storage and network inventory.
- Wi-Fi, Bluetooth, VPN-like adapter, virtual adapter, Store, Xbox, Insider, domain-join and Hyper-V detection.
- Real Scheduled Task inventory through `schtasks.exe`.
- Exact-task disabling with backup, verification and rollback.
- Registry value backup/change/verify/restore for explicit advanced tweaks.
- Service inventory and guarded startup-mode editing.
- Device inventory through `pnputil.exe` and high-impact device enable/disable controls.
- Granular backup sessions with manifests, registry values, service state, task XML and device/network snapshots.
- System Restore Point attempt before optimization.
- Cooperative cancellation and rollback of verified changes from the active session.
- Diagnostics snapshots before/after optimization.
- Explicitly unavailable DPC metric when no native sampler is present; no fabricated latency values.
- LatencyMon executable detection and launch; no invented LatencyMon API parser.
- Synthetic Input Dispatch Latency test using Qt event delivery and `QueryPerformanceCounter` on Windows.
- HTML + JSON optimization reports.
- Dark technical Qt6 UI with animated starfield and compact solar system.
- PyInstaller production build files.

## Quick mode

Quick mode only selects tweaks explicitly marked `SAFE` or `LOW` risk. Medium/high/critical changes remain outside the automatic path.

## Deep mode

Deep Manual exposes advanced scheduler/network/task/service controls. Every selected change is shown with risk, applicability, current state, target state and backup availability.

## Runtime data location

By default:

`%LOCALAPPDATA%\\LunaSystemCore`

with subdirectories:

- `logs`
- `backups`
- `reports`
- `diagnostics`

For a portable layout next to the project/executable, launch with `LUNA_PORTABLE=1`.

## Run from source

1. Use Python 3.12+.
2. Install dependencies:

```text
python -m pip install -r requirements.txt
```

3. Run:

```text
python main.py
```

The entry point requires Windows 11 and requests UAC elevation automatically.

## Build EXE

Run:

```text
build.bat
```

or:

```powershell
powershell -ExecutionPolicy Bypass -File .\\build.ps1
```

The PyInstaller build is windowed and uses the bundled Luna SVG icon.

## Safety boundaries

The automatic optimization catalog does not target core security or servicing infrastructure. In particular, it does not automatically disable Defender, Firewall, Secure Boot, TPM, LSASS protections, Plug and Play, RPC/DCOM, Event Log, Windows Update core servicing, or storage stack components.

The application does not claim that a scheduler/network tweak universally improves FPS or latency. Reports distinguish measured state changes from causal performance conclusions.
