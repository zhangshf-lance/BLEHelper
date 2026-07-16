from __future__ import annotations

import html
import locale
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class WifiBssid:
    address: str
    signal: str = ""
    radio_type: str = ""
    channel: str = ""
    basic_rates: str = ""
    other_rates: str = ""
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    authentication: str = ""
    encryption: str = ""
    signal: str = ""
    network_type: str = ""
    bssid_count: int = 0
    bssids: tuple[WifiBssid, ...] = ()
    details: tuple[tuple[str, str], ...] = ()


class WifiManager:
    def hostap_start(self, ssid: str, password: str) -> str:
        ssid = ssid.strip()
        password = password.strip()
        if not ssid:
            raise ValueError("HOSTAP SSID 不能为空")
        if not 8 <= len(password) <= 63:
            raise ValueError("HOSTAP 密码长度必须为 8-63 个字符")
        config = self._run(
            "wlan",
            "set",
            "hostednetwork",
            "mode=allow",
            f"ssid={ssid}",
            f"key={password}",
        )
        started = self._run("wlan", "start", "hostednetwork")
        return self._join_outputs(config, started)

    def hostap_stop(self) -> str:
        return self._run("wlan", "stop", "hostednetwork")

    def hostap_status(self) -> str:
        return self._run("wlan", "show", "hostednetwork")

    def station_scan(self) -> tuple[list[WifiNetwork], str]:
        output = self._run("wlan", "show", "networks", "mode=bssid")
        return self._parse_networks(output), output

    def station_connect(
        self,
        ssid: str,
        password: str = "",
        network: WifiNetwork | None = None,
    ) -> str:
        ssid = ssid.strip()
        password = password.strip()
        if not ssid:
            raise ValueError("STATION SSID 不能为空")
        authentication = network.authentication if network is not None else ""
        encryption = network.encryption if network is not None else ""
        outputs: list[str] = []
        if password or self._is_open_network(authentication):
            if password and not 8 <= len(password) <= 63:
                raise ValueError("STATION 密码长度必须为 8-63 个字符")
            try:
                outputs.append(self._add_profile(ssid, password, authentication, encryption))
            except RuntimeError as exc:
                if password and "wpa3" in authentication.casefold():
                    outputs.append(
                        f"WPA3 profile failed, retrying as WPA2PSK/AES:\n{exc}"
                    )
                    outputs.append(
                        self._add_profile(ssid, password, "WPA2-Personal", encryption)
                    )
                else:
                    raise
        connected = self._run("wlan", "connect", f"name={ssid}", f"ssid={ssid}")
        outputs.append(connected)
        return self._join_outputs(*outputs)

    def station_disconnect(self) -> str:
        return self._run("wlan", "disconnect")

    def station_status(self) -> str:
        return self._run("wlan", "show", "interfaces")

    def format_network_details(self, network: WifiNetwork) -> str:
        lines = [
            f"SSID: {network.ssid}",
            f"Authentication: {network.authentication or '-'}",
            f"Encryption: {network.encryption or '-'}",
            f"Network type: {network.network_type or '-'}",
            f"Best signal: {network.signal or '-'}",
            f"BSSID count: {network.bssid_count}",
        ]
        extra = [
            (key, value)
            for key, value in network.details
            if key not in {"Authentication", "Encryption", "Network type"}
        ]
        if extra:
            lines.append("")
            lines.append("Network details:")
            lines.extend(f"- {key}: {value}" for key, value in extra)
        if network.bssids:
            lines.append("")
            lines.append("BSSID details:")
            for index, bssid in enumerate(network.bssids, start=1):
                lines.append(f"[{index}] {bssid.address}")
                if bssid.signal:
                    lines.append(f"  Signal: {bssid.signal}")
                if bssid.radio_type:
                    lines.append(f"  Radio type: {bssid.radio_type}")
                if bssid.channel:
                    lines.append(f"  Channel: {bssid.channel}")
                if bssid.basic_rates:
                    lines.append(f"  Basic rates: {bssid.basic_rates}")
                if bssid.other_rates:
                    lines.append(f"  Other rates: {bssid.other_rates}")
                for key, value in bssid.details:
                    if key in {
                        "BSSID",
                        "Signal",
                        "Radio type",
                        "Channel",
                        "Basic rates",
                        "Other rates",
                    }:
                        continue
                    lines.append(f"  {key}: {value}")
        return "\n".join(lines)

    def _add_profile(
        self,
        ssid: str,
        password: str,
        authentication: str = "",
        encryption: str = "",
    ) -> str:
        profile = self._profile_xml(ssid, password, authentication, encryption)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".xml", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(profile)
            path = Path(handle.name)
        try:
            return self._run("wlan", "add", "profile", f"filename={path}", "user=current")
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    def _profile_xml(
        self,
        ssid: str,
        password: str,
        authentication: str = "",
        encryption: str = "",
    ) -> str:
        ssid_xml = html.escape(ssid, quote=True)
        ssid_hex = ssid.encode("utf-8").hex().upper()
        password_xml = html.escape(password, quote=True)
        auth_value = self._profile_authentication(authentication, password)
        encryption_value = self._profile_encryption(encryption, auth_value)
        shared_key = ""
        if password:
            shared_key = f"""
      <sharedKey>
        <keyType>passPhrase</keyType>
        <protected>false</protected>
        <keyMaterial>{password_xml}</keyMaterial>
      </sharedKey>"""
        return f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid_xml}</name>
  <SSIDConfig>
    <SSID>
      <hex>{ssid_hex}</hex>
      <name>{ssid_xml}</name>
    </SSID>
  </SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM>
    <security>
      <authEncryption>
        <authentication>{auth_value}</authentication>
        <encryption>{encryption_value}</encryption>
        <useOneX>false</useOneX>
      </authEncryption>{shared_key}
    </security>
  </MSM>
