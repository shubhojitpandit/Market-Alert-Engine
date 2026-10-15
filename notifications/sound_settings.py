"""Persist imported alert sounds and playback preferences locally."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path


class SoundSettingsError(RuntimeError):
    """Raised when an alert sound cannot be imported or configured."""


@dataclass(frozen=True)
class SoundChoice:
    sound_id: str
    name: str
    path: Path | None


class NotificationSoundSettings:
    """Store sound metadata under the user's local application data directory."""

    DEFAULT_SOUND_ID = "system"
    DEFAULT_SOUND_NAME = "Windows Default"
    CUSTOM_SOUND_ID = "bundled_custom_alert"
    CUSTOM_SOUND_NAME = "Alert tone 1"
    CUSTOM_SOUND_FILENAME = "custom_alert.mp3"

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or self.default_root()
        self._sounds_dir = self._root / "sounds"
        self._config_path = self._root / "notification_sounds.json"
        self._lock = threading.RLock()
        self._sounds: dict[str, str] = {}
        self._selected = self.DEFAULT_SOUND_ID
        self._playback = "Once"
        self._load()

    @staticmethod
    def default_root() -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "MarketAlertEngine"
        return Path.home() / "AppData" / "Local" / "MarketAlertEngine"

    def list_sounds(self) -> list[SoundChoice]:
        with self._lock:
            choices = [
                SoundChoice(
                    self.DEFAULT_SOUND_ID, self.DEFAULT_SOUND_NAME, None
                ),
                SoundChoice(
                    self.CUSTOM_SOUND_ID,
                    self.CUSTOM_SOUND_NAME,
                    self.custom_sound_path(),
                ),
            ]
            choices.extend(
                SoundChoice(sound_id, name, self._sounds_dir / sound_id)
                for sound_id, name in self._sounds.items()
            )
            return choices

    def selected_sound_id(self) -> str:
        with self._lock:
            return self._selected

    @classmethod
    def custom_sound_path(cls) -> Path:
        """Locate the bundled sound in source and PyInstaller executions."""
        bundle_root = getattr(sys, "_MEIPASS", None)
        root = (
            Path(bundle_root)
            if bundle_root is not None
            else Path(__file__).resolve().parent.parent
        )
        return root / "assets" / "sounds" / cls.CUSTOM_SOUND_FILENAME

    def playback(self) -> str:
        with self._lock:
            return self._playback

    def select(self, sound_id: str) -> None:
        with self._lock:
            if sound_id not in {
                self.DEFAULT_SOUND_ID,
                self.CUSTOM_SOUND_ID,
            } and sound_id not in self._sounds:
                raise SoundSettingsError("That alert sound is not available.")
            if sound_id == self.CUSTOM_SOUND_ID:
                if not self.custom_sound_path().is_file():
                    raise SoundSettingsError(
                        "The bundled alert sound file is missing."
                    )
            elif sound_id != self.DEFAULT_SOUND_ID:
                if not (self._sounds_dir / sound_id).is_file():
                    raise SoundSettingsError(
                        "The saved sound file is missing. Import it again."
                    )
            previous = self._selected
            self._selected = sound_id
            try:
                self._save()
            except SoundSettingsError:
                self._selected = previous
                raise

    def set_playback(self, playback: str) -> None:
        if playback not in {"Once", "Twice"}:
            raise ValueError("Playback must be Once or Twice.")
        with self._lock:
            previous = self._playback
            self._playback = playback
            try:
                self._save()
            except SoundSettingsError:
                self._playback = previous
                raise

    def playback_target(self) -> tuple[Path | None, int]:
        with self._lock:
            path = (
                None
                if self._selected == self.DEFAULT_SOUND_ID
                else (
                    self.custom_sound_path()
                    if self._selected == self.CUSTOM_SOUND_ID
                    else self._sounds_dir / self._selected
                )
            )
            if path is not None and not path.is_file():
                if self._selected == self.CUSTOM_SOUND_ID:
                    raise SoundSettingsError(
                        "The bundled alert sound file is missing."
                    )
                raise SoundSettingsError(
                    "The saved sound file is missing. Import it again."
                )
            return path, 1 if self._playback == "Once" else 2

    def import_sound(self, source: Path) -> SoundChoice:
        suffix = source.suffix.casefold()
        if suffix not in {".wav", ".mp3"}:
            raise SoundSettingsError("Select a WAV or MP3 sound file.")
        if suffix == ".wav":
            try:
                with wave.open(str(source), "rb") as wav_file:
                    wav_file.getparams()
            except (EOFError, OSError, wave.Error):
                raise SoundSettingsError("Select a valid WAV sound file.") from None

        with self._lock:
            sound_id = f"{uuid.uuid4().hex}{suffix}"
            name = source.stem.strip() or "Custom sound"
            existing_names = {value.casefold() for value in self._sounds.values()}
            if name.casefold() in existing_names:
                name = f"{name} (2)"
                suffix_number = 2
                while name.casefold() in existing_names:
                    suffix_number += 1
                    name = (
                        f"{source.stem.strip() or 'Custom sound'} "
                        f"({suffix_number})"
                    )

            destination = self._sounds_dir / sound_id
            try:
                self._sounds_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            except OSError:
                raise SoundSettingsError(
                    "Could not copy the selected sound file."
                ) from None

            previous_selected = self._selected
            self._sounds[sound_id] = name
            self._selected = sound_id
            try:
                self._save()
            except SoundSettingsError:
                self._sounds.pop(sound_id, None)
                self._selected = previous_selected
                try:
                    destination.unlink()
                except OSError:
                    pass
                raise
            return SoundChoice(sound_id, name, destination)

    def import_wav(self, source: Path) -> SoundChoice:
        """Compatibility wrapper for existing WAV sound imports."""
        return self.import_sound(source)

    def _load(self) -> None:
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            raise SoundSettingsError(
                "Could not read saved alert sound settings."
            ) from None
        if not isinstance(raw, dict):
            raise SoundSettingsError("Saved alert sound settings are invalid.")
        sounds = raw.get("sounds", [])
        selected = raw.get("selected", self.DEFAULT_SOUND_ID)
        playback = raw.get("playback", "Once")
        if (
            not isinstance(sounds, list)
            or not isinstance(selected, str)
            or not isinstance(playback, str)
            or playback not in {"Once", "Twice"}
        ):
            raise SoundSettingsError("Saved alert sound settings are invalid.")
        for item in sounds:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("file"), str)
                or Path(item["file"]).name != item["file"]
                or Path(item["file"]).suffix.casefold() not in {".wav", ".mp3"}
                or not isinstance(item.get("name"), str)
            ):
                raise SoundSettingsError("Saved alert sound settings are invalid.")
            self._sounds[item["file"]] = item["name"]
        if selected not in {
            self.DEFAULT_SOUND_ID,
            self.CUSTOM_SOUND_ID,
        } and selected not in self._sounds:
            selected = self.DEFAULT_SOUND_ID
        self._selected = selected
        self._playback = playback

    def _save(self) -> None:
        payload = json.dumps(
            {
                "sounds": [
                    {"file": sound_id, "name": name}
                    for sound_id, name in self._sounds.items()
                ],
                "selected": self._selected,
                "playback": self._playback,
            },
            indent=2,
        )
        temporary_path: Path | None = None
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._root,
                delete=False,
            ) as temporary_file:
                temporary_file.write(payload)
                temporary_path = Path(temporary_file.name)
            temporary_path.replace(self._config_path)
        except OSError:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise SoundSettingsError(
                "Could not save alert sound settings."
            ) from None
