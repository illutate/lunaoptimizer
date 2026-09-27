from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from core.system import (
    is_windows,
    reg_query,
    run_command,
    service_state,
    task_query_xml,
)


@dataclass(slots=True)
class BackupSession:
    backup_id: str
    created_at: str
    root: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def registry_dir(self) -> Path:
        return self.root / "registry"

    @property
    def services_dir(self) -> Path:
        return self.root / "services"

    @property
    def tasks_dir(self) -> Path:
        return self.root / "tasks"

    @property
    def devices_dir(self) -> Path:
        return self.root / "devices"

    @property
    def network_dir(self) -> Path:
        return self.root / "network"

    @property
    def metadata_path(self) -> Path:
        return self.root / "metadata.json"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def save(self) -> None:
        self.manifest_path.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.metadata_path.write_text(
            json.dumps({"backup_id": self.backup_id, "created_at": self.created_at}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class BackupManager:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.backups_dir = base_dir / "backups"
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.current: BackupSession | None = None

    def create_session(self, system_snapshot: dict[str, Any]) -> BackupSession:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_id = f"LunaOptimization_{stamp}_{uuid.uuid4().hex[:6]}"
        root = self.backups_dir / backup_id
        for child in ("registry", "services", "tasks", "devices", "network"):
            (root / child).mkdir(parents=True, exist_ok=True)
        session = BackupSession(
            backup_id=backup_id,
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            root=root,
            manifest={
                "schema": 1,
                "app": "Luna System Core",
                "backup_id": backup_id,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "system": system_snapshot,
                "registry": [],
                "services": [],
                "tasks": [],
                "devices": [],
                "network": [],
                "applied_tweaks": [],
                "notes": [],
            },
        )
        session.save()
        self.current = session
        return session

    def _require(self) -> BackupSession:
        if not self.current:
            raise RuntimeError("No active backup session")
        return self.current

    def _add_manifest_item(self, key: str, item: dict[str, Any]) -> None:
        session = self._require()
        session.manifest.setdefault(key, []).append(item)
        session.save()

    def record_tweak_application(self, tweak_id: str, status: str) -> None:
        session = self._require()
        session.manifest.setdefault("applied_tweaks", []).append(
            {"id": tweak_id, "status": status, "time": datetime.now().astimezone().isoformat(timespec="seconds")}
        )
        session.save()

    def note(self, text: str) -> None:
        session = self._require()
        session.manifest.setdefault("notes", []).append(
            {"time": datetime.now().astimezone().isoformat(timespec="seconds"), "text": text}
        )
        session.save()

    def backup_registry_value(self, path: str, value_name: str) -> dict[str, Any]:
        session = self._require()
        marker = {"path": path, "value_name": value_name, "exists": False, "type": None, "value": None}
        try:
            import winreg
            hive = winreg.HKEY_LOCAL_MACHINE if path.upper().startswith("HKLM\\") else winreg.HKEY_CURRENT_USER
            subkey = path[5:] if path.upper().startswith(("HKLM\\", "HKCU\\")) else path
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                value, typ = winreg.QueryValueEx(key, value_name)
                marker.update({"exists": True, "type": typ, "value": value})
        except (FileNotFoundError, OSError, ImportError):
            pass
        index = len(session.manifest["registry"])
        payload = session.registry_dir / f"{index:04d}.json"
        payload.write_text(json.dumps(marker, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self._add_manifest_item("registry", {**marker, "file": str(payload.relative_to(session.root))})
        return marker

    def backup_registry_key_export(self, path: str) -> Path | None:
        session = self._require()
        safe_name = path.replace("\\", "_").replace(":", "")[:180]
        out_file = session.registry_dir / f"key_{safe_name}.reg"
        if not is_windows():
            return None
        code, _, err = run_command(["reg.exe", "export", path, str(out_file), "/y"], 20)
        if code != 0:
            self.note(f"Registry export failed for {path}: {err}")
            return None
        self._add_manifest_item("registry", {"path": path, "export": str(out_file.relative_to(session.root))})
        return out_file

    def backup_service(self, name: str) -> dict[str, Any] | None:
        session = self._require()
        state = service_state(name)
        if not state:
            return None
        file_name = f"{len(session.manifest['services']):04d}_{name}.json"
        path = session.services_dir / file_name
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        self._add_manifest_item("services", {**state, "file": str(path.relative_to(session.root))})
        return state

    def backup_task(self, task_path: str) -> dict[str, Any] | None:
        session = self._require()
        xml = task_query_xml(task_path)
        if xml is None:
            return None
        safe = task_path.strip("\\").replace("\\", "_").replace("/", "_")
        file_name = f"{len(session.manifest['tasks']):04d}_{safe[:180]}.xml"
        path = session.tasks_dir / file_name
        path.write_text(xml, encoding="utf-8")
        item = {"path": task_path, "xml": str(path.relative_to(session.root))}
        self._add_manifest_item("tasks", item)
        return item

    def backup_device_snapshot(self, devices: list[dict[str, Any]]) -> Path:
        session = self._require()
        path = session.devices_dir / "devices.json"
        path.write_text(json.dumps(devices, ensure_ascii=False, indent=2), encoding="utf-8")
        self._add_manifest_item("devices", {"file": str(path.relative_to(session.root)), "count": len(devices)})
        return path

    def backup_network_snapshot(self, adapters: list[dict[str, Any]]) -> Path:
        session = self._require()
        path = session.network_dir / "adapters.json"
        path.write_text(json.dumps(adapters, ensure_ascii=False, indent=2), encoding="utf-8")
        self._add_manifest_item("network", {"file": str(path.relative_to(session.root)), "count": len(adapters)})
        return path

    def restore_registry_value(self, marker: dict[str, Any]) -> tuple[bool, str]:
        if not is_windows():
            return False, "Windows required"
        import winreg
        path = marker["path"]
        hive = winreg.HKEY_LOCAL_MACHINE if path.upper().startswith("HKLM\\") else winreg.HKEY_CURRENT_USER
        subkey = path[5:] if path.upper().startswith(("HKLM\\", "HKCU\\")) else path
        try:
            with winreg.CreateKeyEx(hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
                if marker.get("exists"):
                    key.SetValueEx(marker["value_name"], 0, int(marker["type"]), marker["value"])
                else:
                    try:
                        key.DeleteValue(marker["value_name"])
                    except FileNotFoundError:
                        pass
            return True, "Registry value restored"
        except (OSError, ValueError, TypeError) as exc:
            return False, str(exc)

    def restore_task(self, item: dict[str, Any]) -> tuple[bool, str]:
        session = self._require()
        path = session.root / item["xml"]
        if not path.exists():
            return False, "Task XML backup missing"
        code, out, err = run_command(["schtasks.exe", "/Create", "/TN", item["path"], "/XML", str(path), "/F"], 25)
        return code == 0, out or err

    def list_sessions(self) -> list[BackupSession]:
        result: list[BackupSession] = []
        for folder in sorted(self.backups_dir.iterdir(), reverse=True):
            if not folder.is_dir():
                continue
            manifest_path = folder / "manifest.json"
            metadata_path = folder / "metadata.json"
            if not manifest_path.exists() or not metadata_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                result.append(BackupSession(str(metadata["backup_id"]), str(metadata["created_at"]), folder, manifest))
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                continue
        return result
