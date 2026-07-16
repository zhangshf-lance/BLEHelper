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
class WifiNetwork:
    ssid: str
    authentication: str = ""
    encryption: str = ""
    signal: str = ""
    bssid_count: int = 0


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

    def station_connect(self, ssid: str, password: str = "") -> str:
        ssid = ssid.strip()
        password = password.strip()
        if not ssid:
            raise ValueError("STATION SSID 不能为空")
        output = ""
        if password:
            if not 8 <= len(password) <= 63:
                raise ValueError("STATION 密码长度必须为 8-63 个字符")
            output = self._add_wpa2_profile(ssid, password)
        connected = self._run("wlan", "connect", f"name={ssid}", f"ssid={ssid}")
        return self._join_outputs(output, connected)

    def station_disconnect(self) -> str:
        return self._run("wlan", "disconnect")

    def station_status(self) -> str:
        return self._run("wlan", "show", "interfaces")

    def _add_wpa2_profile(self, ssid: str, password: str) -> str:
        profile = self._wpa2_profile(ssid, password)
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

    def _wpa2_profile(self, ssid: str, password: str) -> str:
        ssid_xml = html.escape(ssid, quote=True)
        password_xml = html.escape(password, quote=True)
        return f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid_xml}</name>
  <SSIDConfig>
    <SSID>
      <name>{ssid_xml}</name>
    </SSID>
  </SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM>
    <security>
      <authEncryption>
        <authentication>WPA2PSK</authentication>
        <encryption>AES</encryption>
        <useOneX>false</useOneX>
      </authEncryption>
      <sharedKey>
        <keyType>passPhrase</keyType>
        <protected>false</protected>
        <keyMaterial>{password_xml}</keyMaterial>
      </sharedKey>
    </security>
  </MSM>
</WLANProfile>
"""

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
        for raw_line in output.splitlines():
            line = raw_line.strip()
            ssid_match = re.match(r"^SSID\s+\d+\s*:\s*(.*)$", line, re.IGNORECASE)
            if ssid_match:
                if current and current.get("ssid"):
                    networks.append(self._network_from_dict(current))
                current = {"ssid": ssid_match.group(1).strip(), "bssid_count": 0}
                continue
            if current is None:
                continue
            key_value = re.match(r"^([^:：]+)\s*[:：]\s*(.*)$", line)
            if not key_value:
                continue
            key = key_value.group(1).strip().lower()
            value = key_value.group(2).strip()
            if key in ("authentication", "身份验证"):
                current["authentication"] = value
            elif key in ("encryption", "加密"):
                current["encryption"] = value
            elif key in ("signal", "信号"):
                current["signal"] = value
            elif key.startswith("bssid"):
                current["bssid_count"] = int(current.get("bssid_count", 0)) + 1
        if current and current.get("ssid"):
            networks.append(self._network_from_dict(current))
        return networks

    def _network_from_dict(self, data: dict[str, object]) -> WifiNetwork:
        return WifiNetwork(
            ssid=str(data.get("ssid", "")),
            authentication=str(data.get("authentication", "")),
            encryption=str(data.get("encryption", "")),
            signal=str(data.get("signal", "")),
            bssid_count=int(data.get("bssid_count", 0)),
        )

    def _join_outputs(self, *outputs: str) -> str:
        return "\n\n".join(output for output in outputs if output)
