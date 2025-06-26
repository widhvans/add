import re
import time
import random
import asyncio

def get(o, k, d=None):
    """Safely get a key from a dictionary/object."""
    return o.get(k, d) if o else d

def fd(s):
    """Format duration in H:MM:SS or MM:SS."""
    if s is None:
        return "N/A"
    m, s = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

def strip_html(text):
    """Remove HTML tags from a string."""
    if not isinstance(text, str):
        return text
    clean = re.compile('<.*?>')
    return re.sub(clean, '', text)

async def delete_after(m, d):
    """Delete a Telegram message after a delay."""
    await asyncio.sleep(d)
    try:
        if isinstance(m, list):
            if m:
                await m[0].client.delete_messages(m[0].chat_id, m)
        else:
            await m.delete()
    except Exception as e:
        pass
