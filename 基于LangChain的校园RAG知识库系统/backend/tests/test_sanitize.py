from src.utils.sanitize import sanitize_text


def test_sanitize_strips_tags():
    assert sanitize_text("<script>alert(1)</script>制度", 100) == "alert(1)制度"


def test_sanitize_escapes_angle_brackets():
    assert sanitize_text("1 < 2 > 0", 100) == "1 &lt; 2 &gt; 0"


def test_sanitize_truncates():
    assert sanitize_text("a" * 50, 10) == "a" * 10


def test_sanitize_none_returns_empty():
    assert sanitize_text(None, 10) == ""
