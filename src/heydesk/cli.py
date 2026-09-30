import uvicorn


def main() -> None:
    """`uv run heydesk` starts the dev server. `heydesk chat` arrives in M2."""
    uvicorn.run("heydesk.main:app", reload=True)
