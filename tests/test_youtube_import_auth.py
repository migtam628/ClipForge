"""Authorized YouTube imports use isolated, disposable cookie sessions."""
import pytest
from fastapi.testclient import TestClient

from clipforge import app as module
from clipforge import import_auth
from clipforge.engine import download_video


def make_cookies(path):
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tprivate-youtube-value\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tPREF\tsecond-youtube-value\n"
        ".another-website.com\tTRUE\t/\tTRUE\t1893456000\tSESSION\tanother-secret\n",
        encoding="utf-8",
    )


def test_missing_cookie_file_is_optional(monkeypatch, tmp_path):
    monkeypatch.delenv(import_auth.COOKIE_ENV, raising=False)
    monkeypatch.setattr(import_auth, "DEFAULT_PATH", str(tmp_path / "absent.txt"))
    assert import_auth.youtube_cookie_status() == {
        "configured": False, "status": "not_configured",
    }
    with import_auth.youtube_cookie_session("https://youtu.be/abc") as cookie:
        assert cookie is None


def test_private_youtube_cookies_filtered_and_temp_removed(monkeypatch, tmp_path):
    source = tmp_path / "auth.txt"
    make_cookies(source)
    monkeypatch.setenv(import_auth.COOKIE_ENV, str(source))
    assert import_auth.youtube_cookie_status()["status"] == "ready"
    with import_auth.youtube_cookie_session(
        "https://www.youtube.com/watch?v=test"
    ) as cookie:
        assert cookie.is_file()
        assert cookie != source
        content = cookie.read_text(encoding="utf-8")
        assert "#HttpOnly_.youtube.com" in content
        assert "private-youtube-value" in content
        assert "another-secret" not in content
        assert not (cookie.stat().st_mode & 0o077)
        temporary_path = cookie
    assert not temporary_path.exists()
    with import_auth.youtube_cookie_session("https://vimeo.com/123") as cookie:
        assert cookie is None


def test_invalid_cookies_do_not_leak_secret_to_settings(monkeypatch, tmp_path):
    invalid = tmp_path / "cookies.txt"
    invalid.write_text('{"secret": "private-bearer-token"}', encoding="utf-8")
    monkeypatch.setenv(import_auth.COOKIE_ENV, str(invalid))
    assert import_auth.youtube_cookie_status() == {
        "configured": False, "status": "invalid",
    }
    settings = TestClient(module.app).get("/api/settings")
    assert settings.status_code == 200
    assert '"status":"invalid"' in settings.text
    assert "private-bearer-token" not in settings.text
    with pytest.raises(RuntimeError, match="Netscape"):
        with import_auth.youtube_cookie_session("https://youtu.be/abc"):
            pass


def test_download_uses_temp_cookies_for_youtube_only(monkeypatch, tmp_path):
    import yt_dlp

    source = tmp_path / "youtube.txt"
    make_cookies(source)
    monkeypatch.setenv(import_auth.COOKIE_ENV, str(source))
    calls = []
    copied_paths = []

    class FakeDownloader:
        def __init__(self, options):
            self.options = options
            calls.append(options.copy())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, urls):
            assert len(urls) == 1
            cookiefile = self.options.get("cookiefile")
            if cookiefile is not None:
                with open(cookiefile, encoding="utf-8") as stream:
                    text = stream.read()
                assert "private-youtube-value" in text
                assert "another-secret" not in text
                copied_paths.append(cookiefile)
            (tmp_path / "source.mp4").write_bytes(b"authorized video file")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeDownloader)
    assert download_video("https://www.youtube.com/watch?v=abc", tmp_path).exists()
    assert calls[0].get("cookiefile")
    assert not __import__("pathlib").Path(copied_paths[0]).exists()
    (tmp_path / "source.mp4").unlink()
    assert download_video("https://vimeo.com/123", tmp_path).exists()
    assert not calls[1].get("cookiefile")


def test_youtube_verification_failure_has_recovery_instructions(monkeypatch, tmp_path):
    import yt_dlp

    monkeypatch.delenv(import_auth.COOKIE_ENV, raising=False)
    monkeypatch.setattr(import_auth, "DEFAULT_PATH", str(tmp_path / "not-found.txt"))

    class FailedDownloader:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, urls):
            raise yt_dlp.utils.DownloadError(
                "ERROR: [youtube] Sign in to confirm you’re not a bot"
            )

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FailedDownloader)
    with pytest.raises(RuntimeError, match="YouTube requested account verification") as error:
        download_video("https://www.youtube.com/watch?v=test", tmp_path)
    assert "Resume job in Activity" in str(error.value)
