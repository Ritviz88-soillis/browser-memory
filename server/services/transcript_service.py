"""YouTube transcript fetching for the current-page source.

youtube-transcript-api scrapes YouTube's caption endpoints, which is inherently
best-effort: no captions, disabled captions, or region blocks all degrade to
None and the caller falls back to whatever text the extension sent.
"""

import asyncio
import logging
from typing import Dict, Optional

from youtube_transcript_api import YouTubeTranscriptApi

import config

logger = logging.getLogger(__name__)


class TranscriptService:
    """Fetches and caches video transcripts, one entry per video id."""

    def __init__(self) -> None:
        """Start with an empty in-process cache."""

        self._cache: Dict[str, Optional[str]] = {}

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """Return a video's transcript, from cache when already fetched.

        Args:
            video_id: The 11-character YouTube video id.

        Returns:
            The transcript text capped to ``config.TRANSCRIPT_TEXT_CAP``, or
            None when the video has no usable captions.
        """

        if video_id in self._cache:
            return self._cache[video_id]

        text = await asyncio.to_thread(self._fetch_sync, video_id)
        if text:
            text = text[: config.TRANSCRIPT_TEXT_CAP]

        if len(self._cache) >= config.TRANSCRIPT_CACHE_MAX:
            self._cache.pop(next(iter(self._cache)))
        self._cache[video_id] = text
        return text

    def _fetch_sync(self, video_id: str) -> Optional[str]:
        """Blocking fetch: preferred languages first, then any available track."""

        api = YouTubeTranscriptApi()
        try:
            try:
                fetched = api.fetch(video_id, languages=config.TRANSCRIPT_LANGUAGES)
            except Exception:
                first_track = next(iter(api.list(video_id)), None)
                if first_track is None:
                    return None
                fetched = first_track.fetch()

            return " ".join(
                snippet.text.strip() for snippet in fetched.snippets if snippet.text.strip()
            )
        except Exception as error:
            logger.info("no transcript for %s: %s", video_id, error)
            return None
