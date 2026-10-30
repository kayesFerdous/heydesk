"""The documents a business adds to its agent's knowledge: text or a file, listed, deleted.

A document is read, cut up and indexed before the call returns, so it is searchable at once.
Nothing is saved if any step fails.
"""

import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from heydesk.api.deps import Svc, Workspace
from heydesk.models import Document
from heydesk.runtime.text import group_alike
from heydesk.services.extract import extract_text, kind_of

router = APIRouter(tags=["knowledge"])


class DocumentIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(max_length=400_000, description="The document's text; Markdown is fine")


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    filename: str
    kind: str  # text, markdown or pdf
    chunk_count: int  # how many pieces it was cut into
    created_at: datetime


class DocumentWithText(DocumentOut):
    text: str


@router.post("/api/agent/documents", status_code=status.HTTP_201_CREATED)
async def add_text(body: DocumentIn, services: Svc, ws: Workspace) -> DocumentOut:
    """Add pasted text to the agent's knowledge."""
    agent = await services.agents.get(ws)  # 404 until the business has an agent
    kind = "markdown" if body.text.lstrip().startswith("#") else "text"
    document = await services.knowledge.add(ws, agent.id, body.title, body.text, kind)
    return DocumentOut.model_validate(document)


@router.post("/api/agent/documents/file", status_code=status.HTTP_201_CREATED)
async def add_file(
    file: Annotated[UploadFile, File(description="A .txt, .md or .pdf file, up to 10 MB")],
    services: Svc,
    ws: Workspace,
    title: Annotated[str, Form()] = "",
) -> DocumentOut:
    """Add a file: its text is read (a PDF by its text layer) and added like pasted text."""
    agent = await services.agents.get(ws)
    name = file.filename or "document.txt"
    kind = kind_of(name)  # a wrong type is refused before it's read
    text = await asyncio.to_thread(extract_text, name, await file.read())
    document = await services.knowledge.add(ws, agent.id, title or name, text, kind, name)
    return DocumentOut.model_validate(document)


@router.get("/api/agent/documents")
async def list_documents(services: Svc, ws: Workspace) -> list[DocumentOut]:
    return [DocumentOut.model_validate(d) for d in await services.knowledge.all(ws)]


@router.get("/api/agent/documents/{document_id}")
async def read_document(document_id: str, services: Svc, ws: Workspace) -> DocumentWithText:
    document: Document = await services.knowledge.get(ws, document_id)
    return DocumentWithText.model_validate(document)


@router.delete("/api/agent/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str, services: Svc, ws: Workspace) -> None:
    await services.knowledge.delete(ws, document_id)


class MissedOut(BaseModel):
    question: str  # the first one asked of the group
    count: int  # how many times it was asked alike
    ids: list[str]  # all of them, for Teach it
    last_asked: datetime


class TeachIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)  # the group's
    question: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=5000)


@router.get("/api/agent/missed")
async def missed(services: Svc, ws: Workspace) -> list[MissedOut]:
    """What the agent couldn't answer, grouped by likeness, the most asked first."""
    store = await services.agents.store(ws)
    rows = await store.missed_list()
    return [
        MissedOut(
            question=rows[group[0]].question,
            count=len(group),
            ids=[rows[i].id for i in group],
            last_asked=max(rows[i].created_at for i in group),
        )
        for group in group_alike([r.question for r in rows])
    ]


@router.post("/api/agent/missed/teach", status_code=status.HTTP_201_CREATED)
async def teach(body: TeachIn, services: Svc, ws: Workspace) -> DocumentOut:
    """*Teach it*: the answer becomes a knowledge entry, so `search_knowledge` finds it next
    time, and the group leaves the list."""
    agent = await services.agents.get(ws)
    text = f"{body.question}\n\n{body.answer}"
    document = await services.knowledge.add(ws, agent.id, body.question, text)
    store = await services.agents.store(ws)
    await store.mark_answered(body.ids)
    return DocumentOut.model_validate(document)
