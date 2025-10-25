import streamlit as st
import asyncio, time, re, zipfile, os, io, mimetypes
from pathlib import Path
from typing import Optional, List, Tuple, Dict
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ---------- UI ----------
st.set_page_config(page_title="Fadr Batch (no API)", page_icon="🎛️", layout="centered")
st.title("🎛️ Fadr Batch Stems (Website UI) — One-at-a-time")
st.caption("Uploads each track to fadr.com/stems, waits, downloads Instrumental + Vocals.")

with st.expander("Login", expanded=True):
    email = st.text_input("Fadr email", value="", placeholder="you@example.com")
    password = st.text_input("Fadr password", value="", type="password", placeholder="••••••••")
    headless = st.toggle("Run headless (unattended)", value=True,
                         help="Turn OFF the first time if you need to solve a CAPTCHA.")

uploads = st.file_uploader("Add audio files", type=["mp3","wav","flac","m4a","aac","aif","aiff","ogg"],
                           accept_multiple_files=True, help="Add multiple tracks; they’ll run sequentially.")

run_btn = st.button("Start batch", type="primary", disabled=not uploads or not email or not password)

# ---------- Helpers ----------
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".aif", ".aiff", ".ogg"}
STEMS_URL = "https://fadr.com/stems"

FILE_INPUT_SELECTORS = [
    "input[type='file']",
    "input[type='file'][multiple]",
    "[data-testid='dropzone'] input[type='file']",
    "form input[type='file']",
]

PAT_INSTR = re.compile(r"(instrumental)", re.I)
PAT_VOCAL = re.compile(r"(vocal|vocals)", re.I)
PAT_DLALL = re.compile(r"(download all|all stems|zip)", re.I)
PAT_ACCEPT = re.compile(r"(accept|agree|ok|got it)", re.I)

async def maybe_click(page, regex):
    try:
        btn = page.get_by_role("button", name=regex).first
        if await btn.count() > 0 and await btn.is_enabled():
            await btn.click()
            await page.wait_for_timeout(300)
    except:
        pass

async def ensure_logged_in(page, email: str, password: str):
    await page.goto("https://fadr.com", wait_until="domcontentloaded")
    await page.wait_for_timeout(800)
    await maybe_click(page, PAT_ACCEPT)

    html = (await page.content()).lower()
    if ("sign in" in html) or ("log in" in html):
        for url in ["https://fadr.com/login", "https://fadr.com/auth/login", "https://fadr.com/sign-in"]:
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(600)
                break
            except:
                pass

        await maybe_click(page, re.compile("email", re.I))

        candidates_email = [
            "input[autocomplete='email']","input[type='email']","input[name='email']",
            "input[placeholder*='Email' i]"
        ]
        candidates_pass = [
            "input[type='password']","input[name='password']",
            "input[placeholder*='Password' i]"
        ]
        email_box = None; pass_box = None
        for sel in candidates_email:
            if await page.locator(sel).count() > 0:
                email_box = page.locator(sel).first; break
        for sel in candidates_pass:
            if await page.locator(sel).count() > 0:
                pass_box = page.locator(sel).first; break

        if email_box and pass_box:
            await email_box.fill(email)
            await pass_box.fill(password)
            try:
                btn = page.get_by_role("button", name=re.compile("(log in|sign in|continue|submit)", re.I)).first
                if await btn.count() > 0: await btn.click()
                else: await page.keyboard.press("Enter")
            except:
                await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)

async def wait_for_results_ui(page, timeout_s=3600):
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            for role in ("button", "link"):
                if await page.get_by_role(role, name=PAT_INSTR).count() > 0: return True
                if await page.get_by_role(role, name=PAT_VOCAL).count() > 0: return True
                if await page.get_by_role(role, name=PAT_DLALL).count() > 0: return True
        except:
            pass
        await page.wait_for_timeout(1000)
    raise TimeoutError("Timed out waiting for stems results UI.")

async def click_download(page, role_regex, dest_stem: Path, timeout_s=1800):
    loc = None
    for role in ("button","link"):
        candidate = page.get_by_role(role, name=role_regex).first
        if await candidate.count() > 0:
            loc = candidate; break
    if not loc: return None
    try:
        async with page.expect_download(timeout=timeout_s*1000) as dl_info:
            await loc.click()
        dl = await dl_info.value
        suggested = dl.suggested_filename or ""
        ext = Path(suggested).suffix or ".mp3"
        dest = dest_stem.with_suffix(ext)
        dest.parent.mkdir(parents=True, exist_ok=True)
        await dl.save_as(str(dest))
        return dest
    except PWTimeout:
        return None

