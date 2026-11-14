"""Authorized YouTube imports use isolated, disposable cookie sessions."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipforge import app as module
from clipforge import import_auth
from clipforge.engine import download_video


def test_po_provider_sidecar_is_pinned_and_not_published():
    root = Path(__file__).resolve().parents[1]
    compose = (root / "compose.yaml").read_text()
    sidecar = compose.split("\n  yt-pot-provider:\n", 1)[1].split("\n  ollama:", 1)[0]
    assert "brainicism/bgutil-ytdlp-pot-provider:2.0.2-deno" in sidecar
    assert "\n    ports:\n" not in sidecar
    assert '"4416"' in sidecar
    assert "healthcheck:" in sidecar
    assert "service_healthy" in compose
    assert "bgutil-ytdlp-pot-provider==2.0.2" in (root / "pyproject.toml").read_text()


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
    assert calls[0]["source_address"] == "0.0.0.0"
    # Prefer source quality <=1080p, but don't fail when the only playable
    # video track exceeds that resolution.
    selector = calls[0]["format"]
    assert "height<=?1080" in selector
    assert selector.endswith("/bv*+ba/b")
    assert calls[0]["format_sort"] == ["res:1080"]
    assert not __import__("pathlib").Path(copied_paths[0]).exists()
    (tmp_path / "source.mp4").unlink()
    assert download_video("https://vimeo.com/123", tmp_path).exists()
    assert not calls[1].get("cookiefile")
    assert calls[1]["source_address"] == "0.0.0.0"


def test_private_po_provider_is_used_for_youtube_only(monkeypatch, tmp_path):
    import yt_dlp

    monkeypatch.setenv("CLIPFORGE_YOUTUBE_POT_URL", "http://yt-pot-provider:4416")
    monkeypatch.delenv(import_auth.COOKIE_ENV, raising=False)
    monkeypatch.setattr(import_auth, "DEFAULT_PATH", str(tmp_path / "missing.txt"))
    calls = []

    class FakeDownloader:
        def __init__(self, options):
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, _urls):
            (tmp_path / "source.mp4").write_bytes(b"video")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeDownloader)
    assert download_video("https://youtu.be/abc", tmp_path).is_file()
    assert calls[0]["extractor_args"] == {
        "youtube": {"player_client": ["mweb"]},
        "youtubepot-bgutilhttp": {"base_url": ["http://yt-pot-provider:4416"]},
    }
    (tmp_path / "source.mp4").unlink()
    assert download_video("https://vimeo.com/123", tmp_path).is_file()
    assert calls[1]["extractor_args"] == {}

    monkeypatch.delenv("CLIPFORGE_YOUTUBE_POT_URL")
    (tmp_path / "source.mp4").unlink()
    assert download_video("https://youtube.com/watch?v=abc", tmp_path).is_file()
    assert calls[2]["extractor_args"] == {}


def test_po_provider_rejects_untrusted_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPFORGE_YOUTUBE_POT_URL", "http://example.com:4416")
    with pytest.raises(ValueError, match="private Docker service"):
        download_video("https://www.youtube.com/watch?v=test", tmp_path)


def test_youtube_403_has_provider_specific_guidance(monkeypatch, tmp_path):
    import yt_dlp

    monkeypatch.setenv("CLIPFORGE_YOUTUBE_POT_URL", "http://yt-pot-provider:4416")
    monkeypatch.delenv(import_auth.COOKIE_ENV, raising=False)
    monkeypatch.setattr(import_auth, "DEFAULT_PATH", str(tmp_path / "missing.txt"))

    class DeniedDownloader:
        def __init__(self, options):
            assert options["extractor_args"]["youtube"]["player_client"] == ["mweb"]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, _urls):
            raise yt_dlp.utils.DownloadError(
                "ERROR: unable to download video data: HTTP Error 403: Forbidden"
            )

    monkeypatch.setattr(yt_dlp, "YoutubeDL", DeniedDownloader)
    with pytest.raises(RuntimeError, match="HTTP 403") as exc:
        download_video("https://youtu.be/abc", tmp_path)
    assert "server IP" in str(exc.value)
    assert "repeated job resumes" in str(exc.value)


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


def test_unavailable_formats_explain_missing_media_and_recovery(monkeypatch, tmp_path):
    import yt_dlp

    monkeypatch.delenv(import_auth.COOKIE_ENV, raising=False)
    monkeypatch.setattr(import_auth, "DEFAULT_PATH", str(tmp_path / "not-found.txt"))

    class MissingFormatsDownloader:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, urls):
            assert self.options["format"].endswith("/bv*+ba/b")
            raise yt_dlp.utils.DownloadError(
                "ERROR: [youtube] Y7kBmcWKLxg: Requested format is not available"
            )

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MissingFormatsDownloader)
    with pytest.raises(RuntimeError, match="YouTube did not provide a playable video format") as exc:
        download_video("https://www.youtube.com/watch?v=Y7kBmcWKLxg", tmp_path)
    assert "Deno and yt-dlp-ejs" in str(exc.value)
    assert "Activity" in str(exc.value)


def test_download_ip_family_is_configurable(monkeypatch, tmp_path):
    import yt_dlp

    monkeypatch.delenv(import_auth.COOKIE_ENV, raising=False)
    monkeypatch.setattr(import_auth, "DEFAULT_PATH", str(tmp_path / "missing.txt"))
    choices = []

    class FakeDownloader:
        def __init__(self, options):
            choices.append(dict(options))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def download(self, urls):
            assert urls == ["https://vimeo.com/123"]
            (tmp_path / "source.mp4").write_bytes(b"video")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeDownloader)
    for preference, expected in (
        ("ipv4", "0.0.0.0"), ("ipv6", "::"), ("auto", None)
    ):
        monkeypatch.setenv("CLIPFORGE_DOWNLOAD_IP_FAMILY", preference)
        assert download_video("https://vimeo.com/123", tmp_path).is_file()
        assert choices[-1].get("source_address") == expected
        (tmp_path / "source.mp4").unlink()

    monkeypatch.setenv("CLIPFORGE_DOWNLOAD_IP_FAMILY", "invalid")
    with pytest.raises(ValueError, match="CLIPFORGE_DOWNLOAD_IP_FAMILY"):
        download_video("https://vimeo.com/123", tmp_path)
    assert len(choices) == 3


def test_unreachable_network_error_is_actionable(monkeypatch, tmp_path):
    import yt_dlp

    monkeypatch.setenv("CLIPFORGE_DOWNLOAD_IP_FAMILY", "ipv4")
    monkeypatch.delenv(import_auth.COOKIE_ENV, raising=False)
    monkeypatch.setattr(import_auth, "DEFAULT_PATH", str(tmp_path / "missing.txt"))

    class Offline:
        def __init__(self, options):
            assert options["source_address"] == "0.0.0.0"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def download(self, urls):
            raise yt_dlp.utils.DownloadError(
                "HTTPSConnection(host='www.youtube.com'): [Errno 101] Network is unreachable"
            )

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Offline)
    with pytest.raises(RuntimeError, match="configured to use ipv4") as error:
        download_video("https://www.youtube.com/watch?v=test", tmp_path)
    assert "Resume job" in str(error.value)
