"""Generation stage — turn sources + question into a grounded, cited answer."""

from typing import List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser

import config
from prompts.answer_prompt import ANSWER_PROMPT
from schemas import HistoryTurn
from utils.llm import build_chat_model


class GenerationService:
    """Runs the LLM over the formatted sources to answer the question."""

    def __init__(self, model=None) -> None:
        """Build the chain (prompt -> chat model -> string parser) once.

        Args:
            model: A chat model or runnable to use instead of the configured
                one (tests inject a fake here).
        """

        model = model or build_chat_model(config.ANSWER_TEMPERATURE)
        self._chain = ANSWER_PROMPT | model | StrOutputParser()

    async def generate(
        self,
        question: str,
        context: str,
        history: List[HistoryTurn],
    ) -> str:
        """Generate an answer grounded in the provided sources.

        Args:
            question: The user's question.
            context: The formatted, numbered source block.
            history: Earlier turns of the conversation, oldest first.

        Returns:
            The raw answer text; citations are validated by the caller.
        """

        answer = await self._chain.ainvoke(
            {
                "history": self._to_messages(history),
                "context": context,
                "question": question,
            }
        )
        return answer.strip()

    def _to_messages(self, history: List[HistoryTurn]) -> List[BaseMessage]:
        """Convert stored turns into LangChain chat messages."""

        return [
            AIMessage(content=turn.content)
            if turn.role == "assistant"
            else HumanMessage(content=turn.content)
            for turn in history
        ]