async def find_file_input(page):
    for sel in FILE_INPUT_SELECTORS:
        loc = page.locator(sel)
        if await loc.count() > 0:
            return loc.first
    return None

async def process_one_file(page, up_path: Path, out_dir: Path):
    base = up_path.stem
    await page.goto(STEMS_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(600)
    await maybe_click(page, PAT_ACCEPT)

    file_input = await find_file_input(page)
    if not file_input:
        try:
            maybe = page.get_by_role("button", name=re.compile("upload", re.I)).first
            if await maybe.count() > 0:
                await maybe.click(); await page.wait_for_timeout(400)
                file_input = await find_file_input(page)
        except:
            pass
    if not file_input:
        raise RuntimeError("Could not find a file upload input on the page.")

    await file_input.set_input_files(str(up_path))
    await wait_for_results_ui(page, timeout_s=3600)

    out_dir.mkdir(parents=True, exist_ok=True)
    inst_stem = out_dir / f"{base}.__tmp_instrumental"
    voc_stem  = out_dir / f"{base}.__tmp_vocals"

    inst_path = await click_download(page, PAT_INSTR, inst_stem)
    voc_path  = await click_download(page, PAT_VOCAL,  voc_stem)

    if not (inst_path and voc_path):
        zip_stem = out_dir / f"{base}.__tmp_all"
        zip_path = await click_download(page, PAT_DLALL, zip_stem)
        if zip_path and zip_path.exists() and zip_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(zip_path, "r") as zf:
                for n in zf.namelist():
                    ln = n.lower()
                    if ("instrumental" in ln) and not inst_path:
                        p = inst_stem.with_suffix(Path(n).suffix or ".mp3")
                        with zf.open(n) as src, open(p, "wb") as dst: dst.write(src.read())
                        inst_path = p
                    if ("vocal" in ln) and not voc_path:
                        p = voc_stem.with_suffix(Path(n).suffix or ".mp3")
                        with zf.open(n) as src, open(p, "wb") as dst: dst.write(src.read())
                        voc_path = p
            try: zip_path.unlink()
            except: pass

    def finalize(downloaded_path: Optional[Path], stem: str):
        if not downloaded_path or not downloaded_path.exists(): return None
        ext = downloaded_path.suffix or ".mp3"
        final = out_dir / f"{base} - {stem}{ext}"
        if final.exists(): final.unlink()
        downloaded_path.replace(final)
        return final

    return finalize(inst_path, "instrumental"), finalize(voc_path, "vocals")

async def run_batch(email: str, password: str, headless: bool, uploads):
    work = Path("work"); up_dir = work/"uploads"; out_dir = work/"out"
    up_dir.mkdir(parents=True, exist_ok=True); out_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for f in uploads:
        p = up_dir / f.name
        with open(p, "wb") as fh: fh.write(f.read())
        saved_files.append(p)

    results = {}
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(work/"profile"),
            headless=headless,
            slow_mo=120 if not headless else 0,
            accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled","--no-sandbox"],
        )
        page = await context.new_page()
        await ensure_logged_in(page, email, password)

        for i, src in enumerate(saved_files, 1):
            st.write(f"**{i}/{len(saved_files)} — {src.name}**")
            try:
                inst, voc = await process_one_file(page, src, out_dir)
                results[src.name] = {"instrumental": inst, "vocals": voc}
                if inst: st.success(f"Instrumental: {inst.name}")
                else:    st.warning("Instrumental not found")
                if voc:  st.success(f"Vocals: {voc.name}")
                else:    st.warning("Vocals not found")
            except Exception as e:
                st.error(f"Error: {e}")

        await context.close()
    return results

def zip_results(result_map):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for _, stems in result_map.items():
            for k in ("instrumental","vocals"):
                p = stems.get(k)
                if p and p.exists():
                    zf.write(p, arcname=p.name)
    return buf.getvalue()

# ---------- Start ----------
if run_btn:
    with st.spinner("Running batch…"):
        result_map = asyncio.run(run_batch(email, password, headless, uploads))

    st.divider()
    st.subheader("Downloads")

    for track, stems in result_map.items():
        cols = st.columns(3)
        cols[0].markdown(f"**{track}**")
        for k in ("instrumental","vocals"):
            p = stems.get(k)
            if p and p.exists():
                with open(p, "rb") as fh:
                    cols[1 if k=='instrumental' else 2].download_button(
                        label=f"Download {k}",
                        data=fh.read(),
                        file_name=p.name,
                        mime=mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                    )
            else:
                cols[1 if k=='instrumental' else 2].write("—")

    blob = zip_results(result_map)
    if blob:
        st.download_button("⬇️ Download ALL (zip)", data=blob, file_name="fadr_stems.zip", mime="application/zip")
