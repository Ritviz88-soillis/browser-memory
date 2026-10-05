"""Query log: every question, what was retrieved for it and what was answered.

The evaluation harness is built from this record.
"""

from typing import Any, Dict, List, Optional

import db


class QueryLogService:
    """Records each exchange."""

    def record(
        self,
        *,
        device_id: Optional[str],
        question: str,
        parsed_filter: Dict[str, Any],
        chunk_ids: List[int],
        answer: str,
        abstained: bool,
        embed_model: str,
        llm_model: str,
        latency_ms: int,
    ) -> None:
        """Save one question and its outcome.

        Args:
            device_id: The asking device.
            question: The question as typed.
            parsed_filter: The filters extracted from it.
            chunk_ids: The memory chunks retrieved for it.
            answer: The answer given (or the abstention text).
            abstained: Whether no answer was generated.
            embed_model: The embedding model in use.
            llm_model: The chat model in use.
            latency_ms: Time taken end to end.
        """

        db.log_query(
            device_id=device_id,
            question=question,
            parsed_filter=parsed_filter,
            chunk_ids=chunk_ids,
            answer=answer,
            abstained=abstained,
            embed_model=embed_model,
            llm_model=llm_model,
            latency_ms=latency_ms,
        )
