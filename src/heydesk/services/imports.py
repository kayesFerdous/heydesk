"""Which of a spreadsheet's columns is which of our fields (D53): the model suggests, Python
checks it, and the owner decides on their screen. The model sees a few rows but never changes a value."""

import asyncio
import json
import logging

from heydesk.desks import csv_import
from heydesk.desks.csv_import import Column, Suggestion
from heydesk.ports.llm import ChatRequest, Finished, LanguageModel, LLMUnavailable, TextDelta

log = logging.getLogger(__name__)

SUGGEST_TIMEOUT = 20  # seconds; then the owner matches by hand

MATCH_COLUMNS = """\
You match the columns of a business's spreadsheet to the fields of our list.
Reply with one JSON object and nothing else: each of our field names as a key, and as its value
the exact name of the file's column that holds it. Use each column at most once. Leave a field
out when no column holds it; don't guess."""


class ImportService:
    def __init__(self, llm: LanguageModel, model: str) -> None:
        self.llm = llm
        self.model = model

    async def suggest(self, text: str, columns: list[Column]) -> Suggestion:
        headers, samples = csv_import.peek(text)
        try:
            async with asyncio.timeout(SUGGEST_TIMEOUT):
                reply = await self._ask(columns, headers, samples)
            mapping = csv_import.check_mapping(json_object(reply), headers, columns)
            if not mapping:
                raise ValueError("nothing in the reply matched")
            by_ai = True
        except (LLMUnavailable, TimeoutError, ValueError) as error:
            log.warning("column match by name", extra={"reason": type(error).__name__})
            mapping, by_ai = csv_import.by_name(headers, columns), False
        log.info("columns suggested", extra={"columns": len(headers), "matched": len(mapping)})
        return Suggestion(
            columns=columns, headers=headers, samples=samples, mapping=mapping, by_ai=by_ai
        )

    async def _ask(
        self, columns: list[Column], headers: list[str], samples: list[dict[str, str]]
    ) -> str:
        ours = "\n".join(
            f"- {c.name}: {c.about}{' (required)' if c.required else ''}" for c in columns
        )
        message = (
            f"Our fields:\n{ours}\n\n"
            f"The file's columns: {json.dumps(headers, ensure_ascii=False)}\n\n"
            f"Its first rows: {json.dumps(samples, ensure_ascii=False)}"
        )
        request = ChatRequest(
            system=MATCH_COLUMNS,
            tools=[],
            messages=[self.llm.user_message(message)],
            model=self.model,
        )
        text = ""
        async for event in self.llm.stream(request):
            if isinstance(event, TextDelta):
                text += event.text
            elif isinstance(event, Finished) and event.stop != "end":
                raise ValueError(f"the model stopped: {event.stop}")
        return text


def json_object(text: str) -> object:
    """The first JSON object in the model's reply. Raises ValueError when there's none."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON in the reply")
    found, _ = json.JSONDecoder().raw_decode(text, start)  # JSONDecodeError is a ValueError
    return found
