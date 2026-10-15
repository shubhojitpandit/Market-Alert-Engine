import sys
from pathlib import Path

from notifications.notification_handler import NotificationHandler
from notifications.sound_settings import NotificationSoundSettings


def test_bundled_sound_is_available_without_imported_sounds(tmp_path) -> None:
    settings = NotificationSoundSettings(tmp_path / "settings")

    assert [(choice.sound_id, choice.name) for choice in settings.list_sounds()] == [
        ("system", "Windows Default"),
        ("bundled_custom_alert", "Alert tone 1"),
    ]
    assert settings.list_sounds()[1].path.is_file()
    assert not (tmp_path / "settings" / "sounds").exists()


def test_bundled_sound_path_supports_source_and_frozen_execution(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    source_path = NotificationSoundSettings.custom_sound_path()
    assert source_path == (
        Path(__file__).resolve().parent.parent
        / "assets"
        / "sounds"
        / "custom_alert.mp3"
    )

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert NotificationSoundSettings.custom_sound_path() == (
        tmp_path / "assets" / "sounds" / "custom_alert.mp3"
    )


def test_bundled_sound_uses_mp3_player_and_once_twice_settings(
    tmp_path, monkeypatch
) -> None:
    settings = NotificationSoundSettings(tmp_path / "settings")
    settings.select(settings.CUSTOM_SOUND_ID)
    handler = NotificationHandler(settings)
    played: list = []
    monkeypatch.setattr(handler, "_play_mp3", played.append)

    handler.play_current_sound()
    handler._play_sound(None)
    settings.set_playback("Twice")
    handler.play_current_sound()

    asset = settings.custom_sound_path()
    assert played == [asset, asset, asset, asset]
    assert settings.playback_target() == (asset, 2)
    assert NotificationSoundSettings(tmp_path / "settings").selected_sound_id() == (
        settings.CUSTOM_SOUND_ID
    )


def test_imported_sound_library_remains_unchanged(tmp_path) -> None:
    settings = NotificationSoundSettings(tmp_path / "settings")
    source = tmp_path / "my_sound.mp3"
    source.write_bytes(b"imported mp3")

    imported = settings.import_sound(source)

    assert imported.name == "my_sound"
    assert imported.path is not None
    assert imported.path.parent == tmp_path / "settings" / "sounds"
    assert imported.path.read_bytes() == source.read_bytes()
    assert {choice.sound_id for choice in settings.list_sounds()} == {
        settings.DEFAULT_SOUND_ID,
        settings.CUSTOM_SOUND_ID,
        imported.sound_id,
    }