</WLANProfile>
"""

    def _is_open_network(self, authentication: str) -> bool:
        value = authentication.casefold()
        return not value or "open" in value or "开放" in authentication

    def _profile_authentication(self, authentication: str, password: str) -> str:
        value = authentication.casefold()
        if not password or self._is_open_network(authentication):
            return "open"
        if "wpa3" in value:
            return "WPA3SAE"
        if "wpa2" in value or "rsna" in value:
            return "WPA2PSK"
        if "wpa" in value:
            return "WPAPSK"
        return "WPA2PSK"

    def _profile_encryption(self, encryption: str, authentication: str) -> str:
        value = encryption.casefold()
        if authentication == "open":
            return "none"
        if "tkip" in value:
            return "TKIP"
        if "wep" in value:
            return "WEP"
        return "AES"

    def _run(self, *args: str) -> str:
        completed = subprocess.run(
            ["netsh", *args],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=30,
        )
        output = self._decode(completed.stdout) + self._decode(completed.stderr)
        output = output.strip()
        if completed.returncode != 0:
            raise RuntimeError(output or f"netsh failed with exit code {completed.returncode}")
        return output

    def _decode(self, data: bytes) -> str:
        if not data:
            return ""
        encodings = (locale.getpreferredencoding(False), "utf-8", "gb18030", "cp936")
        for encoding in encodings:
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    def _parse_networks(self, output: str) -> list[WifiNetwork]:
        networks: list[WifiNetwork] = []
        current: dict[str, object] | None = None
        current_bssid: dict[str, object] | None = None
        for raw_line in output.splitlines():
            line = raw_line.strip()
            ssid_match = re.match(r"^SSID\s+\d+\s*:\s*(.*)$", line, re.IGNORECASE)
            if ssid_match:
                self._append_network(networks, current)
                current = {"ssid": ssid_match.group(1).strip(), "details": [], "bssids": []}
                current_bssid = None
                continue
            if current is None:
                continue
            key_value = re.match(r"^([^:：]+)\s*[:：]\s*(.*)$", line)
            if not key_value:
                continue
            key = key_value.group(1).strip()
            value = key_value.group(2).strip()
            canonical = self._canonical_key(key)
            if canonical == "BSSID":
                current_bssid = {"address": value, "details": [("BSSID", value)]}
                current["bssids"].append(current_bssid)
                continue
            target = current_bssid if current_bssid is not None and self._is_bssid_key(canonical) else current
            target.setdefault("details", []).append((canonical, value))
            if target is current:
                self._store_network_field(current, canonical, value)
            else:
                self._store_bssid_field(current_bssid, canonical, value)
        self._append_network(networks, current)
        return networks

    def _append_network(self, networks: list[WifiNetwork], data: dict[str, object] | None) -> None:
        if not data or not data.get("ssid"):
            return
        bssids = tuple(self._bssid_from_dict(item) for item in data.get("bssids", []))
        best_signal = self._best_signal([item.signal for item in bssids])
        networks.append(
            WifiNetwork(
                ssid=str(data.get("ssid", "")),
                authentication=str(data.get("authentication", "")),
                encryption=str(data.get("encryption", "")),
                signal=best_signal,
                network_type=str(data.get("network_type", "")),
                bssid_count=len(bssids),
                bssids=bssids,
                details=tuple(data.get("details", ())),
            )
        )

    def _bssid_from_dict(self, data: dict[str, object]) -> WifiBssid:
        return WifiBssid(
            address=str(data.get("address", "")),
            signal=str(data.get("signal", "")),
            radio_type=str(data.get("radio_type", "")),
            channel=str(data.get("channel", "")),
            basic_rates=str(data.get("basic_rates", "")),
            other_rates=str(data.get("other_rates", "")),
            details=tuple(data.get("details", ())),
        )

    def _canonical_key(self, key: str) -> str:
        normalized = key.strip().lower()
        mapping = {
            "authentication": "Authentication",
            "身份验证": "Authentication",
            "encryption": "Encryption",
            "加密": "Encryption",
            "network type": "Network type",
            "网络类型": "Network type",
            "signal": "Signal",
            "信号": "Signal",
            "radio type": "Radio type",
            "无线电类型": "Radio type",
            "channel": "Channel",
            "频道": "Channel",
            "basic rates (mbps)": "Basic rates",
            "基本速率(mbps)": "Basic rates",
            "other rates (mbps)": "Other rates",
            "其他速率(mbps)": "Other rates",
        }
        if normalized.startswith("bssid"):
            return "BSSID"
        return mapping.get(normalized, key.strip())

    def _is_bssid_key(self, canonical: str) -> bool:
        return canonical in {
            "Signal",
            "Radio type",
            "Channel",
            "Basic rates",
            "Other rates",
        }

    def _store_network_field(self, data: dict[str, object], key: str, value: str) -> None:
        if key == "Authentication":
            data["authentication"] = value
        elif key == "Encryption":
            data["encryption"] = value
        elif key == "Network type":
            data["network_type"] = value

    def _store_bssid_field(self, data: dict[str, object] | None, key: str, value: str) -> None:
        if data is None:
            return
        if key == "Signal":
            data["signal"] = value
        elif key == "Radio type":
            data["radio_type"] = value
        elif key == "Channel":
            data["channel"] = value
        elif key == "Basic rates":
            data["basic_rates"] = value
        elif key == "Other rates":
            data["other_rates"] = value

    def _best_signal(self, signals: list[str]) -> str:
        best = ""
        best_value = -1
        for signal in signals:
            match = re.search(r"\d+", signal)
            if not match:
                continue
            value = int(match.group(0))
            if value > best_value:
                best_value = value
                best = signal
        return best

    def _join_outputs(self, *outputs: str) -> str:
        return "\n\n".join(output for output in outputs if output)
