import asyncio
import json
import logging
import os
import re
import time
from telethon import TelegramClient, events, errors
from telethon.tl.types import (
    MessageMediaWebPage,
    MessageMediaEmpty,
    MessageMediaUnsupported,
    Channel,
    Chat,
    User,
    PeerChannel,
    PeerChat,
    PeerUser,
    MessageActionChatEditTitle
)
from telethon.tl.functions.channels import EditTitleRequest
from telethon.tl.functions.messages import EditChatTitleRequest

# --- CONFIG ---
API_ID = 33248589
API_HASH = "cb94a9acfa11e9aaf1195610f4144e65"
PHONE = "+916209065785"
SESSION = f"session_{PHONE.replace('+', '').strip()}"
FILE = "settings.json"

# Default 5 Continuous NC Titles (Cycles 1 to 5)
DEFAULT_NC_TITLES = [
    "🔥 ALPHA ZONE 🔥",
    "⚡ THUNDER REIGN ⚡",
    "👑 APEX EMPIRE 👑",
    "💎 NITRO KINGS 💎",
    "🚀 SUPREME RULE 🚀"
]

# 5 Custom Auto-Replies for Groups (Cycles 1 to 5)
DEFAULT_GC_REPLIES = [
    "Hi randiputra 😂😅 Yaha tu typing karta reh gaya waha meney Teri mummy ko pel diya 💦🔥",
    "Abe chutiye sun 😂 Teri aukat nahi hai mere aage bolne ki, ja apni behen ko bhej 😜🔥",
    "Bina baap ki aulad 😂 Tere jaise 10 meri jeb me ghumte hai, chal nikal yaha se 🖕😡",
    "Kya re randi ke bache 😂 Itna mat bhonk, teri ammi ka rate laga dunga group me 💥🔥",
    "Sun bsdk 😂 Jisse tu baap manta hai na, wo roz raat ko mere pair dabata hai 😈🤙"
]

# 5 Custom Auto-Replies for DMs (Cycles 1 to 5)
DEFAULT_DM_REPLIES = [
    "Abe randike DM me kyu apni mummy chudwane aaya hai 😂🖕",
    "Abe lode thoda sabr rakh, teri behen ko chod ke free hota hu fir reply dunga 🔥💥",
    "Aukat me reh ke baat kar lawde 😂 warna yahi teri pant khol dunga 😜💦",
    "Sun bhadwe 😂 mere DM me aake apni amma mat chudwa, nikal yaha se 🚀🖕",
    "Haha bsdk 😂 typing speed badha le pehle, baad me aake apni maa chudwana 🤙😡"
]

GC_REPLIES = list(DEFAULT_GC_REPLIES)
DM_REPLIES = list(DEFAULT_DM_REPLIES)
NC_TITLES = list(DEFAULT_NC_TITLES)
TEXT = GC_REPLIES[0]
NC_INT, VANISH, MAX_REP, COOLDOWN = 3.0, 1.5, 3, 1.5

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
client = TelegramClient(SESSION, API_ID, API_HASH)

# --- STATE ---
REPLY_CHATS, MUTE_CHATS, LOCK_CHATS, UNMUTE_EXEMPTS = set(), set(), set(), set()
DEL_NAME_CHATS = set()  # Chats where all group title change messages are auto-deleted
T_REPLY, T_MUTE, T_LOCK, T_NC_R, T_NC_M, T_NC_L = {}, {}, {}, {}, {}, {}
T_DELNAME = {}  # {cid: set(user_ids)} - Target users whose name change messages get auto-deleted
CACHE, LAST_REP, REP_CNT, NC_TASKS, NC_SETUP = {}, {}, {}, {}, {}
GC_INDEX, DM_INDEX, NC_INDEX = {}, {}, {}
MUTE_ALL, MY_ID = False, None
USER_CACHE = {}  # Fast mention cache: {uid: mention}
ENTITY_CACHE = {}  # Pre-resolved peer input entities for instant title edits

def is_target_user(cid, uid):
    """Checks if a user ID belongs to any active target list in this chat."""
    if not uid:
        return False
    if cid in T_DELNAME and uid in T_DELNAME[cid]:
        return True
    if cid in T_NC_L and uid in T_NC_L[cid]:
        return True
    if cid in T_NC_M and uid in T_NC_M[cid]:
        return True
    if cid in T_NC_R and uid in T_NC_R[cid]:
        return True
    if cid in T_LOCK and uid in T_LOCK[cid]:
        return True
    if cid in T_MUTE and uid in T_MUTE[cid]:
        return True
    if cid in T_REPLY and uid in T_REPLY[cid]:
        return True
    return False

