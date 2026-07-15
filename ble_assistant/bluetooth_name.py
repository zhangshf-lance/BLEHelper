from __future__ import annotations

import subprocess
import winreg
from dataclasses import dataclass


BLUETOOTH_CLASS_GUID = "{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}"
ENUM_ROOT = r"SYSTEM\CurrentControlSet\Enum"


@dataclass(frozen=True)
class BluetoothNameResult:
    changed: bool
    message: str


@dataclass(frozen=True)
class BluetoothAdapter:
    instance_id: str
    registry_path: str
    description: str


def set_system_bluetooth_name(name: str) -> BluetoothNameResult:
    clean_name = name.strip()
    if not clean_name:
        return BluetoothNameResult(False, "系统蓝牙名称为空，已跳过覆盖")

    adapters = list_bluetooth_adapters()
    if not adapters:
        return BluetoothNameResult(False, "未找到可覆盖名称的 Windows 蓝牙适配器")

    failures: list[str] = []
    for adapter in adapters:
        try:
            _write_local_name(adapter.registry_path, clean_name)
        except PermissionError:
            failures.append(f"{adapter.instance_id}: 权限不足，请用 run_admin.bat 或 run_exe_admin.bat 启动")
            continue
        except OSError as exc:
            failures.append(f"{adapter.instance_id}: 写入失败 {exc}")
            continue

        restart_message = _restart_device(adapter.instance_id)
        return BluetoothNameResult(
            True,
            f"已写入系统蓝牙名称：{clean_name}（{adapter.instance_id}）；{restart_message}",
        )

    return BluetoothNameResult(False, "；".join(failures) if failures else "系统蓝牙名称覆盖失败")


def list_bluetooth_adapters() -> list[BluetoothAdapter]:
    adapters: list[BluetoothAdapter] = []
    _walk_enum_tree(ENUM_ROOT, [], adapters)
    return sorted(adapters, key=_adapter_priority)


def _walk_enum_tree(path: str, parts: list[str], adapters: list[BluetoothAdapter]) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            if _is_bluetooth_adapter(key, parts):
                adapters.append(
                    BluetoothAdapter(
                        instance_id="\\".join(parts),
                        registry_path=path,
                        description=_query_string(key, "DeviceDesc") or _query_string(key, "FriendlyName"),
                    )
                )
                return

            index = 0
            while True:
                try:
                    child = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                if len(parts) < 3:
                    _walk_enum_tree(f"{path}\\{child}", [*parts, child], adapters)
    except OSError:
        return


def _is_bluetooth_adapter(key, parts: list[str]) -> bool:
    if len(parts) != 3:
        return False
    try:
        class_guid, _ = winreg.QueryValueEx(key, "ClassGUID")
    except OSError:
        return False
    if str(class_guid).lower() != BLUETOOTH_CLASS_GUID:
        return False
    instance_id = "\\".join(parts).upper()
    return instance_id.startswith("BTH\\MS_BTHBRB\\") or instance_id.startswith("USB\\")


def _query_string(key, value_name: str) -> str:
    try:
        value, _ = winreg.QueryValueEx(key, value_name)
    except OSError:
        return ""
    return str(value)


def _adapter_priority(adapter: BluetoothAdapter) -> tuple[int, str]:
    instance_id = adapter.instance_id.upper()
    if instance_id.startswith("BTH\\MS_BTHBRB\\"):
        return (0, adapter.instance_id)
    if instance_id.startswith("USB\\"):
        return (1, adapter.instance_id)
    return (2, adapter.instance_id)


def _write_local_name(adapter_registry_path: str, name: str) -> None:
    parameters_path = f"{adapter_registry_path}\\Device Parameters"
    with winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE,
        parameters_path,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "Local Name", 0, winreg.REG_BINARY, name.encode("utf-8"))


def _restart_device(instance_id: str) -> str:
    try:
        completed = subprocess.run(
            ["pnputil", "/restart-device", instance_id],
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except FileNotFoundError:
        return "未找到 pnputil，名称可能需要手动重启蓝牙后生效"
    except subprocess.TimeoutExpired:
        return "重启蓝牙适配器超时，名称可能稍后或手动重启蓝牙后生效"

    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode == 0:
        return "已请求重启蓝牙适配器"
    if output:
        return f"重启蓝牙适配器失败：{output}"
    return f"重启蓝牙适配器失败，退出码 {completed.returncode}"
