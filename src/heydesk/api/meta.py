"""What Messenger's and WhatsApp's webhooks share: both are Meta's, set in the same Meta app,
so Meta checks each one the same way and signs every post with the same app secret."""

import json
from typing import Annotated, Any

from fastapi import Depends, Header, Query, Request

from heydesk.api.deps import Svc
from heydesk.errors import NotFound


def meta_challenge(
    services: Svc,
    mode: Annotated[str, Query(alias="hub.mode")] = "",
    token: Annotated[str, Query(alias="hub.verify_token")] = "",
    challenge: Annotated[str, Query(alias="hub.challenge")] = "",
) -> str:
    """Meta checks a webhook once, when it's set in the Meta app's dashboard: it gets its
    challenge back only if the verify token is ours."""
    if mode != "subscribe" or not services.channels.meta_verify_token_matches(token):
        raise NotFound("Not found")
    return challenge


async def signed_meta_post(
    request: Request,
    services: Svc,
    signature: Annotated[str, Header(alias="X-Hub-Signature-256")] = "",
) -> dict[str, Any]:
    """What Meta posted, only if Meta signed it."""
    body = await request.body()
    if not services.channels.meta_signature_is_valid(body, signature):
        raise NotFound("Not found")
    posted: dict[str, Any] = json.loads(body)
    return posted


type MetaChallenge = Annotated[str, Depends(meta_challenge)]
type MetaPost = Annotated[dict[str, Any], Depends(signed_meta_post)]
