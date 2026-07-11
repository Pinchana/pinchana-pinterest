import pytest

from pinchana_pinterest.extractor import (
    InvalidPinterestUrl,
    extract_pin_id,
    parse_pin_html,
    validate_url,
)


def test_extract_pin_id_supports_plain_and_slug_urls():
    assert extract_pin_id("https://www.pinterest.com/pin/123456/") == "123456"
    assert extract_pin_id("https://pinterest.com/pin/a-title--987654/") == "987654"


def test_rejects_lookalike_hosts():
    with pytest.raises(InvalidPinterestUrl):
        validate_url("https://pinterest.com.example.org/pin/123/")


def test_parses_image_metadata():
    document = """
    <html><head>
      <meta property="og:title" content="A pin">
      <meta property="og:description" content="Pin description">
      <meta property="og:image" content="https://i.pinimg.com/originals/example.jpg">
    </head></html>
    """
    result = parse_pin_html(document, "123", "https://pinterest.com/pin/123/")
    assert result.pin_id == "123"
    assert result.caption == "Pin description"
    assert result.image_urls == ["https://i.pinimg.com/originals/example.jpg"]


def test_prefers_embedded_video():
    document = """
    <html><head>
      <meta property="og:image" content="https://i.pinimg.com/poster.jpg">
      <script type="application/ld+json">
      {"@type":"VideoObject","contentUrl":"https://v.pinimg.com/video.mp4"}
      </script>
    </head></html>
    """
    result = parse_pin_html(document, "456", "https://pinterest.com/pin/456/")
    assert result.video_url == "https://v.pinimg.com/video.mp4"
    assert result.thumbnail_url == "https://i.pinimg.com/poster.jpg"

