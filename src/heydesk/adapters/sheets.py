"""Google Sheets' CSV export over httpx: a sheet shared as "Anyone with the link" is
read with no Google keys. A private one answers with Google's sign-in page, not CSV."""

import httpx

from heydesk.ports.sheets import SheetUnreadable

SHARE = "Share the sheet as 'Anyone with the link: Viewer', then try again."


async def fetch_csv(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.HTTPError:
        raise SheetUnreadable("Couldn't reach Google Sheets. Try again in a minute.") from None
    if response.status_code == 404:
        raise SheetUnreadable("There's no such sheet: check the link.")
    if response.status_code != 200 or "text/csv" not in response.headers.get("content-type", ""):
        raise SheetUnreadable(SHARE)
    return response.text
