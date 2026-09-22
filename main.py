import asyncio
import os
import shutil
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

app = FastAPI(title="خطفة - Backend")

# السماح لتطبيق أندرويد بالاتصال بالخادم من أي مكان
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

SECRETS_DIR = "/etc/secrets"
WRITABLE_DIR = "/tmp/cookies"
os.makedirs(WRITABLE_DIR, exist_ok=True)

YOUTUBE_COOKIES = os.path.join(WRITABLE_DIR, "youtube_cookies.txt")
TIKTOK_COOKIES = os.path.join(WRITABLE_DIR, "tiktok_cookies.txt")

for fname in ("youtube_cookies.txt", "tiktok_cookies.txt"):
    src = os.path.join(SECRETS_DIR, fname)
    dst = os.path.join(WRITABLE_DIR, fname)
    if os.path.exists(src):
        shutil.copy(src, dst)


def get_cookiefile(url: str):
    """يختار ملف الكوكيز المناسب حسب المنصة، أو لا شيء إن لم تكن مدعومة"""
    lowered = url.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        if os.path.exists(YOUTUBE_COOKIES):
            return YOUTUBE_COOKIES
    elif "tiktok.com" in lowered:
        if os.path.exists(TIKTOK_COOKIES):
            return TIKTOK_COOKIES
    return None


def get_extractor_args(url: str):
    """يفرض عميل أندرويد ليوتيوب لتفادي حماية 'confirm not a bot'"""
    if "youtube.com" in url.lower() or "youtu.be" in url.lower():
        return {"youtube": {"player_client": ["tv"]}}
    return {}


@app.get("/")
def root():
    """نقطة تأكيد أن الخادم يعمل"""
    return {"status": "ok", "service": "khatfa-backend"}


@app.get("/info")
def get_info(url: str = Query(..., description="رابط الفيديو المراد جلب معلوماته")):
    """
    يستقبل رابط فيديو (يوتيوب/تيك توك/انستغرام/فيسبوك/تويتر)
    ويرجع معلوماته وقائمة الجودات المتاحة للتحميل.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "http_headers": {
            "User-Agent": USER_AGENT,
        },
        "extractor_args": get_extractor_args(url),
    }

    cookiefile = get_cookiefile(url)
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"تعذر جلب معلومات الفيديو: {e}")

    formats = []
    for f in info.get("formats", []):
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")

        # "none" الصريحة تعني غياب المسار فعلاً، أما القيمة المجهولة (None)
        # فتعني أن yt-dlp لم يحدد الأمر بدقة، ونفترض حينها أنه على الأرجح موجود
        has_video = vcodec != "none"
        has_audio = acodec != "none"

        # المرحلة الأولى: نستبعد فقط الصيغ المؤكد أنها فيديو بدون صوت
        # (تحتاج دمجًا مع ffmpeg، وهذا خارج نطاق هذه المرحلة)
        video_only_confirmed = (vcodec not in (None, "none")) and acodec == "none"
        if video_only_confirmed:
            continue

        label = f.get("format_note") or f.get("resolution")
        if not label:
            label = "صوت فقط" if not has_video else f.get("format_id", "جودة قياسية")

        formats.append(
            {
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "resolution": label,
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "has_video": has_video,
                "has_audio": has_audio,
            }
        )

    return {
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "platform": info.get("extractor_key"),
        "formats": formats,
    }


@app.get("/download")
async def download(
    url: str = Query(..., description="رابط الفيديو"),
    format_id: str = Query("best", description="معرف الجودة من /info"),
):
    """
    يقوم بتحميل الفيديو فعليًا من المصدر وبثّه مباشرة لطالب الطلب
    (لا يُخزَّن أي ملف على الخادم).
    """
    cmd = [
        "yt-dlp",
        "-f", format_id,
        "-o", "-",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "--user-agent", USER_AGENT,
    ]

    if "youtube.com" in url.lower() or "youtu.be" in url.lower():
        cmd += ["--extractor-args", "youtube:player_client=tv"]

    cookiefile = get_cookiefile(url)
    if cookiefile:
        cmd += ["--cookies", cookiefile]

    cmd.append(url)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def stream_generator():
        try:
            while True:
                chunk = await process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            await process.wait()

    return StreamingResponse(stream_generator(), media_type="video/mp4")
