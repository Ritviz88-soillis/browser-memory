"""URL → video id extraction. The fetch itself is network I/O, not unit-tested."""

from utils.youtube import video_id


def test_watch_urls():
    assert video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id("https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=42s") == "dQw4w9WgXcQ"


def test_short_and_embed_shapes():
    assert video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id("https://youtu.be/dQw4w9WgXcQ?si=abc") == "dQw4w9WgXcQ"
    assert video_id("https://www.youtube.com/shorts/abc123def45") == "abc123def45"
    assert video_id("https://www.youtube.com/embed/abc123def45") == "abc123def45"
    assert video_id("https://www.youtube.com/live/abc123def45") == "abc123def45"


def test_non_video_urls_are_none():
    assert video_id("https://www.youtube.com/") is None
    assert video_id("https://www.youtube.com/@somechannel") is None
    assert video_id("https://www.youtube.com/feed/subscriptions") is None
    assert video_id("https://example.com/watch?v=dQw4w9WgXcQ") is None
    assert video_id("not a url at all") is None