# --- CONFIG PERSISTENCE ---
def save_cfg():
    try:
        d = {
            "r": list(REPLY_CHATS),
            "m": list(MUTE_CHATS),
            "l": list(LOCK_CHATS),
            "u": list(UNMUTE_EXEMPTS),
            "all_dm": MUTE_ALL,
            "del_name_chats": list(DEL_NAME_CHATS),
            "gc_replies": GC_REPLIES,
            "dm_replies": DM_REPLIES,
            "nc_titles": NC_TITLES,
            "tr": {str(k): list(v) for k, v in T_REPLY.items()},
            "tm": {str(k): list(v) for k, v in T_MUTE.items()},
            "tl": {str(k): list(v) for k, v in T_LOCK.items()},
            "ncr": {str(k): list(v) for k, v in T_NC_R.items()},
            "ncm": {str(k): list(v) for k, v in T_NC_M.items()},
            "ncl": {str(k): list(v) for k, v in T_NC_L.items()},
            "tdel": {str(k): list(v) for k, v in T_DELNAME.items()}
        }
        with open(FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logging.error(f"Error saving config: {exc}")

def load_cfg():
    global MUTE_ALL, GC_REPLIES, DM_REPLIES, NC_TITLES
    if not os.path.exists(FILE):
        return
    try:
        with open(FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        REPLY_CHATS.clear(); REPLY_CHATS.update(d.get("r", []))
        MUTE_CHATS.clear(); MUTE_CHATS.update(d.get("m", []))
        LOCK_CHATS.clear(); LOCK_CHATS.update(d.get("l", []))
        UNMUTE_EXEMPTS.clear(); UNMUTE_EXEMPTS.update(d.get("u", []))
        DEL_NAME_CHATS.clear(); DEL_NAME_CHATS.update(d.get("del_name_chats", []))
        MUTE_ALL = d.get("all_dm", False)
        
        saved_gc = d.get("gc_replies", [])
        saved_dm = d.get("dm_replies", [])
        saved_nc = d.get("nc_titles", [])
        if isinstance(saved_gc, list) and len(saved_gc) >= 5:
            GC_REPLIES[:] = saved_gc[:5]
        if isinstance(saved_dm, list) and len(saved_dm) >= 5:
            DM_REPLIES[:] = saved_dm[:5]
        if isinstance(saved_nc, list) and len(saved_nc) >= 2:
            NC_TITLES[:] = saved_nc[:10]

        for k, v in d.get("tr", {}).items(): T_REPLY[int(k)] = set(v)
        for k, v in d.get("tm", {}).items(): T_MUTE[int(k)] = set(v)
        for k, v in d.get("tl", {}).items(): T_LOCK[int(k)] = set(v)
        for k, v in d.get("ncr", {}).items(): T_NC_R[int(k)] = set(v)
        for k, v in d.get("ncm", {}).items(): T_NC_M[int(k)] = set(v)
        for k, v in d.get("ncl", {}).items(): T_NC_L[int(k)] = set(v)
        for k, v in d.get("tdel", {}).items(): T_DELNAME[int(k)] = set(v)
    except Exception as exc:
        logging.error(f"Error loading config: {exc}")

# --- FAST ASYNC HELPERS (ZERO EVENT LOOP FREEZING) ---
async def _delayed_delete(msg, delay):
    """Deletes message in background without blocking command loop."""
    try:
        await asyncio.sleep(delay)
        await msg.delete()
    except Exception:
        pass

async def fb(e, txt, d=VANISH):
    """Fast non-blocking feedback. Returns immediately and self-destructs asynchronously."""
    try:
        target = await e.edit(txt, parse_mode="md")
        if d:
            asyncio.create_task(_delayed_delete(target or e, d))
    except Exception:
        try:
            target = await e.edit(txt, parse_mode=None)
            if d:
                asyncio.create_task(_delayed_delete(target or e, d))
        except Exception:
            pass

class TargetUser:
    """Lightweight user target container ensuring ID resolution works even without entity cache."""
    def __init__(self, uid, first_name=None, username=None):
        self.id = uid
        self.first_name = first_name or f"User_{uid}"
        self.username = username
        self.title = None

async def get_targets(e, arg=""):
    """Accurately extracts target users from reply or arguments without failing on uncached IDs."""
    targets = []
    
    # Check reply message
    if e.is_reply:
        try:
            r = await e.get_reply_message()
            if r and r.sender_id:
                s = getattr(r, '_sender', None) or getattr(r, 'sender', None)
                if not s:
                    try:
                        s = await r.get_sender()
                    except Exception:
                        s = None
                if s:
                    targets.append(s)
                else:
                    targets.append(TargetUser(r.sender_id))
        except Exception:
            pass

    # Check textual arguments (usernames or numeric IDs)
    tokens = [p.strip(",;@ ") for p in (arg or "").split() if p.strip(",;@ ")]
    for tok in tokens:
        clean_tok = tok.lstrip("-")
        if clean_tok.isdigit():
            uid = int(tok)
            try:
                ent = await client.get_entity(uid)
                if ent and ent not in targets:
                    targets.append(ent)
            except Exception:
                targets.append(TargetUser(uid))
        else:
            try:
                ent = await client.get_entity(tok)
                if ent and ent not in targets:
                    targets.append(ent)
            except Exception:
                pass

    return targets

def uname(u):
    """Clean markdown safe username or mention string."""
    if not u:
        return "@User"
    if getattr(u, "username", None):
        return f"@{u.username}"
    uid = getattr(u, "id", None)
    raw_name = (getattr(u, "first_name", "") or getattr(u, "title", "") or f"User_{uid or ''}").strip()
    clean_name = re.sub(r"[\[\]\(\)*_`]", "", raw_name).strip() or f"User_{uid}"
    if uid:
        return f"[{clean_name}](tg://user?id={uid})"
    return f"@{clean_name}"

async def get_mention(e):
    """High-speed mention resolver with local memory cache to eliminate API lag."""
    sid = e.sender_id
    if not sid:
        return "@there"
    if sid in USER_CACHE:
        return USER_CACHE[sid]

    s = getattr(e, '_sender', None) or getattr(e, 'sender', None)
    if not s:
        try:
            s = await e.get_sender()
        except Exception:
            s = None

    mention = uname(s) if s else f"[User](tg://user?id={sid})"
    USER_CACHE[sid] = mention
    return mention

def is_media(m):
    return m and not isinstance(m, (MessageMediaWebPage, MessageMediaEmpty, MessageMediaUnsupported))

def cache(m):
    if not m or not getattr(m, "chat_id", None) or not getattr(m, "id", None):
        return
    CACHE[(m.chat_id, m.id)] = {
        "cid": m.chat_id,
        "sid": getattr(m, "sender_id", None),
        "text": m.text or m.raw_text or "",
        "media": m.media if is_media(m.media) else None
    }

async def toggle_target(e, arg, dic, label, is_stop=False):
    """Toggles target rules with instant feedback and clean state persistence."""
    cid = e.chat_id
    t = await get_targets(e, arg)

    if is_stop:
        if t and cid in dic:
            for x in t:
                dic[cid].discard(x.id)
            msg = f"🛑 {label} OFF: " + ", ".join(uname(x) for x in t)
        elif cid in dic:
            dic[cid].clear()
            msg = f"🛑 All {label} cleared for this chat."
        else:
            msg = f"ℹ️ No active {label} in this chat."
    else:
        if not t:
            return await fb(e, "⚠️ **Specify @username, ID, or reply to a message.**")
        dic.setdefault(cid, set()).update(x.id for x in t)
        msg = f"🎯 **{label} ON** ({len(t)}): " + ", ".join(uname(x) for x in t)

    save_cfg()
    return await fb(e, msg)

# --- ROBUST & FAST NAME CHANGER (NC) ENGINE ---
async def execute_title_change(cid, title):
    """Executes chat title change with pre-resolved entity cache and error diagnostics."""
    try:
        cached_info = ENTITY_CACHE.get(cid)
        if not cached_info:
            entity = await client.get_entity(cid)
            is_channel = getattr(entity, "megagroup", False) or getattr(entity, "broadcast", False) or isinstance(entity, Channel)
            input_entity = await client.get_input_entity(cid)
            cached_info = {"input": input_entity, "is_channel": is_channel, "raw_id": getattr(entity, "id", abs(cid))}
            ENTITY_CACHE[cid] = cached_info

        if cached_info["is_channel"]:
            await client(EditTitleRequest(channel=cached_info["input"], title=title))
        else:
            # For basic Telegram groups, chat_id must be a positive integer
            await client(EditChatTitleRequest(chat_id=cached_info["raw_id"], title=title))
        return True, "Success"
    except errors.FloodWaitError as err:
        logging.warning(f"Telegram FloodWait on NC (chat {cid}): waiting {err.seconds}s")
        return False, f"FloodWait: {err.seconds}s"
    except errors.ChatAdminRequiredError:
        logging.error(f"Cannot change title in {cid}: Admin permission 'Change Group Info' required!")
        return False, "ChatAdminRequired"
    except errors.ChatNotModifiedError:
        return True, "Title already set"
    except Exception as exc:
        logging.error(f"Title change error in {cid}: {exc}")
        ENTITY_CACHE.pop(cid, None)
        return False, str(exc)

async def nc_worker(cid, names):
    """High performance NC loop with adaptive delay and admin permission check."""
    i = 0
    try:
        while cid in NC_TASKS:
            title = names[i % len(names)]
            i += 1
            ok, reason = await execute_title_change(cid, title)
            if not ok:
                if reason == "ChatAdminRequired":
                    try:
                        await client.send_message(cid, "⚠️ **Auto-NC Stopped:** You need 'Change Group Info' admin rights in this group.")
                    except Exception:
                        pass
                    break
                elif "FloodWait" in reason:
                    try:
                        wait_sec = int(reason.split(":")[1].replace("s", "").strip())
                        await asyncio.sleep(wait_sec + 1)
                    except Exception:
                        await asyncio.sleep(10)
            await asyncio.sleep(NC_INT)
    except asyncio.CancelledError:
        pass
    finally:
        NC_TASKS.pop(cid, None)

def start_nc(cid, names):
    """Starts or replaces continuous title changer."""
    stop_nc_task(cid)
    clean_names = [n.strip() for n in names if n.strip()]
    if not clean_names:
        clean_names = list(DEFAULT_NC_TITLES)
    NC_TASKS[cid] = asyncio.create_task(nc_worker(cid, clean_names))

def stop_nc_task(cid):
    """Stops continuous title changer and cleans up any setup state."""
    if cid in NC_TASKS:
        try:
            NC_TASKS[cid].cancel()
            del NC_TASKS[cid]
        except Exception:
            pass
    if cid in NC_SETUP:
        del NC_SETUP[cid]

# --- HELP MENU ---
HELP = """📋 **USERBOT ALL COMMANDS & CONTROLS**

🔄 **CONTINUOUS TITLE CHANGER (NC):**
• `*nc` ➔ Start 5-title cycle immediately (default or active titles)
• `*nc n1 | n2 | n3 | n4 | n5` ➔ Start with custom names (pipe/comma separated)
• `*nc setup` ➔ Interactive step-by-step setup wizard
• `*stopnc` / `*stop nc` / `*unnc` ➔ Stop title loop

🎯 **TARGET NC ALERTS & LOCK:**
• `*target nc reply <@user/ID>` ➔ Set target for NC reply callouts
• `*stop target nc reply` ➔ Disable target NC reply
• `*target nc mute <@user/ID>` ➔ Auto-delete target messages + NC trigger
• `*stop target nc mute` ➔ Disable target NC mute
• `*target nc lock <@user/ID>` ➔ Lock chat against target
• `*stop target nc lock` ➔ Disable target NC lock
• `*target delname <@user/ID>` ➔ Auto-delete group name change messages of target
• `*stop target delname [@user]` ➔ Disable target name change message deletion
• `*delname` ➔ Auto-delete ALL "changed group name" service messages in this chat
• `*stop delname` ➔ Disable auto-delete for group name change messages

🎯 **TARGET MESSAGES (Multi-User):**
• `*target reply <@user/ID>` ➔ Auto-reply (1-5 rotation) to target
• `*stop target reply [@user]` ➔ Stop target reply
• `*target mute <@user/ID>` ➔ Auto-delete all messages from target
• `*stop target mute [@user]` ➔ Stop target mute
• `*target lock <@user/ID>` ➔ Lock down target user
• `*stop target lock [@user]` ➔ Stop target lock

👥 **GROUP COMMANDS (5 Custom Rotating Auto-Replies with @ProfileName):**
• `*replyall` ➔ Toggle reply to all group members (1-5 rotation)
• `*stop replyall` (or `*unreplyall`) ➔ Stop replyall
• `*gcmsgs` ➔ View current 5 group auto-replies
• `*setgc <1-5> <msg>` ➔ Edit a specific group reply slot
• `*mutegc` ➔ Auto-delete all incoming group messages
• `*stop mutegc` (or `*unmutegc`) ➔ Stop mutegc
• `*lockall` ➔ Full group lockdown
• `*stop lockall` (or `*unlockall`) ➔ Disable lockall

💬 **DM COMMANDS (5 Custom Rotating Auto-Replies with @ProfileName):**
• `*reply` ➔ Toggle DM auto-reply (1-5 rotation)
• `*stop reply` (or `*unreply`) ➔ Stop DM reply
• `*dmmsgs` ➔ View current 5 DM auto-replies
• `*setdm <1-5> <msg>` ➔ Edit a specific DM reply slot
• `*mute` ➔ Mute incoming messages in this DM
• `*stop mute` (or `*unmute`) ➔ Unmute this DM
• `*muteall` ➔ Global DM mute for all private chats
• `*stop muteall` ➔ Disable global DM mute
• `*lock` ➔ Lock this DM
• `*stop lock` (or `*unlock`) ➔ Unlock this DM

🛑 **STOP & STATUS CONTROLS:**
• `*stopall` / `*stop all` ➔ Emergency stop: kill NC & clear all rules in chat
• `*status` ➔ Real-time dashboard of active features & rules"""

# --- COMMAND DISPATCHER ---
@client.on(events.NewMessage(outgoing=True))
@client.on(events.MessageEdited(outgoing=True))
async def on_cmd(e):
    global MUTE_ALL, GC_REPLIES, DM_REPLIES, NC_TITLES
    txt = (e.raw_text or "").strip()
    cid = e.chat_id
    if cid is None:
        return

    # Interactive Step-by-Step 5-Name NC Setup Wizard
    if cid in NC_SETUP and not txt.startswith("*"):
        st = NC_SETUP[cid]
        lines = [l.strip() for l in (txt.split("\n") if "\n" in txt else txt.split("|")) if l.strip()]
        for l in (lines or [txt]):
            if len(st["names"]) < 5 and l:
                st["names"].append(l)
        try:
            await e.delete()
        except Exception:
            pass

        if len(st["names"]) < 5:
            n = len(st["names"]) + 1
            saved = "\n".join([f" {i+1}. `{x}`" for i, x in enumerate(st["names"])])
            msg = f"📝 **[Auto-NC Step {n}/5]**\nSaved ({len(st['names'])}/5):\n{saved}\n\n👉 **Send Name {n} of 5 (or send multiple separated by |):**"
            try:
                pm = await client.get_messages(cid, ids=st.get("pid")) if st.get("pid") else None
                if pm:
                    await pm.edit(msg)
                else:
                    st["pid"] = (await client.send_message(cid, msg)).id
            except Exception:
                st["pid"] = (await client.send_message(cid, msg)).id
            return
        else:
            names = st["names"][:5]
            NC_TITLES[:] = names
            save_cfg()
            del NC_SETUP[cid]
            start_nc(cid, names)
            saved = "\n".join([f" {i+1}. `{x}`" for i, x in enumerate(names)])
            msg = f"🚀 **[Auto-NC Started]**\n{saved}\n\n🔄 Continuously cycling every {NC_INT}s. Type `*stopnc` to stop."
            try:
                pm = await client.get_messages(cid, ids=st.get("pid")) if st.get("pid") else None
                if pm:
                    await pm.edit(msg)
                else:
                    await client.send_message(cid, msg)
            except Exception:
                await client.send_message(cid, msg)
            return

    if not txt.startswith("*"):
        return

    c = txt[1:].strip()
    cl = c.lower()

    # Help
    if cl in ("help", "all commands", "all_commands", "commands", "cmds"):
        return await fb(e, HELP, d=None)

    # 5 Group Auto-Reply View & Edit
    if cl in ("gcmsgs", "gc_msgs", "gc reply", "gcreplies", "gc replies"):
        list_txt = "\n".join([f"**{i+1}.** {m}" for i, m in enumerate(GC_REPLIES)])
        return await fb(e, f"👥 **[5 GROUP AUTO-REPLY MESSAGES]**\n{list_txt}\n\n✏️ Change: `*setgc <1-5> <new message>`", d=None)

    if cl.startswith("setgc"):
        parts = c.split(maxsplit=2)
        if len(parts) >= 3 and parts[1].isdigit() and 1 <= int(parts[1]) <= 5:
            idx = int(parts[1]) - 1
            new_msg = parts[2].strip()
            GC_REPLIES[idx] = new_msg
            save_cfg()
            return await fb(e, f"✅ **Group Auto-Reply #{idx+1} updated:**\n`{new_msg}`")
        else:
            return await fb(e, "⚠️ **Usage:** `*setgc <1-5> <your custom reply message>`")

    # 5 DM Auto-Reply View & Edit
    if cl in ("dmmsgs", "dm_msgs", "dm reply", "dmreplies", "dm replies"):
        list_txt = "\n".join([f"**{i+1}.** {m}" for i, m in enumerate(DM_REPLIES)])
        return await fb(e, f"💬 **[5 DM AUTO-REPLY MESSAGES]**\n{list_txt}\n\n✏️ Change: `*setdm <1-5> <new message>`", d=None)

    if cl.startswith("setdm"):
        parts = c.split(maxsplit=2)
        if len(parts) >= 3 and parts[1].isdigit() and 1 <= int(parts[1]) <= 5:
            idx = int(parts[1]) - 1
            new_msg = parts[2].strip()
            DM_REPLIES[idx] = new_msg
            save_cfg()
            return await fb(e, f"✅ **DM Auto-Reply #{idx+1} updated:**\n`{new_msg}`")
        else:
            return await fb(e, "⚠️ **Usage:** `*setdm <1-5> <your custom reply message>`")

    # Stop All in current chat
    if cl in ("stopall", "stop all", "stopchat", "stop chat", "clearall", "clear all"):
        stop_nc_task(cid)
        REPLY_CHATS.discard(cid)
        MUTE_CHATS.discard(cid)
        LOCK_CHATS.discard(cid)
        DEL_NAME_CHATS.discard(cid)
        UNMUTE_EXEMPTS.add(cid)
        for dic in (T_REPLY, T_MUTE, T_LOCK, T_NC_R, T_NC_M, T_NC_L, T_DELNAME):
            dic.pop(cid, None)
        save_cfg()
        return await fb(e, "🛑 **All features & target rules stopped for this chat.**")

    # Stop Continuous NC
    if cl in ("stopnc", "stop_nc", "stop nc", "ncstop", "nc off", "nc stop", "unnc"):
        stop_nc_task(cid)
        return await fb(e, "🛑 **Continuous NC Stopped.**")

    # Start Continuous NC
    if cl.startswith("nc") and not cl.startswith(("nc reply", "nc mute", "nc lock", "ncr", "ncm", "ncl")):
        sub_arg = c[2:].strip()
        
        # If user explicitly asks for wizard setup
        if sub_arg.lower() in ("setup", "wizard", "add"):
            stop_nc_task(cid)
            NC_SETUP[cid] = {"names": [], "pid": e.id}
            return await fb(e, f"📝 **[Auto-NC Setup]** Send 5 names one by one (or separate with |).\n👉 **Send Name 1 of 5:**", d=None)

        # Parse inline titles
        raw = []
        if sub_arg:
            if "\n" in sub_arg:
                raw = [x.strip() for x in sub_arg.split("\n") if x.strip()]
            elif "|" in sub_arg:
                raw = [x.strip() for x in sub_arg.split("|") if x.strip()]
            elif "," in sub_arg:
                raw = [x.strip() for x in sub_arg.split(",") if x.strip()]
            else:
                raw = [sub_arg]

        # If user simply typed *nc with no arguments, use the configured NC_TITLES
        if not raw:
            raw = list(NC_TITLES)

        if len(raw) >= 2:
            start_nc(cid, raw)
            titles_display = "\n".join([f"• `{t}`" for t in raw])
            return await fb(e, f"🚀 **[Auto-NC Started]** ({len(raw)} titles cycling every {NC_INT}s):\n{titles_display}\n\nType `*stopnc` to stop.")
        else:
            stop_nc_task(cid)
            NC_SETUP[cid] = {"names": raw, "pid": e.id}
            return await fb(e, f"📝 **[Auto-NC Setup]** Send names (or separate with |).\n👉 **Send Name {len(raw)+1} of 5:**", d=None)

    # Target NC Stops
    if cl.startswith(("stop target nc reply", "stop nc reply", "untarget nc reply", "target nc stop reply", "stop target ncreply", "untarget ncreply")):
        return await toggle_target(e, c.split("reply", 1)[1] if "reply" in c else "", T_NC_R, "Target NC Reply", True)
    if cl.startswith(("stop target nc mute", "stop nc mute", "untarget nc mute", "target nc stop mute", "stop target ncmute", "untarget ncmute")):
        return await toggle_target(e, c.split("mute", 1)[1] if "mute" in c else "", T_NC_M, "Target NC Mute", True)
    if cl.startswith(("stop target nc lock", "stop nc lock", "untarget nc lock", "target nc stop lock", "stop target nclock", "untarget nclock")):
        return await toggle_target(e, c.split("lock", 1)[1] if "lock" in c else "", T_NC_L, "Target NC Lock", True)
    if cl.startswith(("stop target delname", "stop target del name", "untarget delname", "stop target ncdel", "untarget ncdel", "stop target name", "untarget target delname")):
        arg = c.split("delname", 1)[1] if "delname" in c else (c.split("name", 1)[1] if "name" in c else "")
        return await toggle_target(e, arg, T_DELNAME, "Target Auto-Delete Name Changes", True)

    # Target NC Starts
    if cl.startswith("target nc reply"):
        return await toggle_target(e, c[15:], T_NC_R, "Target NC Reply")
    if cl.startswith("target nc mute"):
        return await toggle_target(e, c[14:], T_NC_M, "Target NC Mute")
    if cl.startswith("target nc lock"):
        return await toggle_target(e, c[14:], T_NC_L, "Target NC Lock")
    if cl.startswith(("target delname", "target del name", "target delnc", "target ncdel", "target delete name")):
        arg = c.split("delname", 1)[1] if "delname" in c else (c.split("delnc", 1)[1] if "delnc" in c else c.split("name", 1)[1])
        return await toggle_target(e, arg, T_DELNAME, "Target Auto-Delete Name Changes")

    # Chat-Wide Auto-Delete for all "changed group name" service messages
    if cl in ("stop delname", "stop del_name", "undelname", "delname off", "delnc off", "stop delncmsg", "stop deltitle"):
        DEL_NAME_CHATS.discard(cid)
        save_cfg()
        return await fb(e, "🛑 **Auto-Delete Changed Name Messages OFF**")
    if cl in ("delname", "del_name", "delncmsg", "autodelname", "deltitle", "delnames"):
        DEL_NAME_CHATS.add(cid)
        save_cfg()
        return await fb(e, "✅ **Auto-Delete All Changed Name Messages ON** (All chat title service notifications will be deleted automatically)")

    # Target Messages Stops
    if cl.startswith(("stop target reply", "untarget reply", "target stop reply", "stop reply @")) or (cl.startswith("stop reply") and len(cl) > 10):
        return await toggle_target(e, c.split("reply", 1)[1] if "reply" in c else "", T_REPLY, "Target Reply", True)
    if cl.startswith(("stop target mute", "untarget mute", "target stop mute", "stop mute @")) or (cl.startswith("stop mute") and len(cl) > 9):
        return await toggle_target(e, c.split("mute", 1)[1] if "mute" in c else "", T_MUTE, "Target Mute", True)
    if cl.startswith(("stop target lock", "untarget lock", "target stop lock", "stop lock @")) or (cl.startswith("stop lock") and len(cl) > 9):
        return await toggle_target(e, c.split("lock", 1)[1] if "lock" in c else "", T_LOCK, "Target Lock", True)

    # Target Messages Starts
    if cl.startswith("target reply"):
        return await toggle_target(e, c[12:], T_REPLY, "Target Reply")
    if cl.startswith("target mute"):
        return await toggle_target(e, c[11:], T_MUTE, "Target Mute")
    if cl.startswith("target lock"):
        return await toggle_target(e, c[11:], T_LOCK, "Target Lock")

    # Group ReplyAll & Stop
    if cl in ("stop replyall", "stop reply_all", "stopreplyall", "unreplyall", "replyall stop", "replyall off"):
        REPLY_CHATS.discard(cid)
        save_cfg()
        return await fb(e, "🛑 GC **ReplyAll OFF**")
    if cl in ("replyall", "reply_all"):
        REPLY_CHATS.add(cid)
        save_cfg()
        return await fb(e, "✅ GC **ReplyAll ON (5 Custom Messages Cycling with @ProfileName)**")

    # Group MuteGC & Stop
    if cl in ("stop mutegc", "stop mute_gc", "stopmutegc", "unmutegc", "mutegc stop", "mutegc off", "stop mutegroup"):
        MUTE_CHATS.discard(cid)
        UNMUTE_EXEMPTS.add(cid)
        save_cfg()
        return await fb(e, "🛑 GC **MuteGC OFF**")
    if cl in ("mutegc", "mute_gc", "mutegroup"):
        MUTE_CHATS.add(cid)
        UNMUTE_EXEMPTS.discard(cid)
        save_cfg()
        return await fb(e, "✅ GC **MuteGC ON (Auto-deleting member messages)**")

    # Group LockAll & Stop
    if cl in ("stop lockall", "stop lock_all", "stoplockall", "unlockall", "lockall stop", "lockall off", "stop lockgroup"):
        LOCK_CHATS.discard(cid)
        save_cfg()
        return await fb(e, "🛑 GC **LockAll OFF**")
    if cl in ("lockall", "lock_all", "lockgroup"):
        LOCK_CHATS.add(cid)
        save_cfg()
        return await fb(e, "🛡️ GC **LockAll ON**")

    # DM Reply & Stop
    if cl in ("stop reply", "stop_reply", "stopreply", "unreply", "reply stop", "reply off"):
        REPLY_CHATS.discard(cid)
        save_cfg()
        return await fb(e, "🛑 DM **Reply OFF**")
    if cl == "reply":
        REPLY_CHATS.add(cid)
        save_cfg()
        return await fb(e, "✅ DM **Reply ON (5 Custom Messages Cycling with @ProfileName)**")

    # DM Mute & Stop (*unmute)
    if cl in ("stop mute", "stop_mute", "stopmute", "unmute", "mute stop", "mute off"):
        MUTE_CHATS.discard(cid)
        UNMUTE_EXEMPTS.add(cid)
        save_cfg()
        return await fb(e, "🛑 **Unmuted** (Exempted from Global Mute)" if MUTE_ALL else "🛑 DM **Mute OFF**")
    if cl == "mute":
        MUTE_CHATS.add(cid)
        UNMUTE_EXEMPTS.discard(cid)
        save_cfg()
        return await fb(e, "✅ DM **Mute ON**")

    # DM Mute All DMs & Stop
    if cl in ("stop muteall", "stop mute all", "stop_muteall", "stopmuteall", "unmuteall", "unmute all", "muteall stop", "muteall off"):
        MUTE_ALL = False
        UNMUTE_EXEMPTS.clear()
        save_cfg()
        return await fb(e, "🔓 **Mute All DMs OFF**")
    if cl in ("muteall", "mute all", "mute_all"):
        MUTE_ALL = True
        save_cfg()
        return await fb(e, "🔒 **Mute All DMs ON** (Send `*unmute` in a DM to exempt it)")

    # DM Lock & Stop
    if cl in ("stop lock", "stop_lock", "stoplock", "unlock", "lock stop", "lock off"):
        LOCK_CHATS.discard(cid)
        save_cfg()
        return await fb(e, "🛑 DM **Lock OFF**")
    if cl == "lock":
        LOCK_CHATS.add(cid)
        save_cfg()
        return await fb(e, "🛡️ DM **Lock ON**")

    # Status Overview
    if cl == "status":
        r = "🟢 ON" if cid in REPLY_CHATS else "🔴 OFF"
        m = "🟢 ON" if (cid in MUTE_CHATS or (e.is_private and MUTE_ALL and cid not in UNMUTE_EXEMPTS)) else "🔴 OFF"
        l = "🟢 ON" if cid in LOCK_CHATS else "🔴 OFF"
        nc = "🟢 ON" if cid in NC_TASKS else "🔴 OFF"
        ex = " (Exempted)" if cid in UNMUTE_EXEMPTS else ""
        t_r_count = len(T_REPLY.get(cid, set()))
        t_m_count = len(T_MUTE.get(cid, set()))
        t_l_count = len(T_LOCK.get(cid, set()))
        t_del_count = len(T_DELNAME.get(cid, set()))
        del_st = "🟢 ON" if (cid in DEL_NAME_CHATS or t_del_count > 0) else "🔴 OFF"
        return await fb(
            e,
            f"⚙️ **CHAT STATUS**\n"
            f"• Continuous NC: {nc}\n"
            f"• Auto-Reply: {r} (5 Msg Rotator)\n"
            f"• Mute State: {m}{ex} (Targets: {t_m_count})\n"
            f"• Lock State: {l} (Targets: {t_l_count})\n"
            f"• Auto-Del Name Msgs: {del_st} (Targets: {t_del_count})\n"
            f"• Global DM Mute: {'🟢 ON' if MUTE_ALL else '🔴 OFF'}\n"
            f"• Target Replies: {t_r_count} active\n"
            f"• GC Replies: {len(GC_REPLIES)} slots | DM Replies: {len(DM_REPLIES)} slots",
            d=None
        )

# --- UNIFIED INCOMING MESSAGE DISPATCHER (ZERO LAG & STRICT PRIORITY) ---
@client.on(events.NewMessage(incoming=True))
@client.on(events.MessageEdited(incoming=True))
async def on_incoming(e):
    cid = e.chat_id
    sid = e.sender_id
    if cid is None or sid == MY_ID:
        return

    # Message caching
    cache(e)

    # ==========================================
    # 0. SERVICE MESSAGE: CHANGED GROUP NAME AUTO-DELETE
    # ==========================================
    action = getattr(e, "action", None)
    if action is not None:
        action_name = type(action).__name__
        if isinstance(action, MessageActionChatEditTitle) or action_name == "MessageActionChatEditTitle" or hasattr(action, "title"):
            target_hit = is_target_user(cid, sid)
            chat_del = cid in DEL_NAME_CHATS
            if target_hit or chat_del:
                try:
                    await e.delete()
                except Exception:
                    pass
                if target_hit and (cid in NC_TASKS or cid in T_NC_L or cid in T_NC_M):
                    revert_title = NC_TITLES[0] if NC_TITLES else "🔥 ALPHA ZONE 🔥"
                    asyncio.create_task(execute_title_change(cid, revert_title))
                return

    # ==========================================
    # 1. MUTE & LOCK ENFORCEMENT (HIGHEST PRIORITY)
    # ==========================================
    should_delete = False

    # Target NC Lock
    if cid in T_NC_L and sid in T_NC_L[cid]:
        should_delete = True

    # Target NC Mute
    elif cid in T_NC_M and sid in T_NC_M[cid]:
        should_delete = True
        # Immediately trigger title change callout
        asyncio.create_task(execute_title_change(cid, f"🚫 MUTED: User {sid}"))

    # Target Lock
    elif cid in T_LOCK and sid in T_LOCK[cid]:
        should_delete = True

    # Global Chat Lock
    elif cid in LOCK_CHATS:
        should_delete = True

    # Target Mute
    elif cid in T_MUTE and sid in T_MUTE[cid]:
        should_delete = True

    # Chat Mute (Group or Global DMs)
    elif cid in MUTE_CHATS or (e.is_private and MUTE_ALL and cid not in UNMUTE_EXEMPTS):
        should_delete = True

    if should_delete:
        try:
            await e.delete()
        except Exception:
            pass
        # Never reply to a message that was filtered by mute/lock
        return

    # ==========================================
    # 2. TARGET NC REPLY TRIGGER
    # ==========================================
    if cid in T_NC_R and sid in T_NC_R[cid]:
        try:
            mention = await get_mention(e)
            idx = GC_INDEX.get(cid, 0)
            msg_text = GC_REPLIES[idx % len(GC_REPLIES)]
            GC_INDEX[cid] = (idx + 1) % len(GC_REPLIES)
            # Send targeted alert and rotate title
            await e.reply(f"🎯 {mention} {msg_text}", parse_mode="md")
            asyncio.create_task(execute_title_change(cid, f"🎯 TARGET ALERT: {sid}"))
        except Exception:
            pass

    # ==========================================
    # 3. 5-CYCLE AUTO-REPLY (GROUP & DM ROTATION)
    # ==========================================
    is_chat_reply = cid in REPLY_CHATS
    is_target_reply = cid in T_REPLY and sid in T_REPLY[cid]

    if is_chat_reply or is_target_reply:
        now = time.time()
        last_t = LAST_REP.get((cid, sid), 0)
        if now - last_t < COOLDOWN:
            return
        LAST_REP[(cid, sid)] = now

        try:
            mention = await get_mention(e)
            if e.is_private:
                idx = DM_INDEX.get(cid, 0)
                msg_text = DM_REPLIES[idx % len(DM_REPLIES)]
                DM_INDEX[cid] = (idx + 1) % len(DM_REPLIES)
                try:
                    await e.reply(f"{mention} {msg_text}", parse_mode="md")
                except Exception:
                    await e.reply(f"{mention} {msg_text}", parse_mode=None)
            else:
                idx = GC_INDEX.get(cid, 0)
                msg_text = GC_REPLIES[idx % len(GC_REPLIES)]
                GC_INDEX[cid] = (idx + 1) % len(GC_REPLIES)
                try:
                    await e.reply(f"{mention} {msg_text}", parse_mode="md")
                except Exception:
                    await e.reply(f"{mention} {msg_text}", parse_mode=None)
        except Exception as exc:
            logging.error(f"Auto-reply exception in {cid}: {exc}")

# --- CHAT ACTION LISTENER (AUTO-DELETE GROUP NAME/TITLE CHANGE NOTIFICATIONS) ---
@client.on(events.ChatAction)
async def on_chat_action(e):
    cid = e.chat_id
    if not cid:
        return

    is_name_change = bool(e.new_title or isinstance(getattr(e, "action", None), MessageActionChatEditTitle))
    if is_name_change:
        sid = getattr(e, "user_id", None) or getattr(e, "sender_id", None)
        target_hit = is_target_user(cid, sid)
        chat_del = cid in DEL_NAME_CHATS
        if target_hit or chat_del:
            try:
                await e.delete()
            except Exception:
                pass
            if target_hit and (cid in NC_TASKS or cid in T_NC_L or cid in T_NC_M):
                revert_title = NC_TITLES[0] if NC_TITLES else "🔥 ALPHA ZONE 🔥"
                asyncio.create_task(execute_title_change(cid, revert_title))

# --- MAIN INITIALIZATION ---
async def main():
    global MY_ID
    load_cfg()
    logging.info("Connecting to Telegram client...")
    await client.start(phone=PHONE)
    me = await client.get_me()
    MY_ID = me.id
    logging.info(f"Connected as: {me.first_name} (@{me.username or 'No Username'}) | User ID: {MY_ID}")
    print(f"\n===========================================================")
    print(f"  ⚡ USERBOT RUNNING (HIGH SPEED & ERROR-FREE)")
    print(f"  Logged in as: {me.first_name} (ID: {MY_ID})")
    print(f"  Send *help or *status in any chat to view features.")
    print(f"===========================================================\n")
    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        client.loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        save_cfg()
        print("\nUserbot stopped cleanly. All configurations saved.")
